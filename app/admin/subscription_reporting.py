"""Read-only admin summaries; entitlement and billing rules remain authoritative."""
from datetime import datetime
from sqlalchemy import func, or_
from app.models import Business, Payment, RecurringSubscription, User
from app.subscriptions.entitlements import PLANS, effective_access


def current_subscription_accounts(customers, now=None):
    """Count each eligible customer once, using the effective paid-period winner."""
    now = now or datetime.utcnow()
    counts = {plan: {interval: 0 for interval in ('monthly', 'yearly')}
              for plan in ('basic', 'plus', 'pro')}
    for user in customers:
        access = effective_access(user, now=now)
        sub = access.subscription
        if access.can_write and sub and (sub.plan_code, sub.billing_interval) in PLANS:
            counts[sub.plan_code][sub.billing_interval] += 1
    return {'total': sum(sum(intervals.values()) for intervals in counts.values()),
            'by_plan': counts}


def subscription_payments_query():
    # Keep the listing's existing ownership scope for HTML and opt-in API alike.
    return Payment.query.join(User, Payment.user_id == User.id).filter(
        Payment.product == 'subscription', User.role == 'user', User.admin_enabled.is_(False),
        or_(Payment.business_id.is_(None),
            Payment.business.has(Business.user_id == Payment.user_id)))


def verified_subscription_payments_query():
    # Count durable verified ledger rows, never current prices or email guesses.
    return subscription_payments_query().join(
        RecurringSubscription, Payment.subscription_id == RecurringSubscription.id
    ).filter(
        Payment.status == 'success', Payment.provider == 'paystack', Payment.currency == 'NGN',
        Payment.paid_at.isnot(None), Payment.amount_kobo > 0,
        Payment.user_id == RecurringSubscription.user_id,
        Payment.business_id == RecurringSubscription.business_id,
        Payment.plan_code == RecurringSubscription.plan_code,
        Payment.billing_interval == RecurringSubscription.billing_interval,
        Payment.amount_kobo == RecurringSubscription.amount_kobo,
        or_(*( (Payment.plan_code == plan) & (Payment.billing_interval == interval)
               for plan, interval in PLANS)),
    )


def collected_subscription_payments(start=None, end=None):
    query = verified_subscription_payments_query()
    if start is not None:
        query = query.filter(Payment.paid_at >= start)
    if end is not None:
        query = query.filter(Payment.paid_at < end)
    count, revenue = query.with_entities(
        func.count(Payment.id), func.coalesce(func.sum(Payment.amount_kobo), 0)).one()
    return {'count': count, 'revenue_kobo': revenue}
