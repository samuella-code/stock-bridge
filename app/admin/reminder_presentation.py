"""Read-only recipient projection. Sending continues to use service.candidate."""
from app.models import AccountBilling, Business, RecurringSubscription, User
from app.admin import reminder_service as service
from app.admin.customer_data import local_time, masked_email

EXCLUSIONS = {
    'stopped': ('Opted out', 'This owner stopped reminder emails. A frequency override cannot restore consent.'),
    'unavailable': ('Account unavailable', 'The account is suspended, unverified, has an invalid email, or its business is unavailable.'),
    'existing_access': ('Access already covered', 'This account already has access that is not ending.'),
    'no_trial': ('No trial', 'This account has no trial to remind them about.'),
    'plan_chosen': ('Plan chosen', 'Trial reminders stop when the owner chooses a plan.'),
    'outside_window': ('Outside reminder window', 'This account is outside the current reminder audience window.'),
    'no_paid_period': ('No paid access period', 'There is no paid access period for a win-back message.'),
    'renewal_on': ('Renewal is on', 'Win-back messages stop when renewal is on.'),
    'paid_again': ('Paid or renewed', 'This owner has paid again or has a later paid period.'),
    'no_business': ('No available business', 'There is no available business for this reminder.'),
    'not_owner': ('Not an account owner', 'Employees do not receive plan reminders.'),
}


def recipient_page(kind, now, eligible, *, page=1, target=None):
    """Eligible owners first, followed by a paged, non-selectable explanation list."""
    query = User.query.filter_by(role='user', admin_enabled=False, staff_only=False)
    if target is not None:
        query = query.filter(User.id == target)
    ids = [facts['user'].id for facts in eligible]
    if ids:
        query = query.filter(~User.id.in_(ids))
    total = len(eligible) + query.count()
    offset = (page - 1) * 25
    if offset >= total:
        return [], total
    facts_page = eligible[offset:offset + 25]
    excluded = query.order_by(User.id).offset(max(0, offset - len(eligible))).limit(25 - len(facts_page)).all() if len(facts_page) < 25 else []
    excluded_ids = [user.id for user in excluded]
    businesses = {}
    business_by_id = {}
    accounts = {}
    subscriptions = {}
    if excluded_ids:
        for shop in Business.query.filter(Business.user_id.in_(excluded_ids)).order_by(Business.id).all():
            businesses.setdefault(shop.user_id, shop)
            business_by_id[shop.id] = shop
        accounts = {row.user_id: row for row in AccountBilling.query.filter(AccountBilling.user_id.in_(excluded_ids)).all()}
        for sub in RecurringSubscription.query.filter(RecurringSubscription.user_id.in_(excluded_ids), RecurringSubscription.created_at <= now).order_by(RecurringSubscription.current_period_end.desc(), RecurringSubscription.id.desc()).all():
            subscriptions.setdefault(sub.user_id, sub)
    rows = []
    for facts in facts_page:
        user = facts['user']
        _, reason = service.candidate(user, kind, now)
        blocked = EXCLUSIONS.get(reason, ('Not eligible', 'Refresh the audience before sending.')) if reason and reason != 'recent_contact' else (None, None)
        rows.append(dict(id=user.id, name=user.full_name, email=masked_email(user.email),
            business=facts['business'].name, end=local_time(facts['end']),
            recent=reason == 'recent_contact', blocked_reason=blocked[0], exclusion_hint=blocked[1]))
    for user in excluded:
        # The canonical candidate check supplies the reason; this list never grants eligibility.
        _, reason = service.candidate(user, kind, now, override=True)
        label, hint = EXCLUSIONS.get(reason, ('Not eligible', 'Refresh the audience before sending.'))
        account = accounts.get(user.id)
        sub = subscriptions.get(user.id)
        preferred_id = (account.primary_business_id if account else None) if kind == 'trial' else (sub.business_id if sub else None)
        shop = business_by_id.get(preferred_id)
        if not shop or shop.user_id != user.id:
            shop = businesses.get(user.id)
        end = (account.trial_ends_at if account else None) if kind == 'trial' else (sub.current_period_end if sub else None)
        rows.append(dict(id=user.id, name=user.full_name, email=masked_email(user.email),
            business=shop.name if shop else 'No business',
            end=local_time(end) or '—', recent=False, blocked_reason=label, exclusion_hint=hint))
    return rows, total
