"""Account-owned entitlements; no provider calls, deletions or browser dates."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from flask import current_app, has_request_context, session
from sqlalchemy import update, func, cast, String, literal
from app import db
from app.models import AccountBilling, Business, RecurringSubscription, BillingEvent

PLANS = {
    ('basic','monthly'): {'amount':300000,'limit':1,'interval':'monthly'},
    ('basic','yearly'): {'amount':3000000,'limit':1,'interval':'annually'},
    ('plus','monthly'): {'amount':500000,'limit':2,'interval':'monthly'},
    ('plus','yearly'): {'amount':5000000,'limit':2,'interval':'annually'},
    ('pro','monthly'): {'amount':1000000,'limit':5,'interval':'monthly'},
    ('pro','yearly'): {'amount':10000000,'limit':5,'interval':'annually'},
}


def plan_spec(plan, interval):
    if (plan, interval) not in PLANS:
        raise ValueError('Choose Basic, Plus or Pro with monthly or yearly billing.')
    return PLANS[(plan, interval)]


def account_for(user, *, create=False):
    account = db.session.get(AccountBilling, user.id)
    if not account and create:
        account = AccountBilling(user_id=user.id, trial_eligible=False,
            primary_business_id=user.businesses[0].id if user.businesses else None)
        db.session.add(account)
        db.session.flush()
    return account


def start_trial(user):
    account = account_for(user)
    if (not account or not account.trial_eligible or account.legacy_granted_at
            or not user.email_verified_at or user.role != 'user' or user.admin_enabled):
        return False
    now = datetime.utcnow()
    changed = db.session.execute(update(AccountBilling).where(AccountBilling.user_id == user.id,
        AccountBilling.trial_started_at.is_(None)).values(trial_started_at=now, trial_ends_at=now+timedelta(days=7)))
    if changed.rowcount:
        db.session.add(BillingEvent(event_key=f'trial:{user.id}', user_id=user.id, kind='trial_started',
            message='Your 7-day free trial has started. No card is required.'))
        return True
    return False


@dataclass(frozen=True)
class Access:
    kind: str
    can_write: bool
    business_limit: int
    trial_days_remaining: int = 0
    subscription: object = None
    legacy: bool = False


CURRENT_STATUSES = ('active', 'past_due', 'cancel_at_period_end', 'cancelled')
ENTITLEMENT_BATCH_SIZE = 200


def current_subscriptions(now):
    return RecurringSubscription.query.filter(
        RecurringSubscription.current_period_start <= now,
        RecurringSubscription.current_period_end > now,
        RecurringSubscription.status.in_(CURRENT_STATUSES))


def started_multi_business_subscriptions():
    # Selection generation deliberately includes expired/failed/future-started rows.
    return RecurringSubscription.query.filter(
        RecurringSubscription.plan_code.in_(('plus', 'pro')),
        RecurringSubscription.current_period_start.isnot(None))


def selection_event_key(user_id, previous_id):
    return f'business-selection:{user_id}:{previous_id or 0}'


def needs_business_selection(enabled, limit, count, selected):
    return bool(enabled and limit == 1 and count > 1 and not selected)


def _account_access(user, account, subscription, now):
    """The single authoritative account evaluator, shared by scalar and bulk."""
    legacy = bool(account and account.legacy_granted_at and account.legacy_payment_id)
    kind, limit, days = 'restricted', 0, 0
    if subscription:
        kind = 'legacy_lifetime_' + subscription.plan_code if legacy and subscription.plan_code in ('plus', 'pro') else subscription.plan_code
        limit = plan_spec(subscription.plan_code, subscription.billing_interval)['limit']
    elif legacy:
        kind, limit = 'legacy_lifetime', 1
    elif account and account.trial_started_at and account.trial_ends_at and account.trial_started_at <= now < account.trial_ends_at:
        kind, limit = 'trial', 1
        days = max(0, int(((account.trial_ends_at-now).total_seconds()+86399)//86400))
    return Access(kind, bool(limit and user.email_verified_at and not user.suspended_at
        and user.role=='user' and (not user.admin_enabled or legacy)), limit, days, subscription, legacy)


def _business_access(access, user, account, business, facts):
    """Apply the same ownership, primary, selection and slot rules to either loader."""
    if business is None:
        return access
    primary = account.primary_business_id if account else None
    if primary and not facts.owns(primary):
        primary = None
    allowed = business.user_id == user.id and not business.suspended_at
    if access.business_limit == 1:
        allowed = allowed and not facts.selection_required(access) and (
            business.id == primary or (primary is None and facts.count() == 1))
    elif access.business_limit > 1:
        allowed = allowed and business.id in facts.slots(access.business_limit)
    return replace(access, can_write=bool(access.can_write and allowed))


class _ScalarFacts:
    def __init__(self, user):
        self.user = user

    def owns(self, business_id):
        return Business.query.filter_by(id=business_id, user_id=self.user.id).first() is not None

    def count(self):
        return Business.query.filter_by(user_id=self.user.id).count()

    def slots(self, limit):
        return [r[0] for r in db.session.query(Business.id).filter_by(
            user_id=self.user.id).order_by(Business.id).limit(limit)]

    def selection_required(self, access):
        from app.businesses.service import selection_required
        return selection_required(self.user, access)


def effective_access(user, *, business=None, now=None):
    now = now or datetime.utcnow()
    account = account_for(user)
    subscription = current_subscriptions(now).filter(
        RecurringSubscription.user_id == user.id).order_by(
            RecurringSubscription.current_period_start.desc(), RecurringSubscription.id.desc()).first()
    access = _account_access(user, account, subscription, now)
    return _business_access(access, user, account, business, _ScalarFacts(user))


class _BulkFacts:
    def __init__(self, ids, selected, enabled):
        self.ids, self.selected, self.enabled = ids, selected, enabled

    def owns(self, business_id):
        return business_id in self.ids

    def count(self):
        return len(self.ids)

    def slots(self, limit):
        return self.ids[:limit]

    def selection_required(self, access):
        return needs_business_selection(self.enabled, access.business_limit, len(self.ids), self.selected)


class EntitlementSnapshot:
    """Operation-local snapshot. Callers must discard it after their read operation.

    Four SELECTs per batch of at most 200 distinct user IDs. Never stored in a
    process/global cache. No historical subscription/event collections are loaded.
    """
    def __init__(self, users, *, now=None):
        self.now = now or datetime.utcnow()
        self.users = {u.id: u for u in users}
        self.accounts, self.subscriptions, self.facts, self.results = {}, {}, {}, {}
        enabled = current_app.config.get('SUBSCRIPTIONS_ENABLED')
        keys = sorted(self.users)
        for offset in range(0, len(keys), ENTITLEMENT_BATCH_SIZE):
            ids = keys[offset:offset + ENTITLEMENT_BATCH_SIZE]
            accounts = AccountBilling.query.filter(AccountBilling.user_id.in_(ids)).all()
            self.accounts.update((a.user_id, a) for a in accounts)
            ranked = current_subscriptions(self.now).filter(RecurringSubscription.user_id.in_(ids)).with_entities(
                RecurringSubscription.id.label('id'), func.row_number().over(
                    partition_by=RecurringSubscription.user_id,
                    order_by=(RecurringSubscription.current_period_start.desc(),
                              RecurringSubscription.id.desc())).label('position')).subquery()
            winners = RecurringSubscription.query.join(ranked, ranked.c.id == RecurringSubscription.id).filter(
                ranked.c.position == 1).all()
            self.subscriptions.update((r.user_id, r) for r in winners)
            owned = {uid: [] for uid in ids}
            for uid, bid in db.session.query(Business.user_id, Business.id).filter(
                    Business.user_id.in_(ids)).order_by(Business.id):
                owned[uid].append(bid)
            # Generation is the highest started Plus/Pro ID, regardless of status
            # or date. This intentionally matches selection_key, not period order.
            generations = started_multi_business_subscriptions().with_entities(
                RecurringSubscription.user_id.label('uid'),
                func.max(RecurringSubscription.id).label('latest')).filter(
                    RecurringSubscription.user_id.in_(ids)).group_by(
                        RecurringSubscription.user_id).subquery()
            # One exact-key event read with the set-based generation subquery; neither
            # loads historical objects or builds an unbounded list of event keys.
            event_key = (literal('business-selection:') + cast(BillingEvent.user_id, String)
                         + literal(':') + cast(func.coalesce(generations.c.latest, 0), String))
            selected = {r[0] for r in db.session.query(BillingEvent.user_id).outerjoin(
                generations, generations.c.uid == BillingEvent.user_id).filter(
                    BillingEvent.user_id.in_(ids), BillingEvent.event_key == event_key)}
            for uid in ids:
                self.facts[uid] = _BulkFacts(tuple(owned[uid]), uid in selected, enabled)
        for uid, user in self.users.items():
            self.results[uid] = _account_access(user, self.accounts.get(uid),
                self.subscriptions.get(uid), self.now)

    def access(self, user, *, business=None):
        if self.users.get(user.id) is not user:
            raise ValueError('User is outside this entitlement snapshot.')
        return _business_access(self.results[user.id], user, self.accounts.get(user.id),
                                business, self.facts[user.id])


def access_label(user):
    labels={'trial':'Free Trial','basic':'StockBridge Basic','plus':'StockBridge Plus','pro':'StockBridge Pro',
        'legacy_lifetime':'Existing account access','legacy_lifetime_plus':'Existing account access + Plus','legacy_lifetime_pro':'Existing account access + Pro',
        'restricted':'Read-only — choose a plan'}
    access=effective_access(user)
    label=labels[access.kind]
    if access.subscription:
        label+=' · '+access.subscription.billing_interval.title()
        if access.subscription.status=='past_due':label+=' · renewal failed'
        elif access.subscription.cancel_at_period_end:label+=' · renewal stopped'
    elif access.kind=='restricted':
        account=account_for(user)
        if account and account.trial_ends_at:label='Trial expired · read-only'
        last=RecurringSubscription.query.filter_by(user_id=user.id).order_by(RecurringSubscription.id.desc()).first()
        if last and last.current_period_end:label='Subscription expired · read-only'
        elif last:label='Subscription pending · read-only'
    return label


def selected_business(user):
    """Resolve a session context only after ownership and entitlement checks."""
    from app.businesses.service import owned_businesses, accessible_business
    businesses = owned_businesses(user)
    if not businesses:
        return None
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        return businesses[0]
    active_id = session.get('active_business_id') if has_request_context() else None
    active = next((b for b in businesses if b.id == active_id), None)
    if accessible_business(user, active):
        return active
    account = account_for(user)
    primary = next((b for b in businesses if account and b.id == account.primary_business_id), None)
    fallback = next((b for b in [primary] + businesses if accessible_business(user, b)), None)
    if fallback and has_request_context():
        session['active_business_id'] = fallback.id
    # Billing remains reachable while a downgrade choice is required. The
    # request guard blocks ALL business screens until that choice is made.
    return fallback or primary or businesses[0]


def immediate_upgrade(old_plan, new_plan, old_interval, new_interval):
    ranks = {'basic': 1, 'plus': 2, 'pro': 3}
    return (old_interval == new_interval and old_plan in ranks and new_plan in ranks
            and ranks[new_plan] > ranks[old_plan])
