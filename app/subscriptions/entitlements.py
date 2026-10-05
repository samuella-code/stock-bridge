"""Account-owned entitlements; no provider calls, deletions or browser dates."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from flask import current_app, has_request_context, session
from sqlalchemy import update
from app import db
from app.models import AccountBilling, Business, RecurringSubscription, BillingEvent

PLANS = {
    ('basic','monthly'): {'amount':300000,'limit':1,'interval':'monthly'},
    ('basic','yearly'): {'amount':3000000,'limit':1,'interval':'annually'},
    ('plus','monthly'): {'amount':500000,'limit':2,'interval':'monthly'},
    ('plus','yearly'): {'amount':5000000,'limit':2,'interval':'annually'},
}


def plan_spec(plan, interval):
    if (plan, interval) not in PLANS:
        raise ValueError('Choose Basic or Plus with monthly or yearly billing.')
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


def effective_access(user, *, business=None, now=None):
    now = now or datetime.utcnow()
    account = account_for(user)
    legacy = bool(account and account.legacy_granted_at and account.legacy_payment_id)
    subscription = RecurringSubscription.query.filter(RecurringSubscription.user_id==user.id,
        RecurringSubscription.current_period_start<=now, RecurringSubscription.current_period_end>now,
        RecurringSubscription.status.in_(('active','past_due','cancel_at_period_end','cancelled'))
    ).order_by(RecurringSubscription.current_period_start.desc(), RecurringSubscription.id.desc()).first()
    kind, limit, days = 'restricted', 0, 0
    if subscription:
        kind = 'legacy_lifetime_plus' if legacy and subscription.plan_code=='plus' else subscription.plan_code
        limit = plan_spec(subscription.plan_code, subscription.billing_interval)['limit']
    elif legacy:
        kind, limit = 'legacy_lifetime', 1
    elif account and account.trial_started_at and account.trial_ends_at and account.trial_started_at <= now < account.trial_ends_at:
        kind, limit = 'trial', 1
        days = max(0, int(((account.trial_ends_at-now).total_seconds()+86399)//86400))
    primary = account.primary_business_id if account else None
    if primary and not Business.query.filter_by(id=primary, user_id=user.id).first():
        primary = None
    allowed = True
    if business is not None:
        allowed = business.user_id == user.id and not business.suspended_at
        if limit == 1:
            from app.businesses.service import selection_required
            allowed = allowed and not selection_required(user, Access(kind, True, limit)) and (business.id == primary or (primary is None and Business.query.filter_by(user_id=user.id).count() == 1))
        elif limit > 1:
            # The switcher and entitlement checks share the same owned slots.
            ids = [r[0] for r in db.session.query(Business.id).filter_by(user_id=user.id).order_by(Business.id).limit(limit)]
            allowed = allowed and business.id in ids
    return Access(kind, bool(limit and allowed and user.email_verified_at and not user.suspended_at
        and user.role=='user' and (not user.admin_enabled or legacy)), limit, days, subscription, legacy)


def access_label(user):
    labels={'trial':'Free Trial','basic':'StockBridge Basic','plus':'StockBridge Plus',
        'legacy_lifetime':'Legacy Lifetime Access','legacy_lifetime_plus':'Legacy Lifetime Access + Plus',
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
