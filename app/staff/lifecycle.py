"""Read-only Pro continuity from verified payment-period receipts, never providers.

An explicit owner grant anchors known current access. Only successful payments
with their immutable original-period receipts can extend that anchor. Delivery
may be delayed; paid_at must still be on/before the existing coverage boundary.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from app import db
from app.models import Payment, BillingEvent, RecurringSubscription

BATCH_SIZE = 200


def event_watermark(owner_id):
    return db.session.query(db.func.coalesce(db.func.max(BillingEvent.id), 0)).filter(BillingEvent.user_id == owner_id).scalar()


def utc(value):
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def coverage(owner_id, grant, *, now):
    edge = grant.access_until
    baseline = (grant.grant_pro_start, grant.grant_subscription_id)
    periods = []
    last = 0
    while True:
        rows = db.session.query(Payment, RecurringSubscription).join(
            RecurringSubscription, RecurringSubscription.id == Payment.subscription_id).filter(
                Payment.user_id == owner_id, RecurringSubscription.user_id == owner_id,
                Payment.id > last, Payment.product == 'subscription', Payment.status == 'success',
                Payment.provider == 'paystack', Payment.currency == 'NGN',
                Payment.paid_at.isnot(None), Payment.paid_at <= now).order_by(Payment.id).limit(BATCH_SIZE).all()
        if not rows: break
        keys = {'payment-period:'+hashlib.sha256(p.reference.encode()).hexdigest(): (p, sub) for p, sub in rows}
        receipts = {r.event_key:r for r in BillingEvent.query.filter(
            BillingEvent.user_id == owner_id, BillingEvent.kind == 'provider_event',
            BillingEvent.event_key.in_(keys)).all()}
        for key, (payment, sub) in keys.items():
            receipt = receipts.get(key)
            valid = bool(receipt and receipt.subscription_id == sub.id and receipt.created_at <= now
                         and payment.business_id == sub.business_id and payment.amount_kobo == sub.amount_kobo
                         and payment.plan_code in ('basic','plus','pro')
                         and payment.billing_interval in ('monthly','yearly'))
            try:
                values = json.loads(receipt.message) if valid else None
                if not isinstance(values, list) or len(values) != 2: raise ValueError
                start, end = (utc(value) for value in values)
                if not start < end or end <= payment.paid_at or end-start > timedelta(days=32 if payment.billing_interval=='monthly' else 366):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                # Unknown recent non-Pro success cannot prove continuous Pro.
                if payment.plan_code != 'pro' and (payment.paid_at >= grant.grant_started_at or
                        (sub.current_period_start and sub.current_period_end and
                         (sub.current_period_start, sub.id) > baseline and sub.current_period_end > grant.grant_started_at)):
                    return None
                continue
            if payment.plan_code != 'pro':
                if start <= now and (start, sub.id) > baseline and (
                        start > grant.grant_started_at or
                        (end > grant.grant_started_at and receipt.id > grant.grant_event_id)):
                    return None
            else:
                periods.append((start, end, payment.paid_at))
        last = rows[-1][0].id
    # Reachability over original periods handles out-of-order callbacks. Repeated periods
    # do not add time. A backdated period paid AFTER the boundary cannot bridge it.
    while True:
        reachable = max([edge] + [end for start, end, paid_at in periods
                                 if start <= edge and paid_at <= edge and end > edge])
        if reachable == edge: return edge
        edge = reachable
