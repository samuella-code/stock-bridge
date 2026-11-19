"""Read-only payment projections. Never verify payments or change access."""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from app import db
from app.admin.customer_data import local_time, masked_email
from app.admin.dashboard import owner_query
from app.admin.subscription_reporting import subscription_payments_query, collected_subscription_payments
from app.models import Business, Payment, User
from app.subscriptions.entitlements import EntitlementSnapshot

FILTERS = ('all', 'success', 'pending', 'failed', 'needs-attention')


def filtered(q='', status='all'):
    query = subscription_payments_query().filter(User.staff_only.is_(False))
    if q:
        query = query.filter(or_(Payment.reference.icontains(q, autoescape=True),
            Payment.customer_email.icontains(q, autoescape=True), User.email.icontains(q, autoescape=True),
            User.full_name.icontains(q, autoescape=True),
            Payment.business.has(Business.name.icontains(q, autoescape=True))))
    if status == 'needs-attention':
        query = query.filter(Payment.status.in_(('initialized', 'pending', 'failed')))
    elif status == 'pending':
        query = query.filter(Payment.status.in_(('initialized', 'pending')))
    elif status in ('success', 'failed'):
        query = query.filter(Payment.status == status)
    return query.options(joinedload(Payment.business)).order_by(Payment.created_at.desc(), Payment.id.desc())


def money(kobo, currency='NGN'):
    amount = Decimal(kobo) / 100
    places = 2 if amount != amount.to_integral_value() else 0
    return ('₦' if currency == 'NGN' else currency + ' ') + format(amount, f',.{places}f')


def serialize(payments):
    payments = list(payments)
    owners = {u.id: u for u in User.query.filter(User.id.in_({p.user_id for p in payments}))}
    return [dict(id=p.id, owner_id=p.user_id, customer=owners[p.user_id].full_name,
        email=masked_email(owners[p.user_id].email), business=p.business.name if p.business else 'Unlinked',
        business_id=p.business_id, plan=p.plan_code.title() if p.plan_code else 'Not recorded',
        interval=p.billing_interval.title() if p.billing_interval else 'Interval not recorded',
        amount=money(p.amount_kobo, p.currency), status='Pending' if p.status == 'initialized' else p.status.title(),
        can_check=p.provider == 'paystack' and p.status in ('initialized', 'pending', 'failed'),
        created=local_time(p.created_at), reference=p.reference,
        short_reference=p.reference if len(p.reference) <= 20 else p.reference[:10] + '…' + p.reference[-6:])
        for p in payments]


def summary(now):
    result = {'collected': collected_subscription_payments()}
    for label, statuses in [('pending', ('initialized', 'pending')), ('failed', ('failed',))]:
        count, value = subscription_payments_query().filter(User.staff_only.is_(False),
            Payment.status.in_(statuses), Payment.currency == 'NGN', Payment.provider == 'paystack',
            Payment.amount_kobo > 0).with_entities(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_kobo), 0)).one()
        result[label] = dict(count=count, revenue_kobo=value)
    renewals = dict(count=0, revenue_kobo=0)
    last_id = 0
    while True:
        owners = owner_query().filter(User.id > last_id).order_by(User.id).limit(100).all()
        if not owners:
            break
        snapshot = EntitlementSnapshot(owners, now=now)
        owned = dict(db.session.query(Business.id, Business.user_id).filter(Business.user_id.in_([u.id for u in owners])))
        for owner in owners:
            access = snapshot.access(owner)
            sub = access.subscription
            due = (sub.next_renewal_at or sub.current_period_end) if sub else None
            if (access.can_write and sub and sub.status == 'active' and not sub.cancel_at_period_end
                    and not sub.cancelled_at and sub.amount_kobo > 0 and owned.get(sub.business_id) == owner.id
                    and due and now <= due < now + timedelta(days=30)):
                renewals['count'] += 1
                renewals['revenue_kobo'] += sub.amount_kobo
        last_id = owners[-1].id
    result['renewals'] = renewals
    return result
