"""Audited provider lookups only. Never reconcile payments or grant access."""
import json
from datetime import datetime, timedelta

from flask import abort, jsonify, session
from flask_login import current_user

from app import db
from app.admin.routes import admin_bp, admin_required, log
from app.admin.subscription_reporting import subscription_payments_query
from app.error_handling import log_failure
from app.models import AuditLog, Payment, RecurringSubscription, User
from app.subscriptions import provider

REQUESTED = 'ADMIN_PAYMENT_CHECK_REQUESTED'
RESULT = 'ADMIN_PAYMENT_CHECK_RESULT'
STATUSES = {'success': 'Success', 'failed': 'Failed', 'pending': 'Pending',
            'abandoned': 'Abandoned', 'ongoing': 'Ongoing', 'processing': 'Processing',
            'queued': 'Queued', 'reversed': 'Reversed'}


def comparison(data, expected):
    """Allowlisted outcome; provider text and nested records never leave here."""
    if (not isinstance(data, dict) or not isinstance(data.get('status'), str)
            or data['status'] not in STATUSES):
        return dict(outcome='unavailable', provider_status='Unavailable', matches=False)
    customer = data.get('customer')
    matches = bool(isinstance(customer, dict)
        and isinstance(customer.get('email'), str)
        and customer['email'].lower() == expected['email'].lower()
        and expected['customer_code'] and customer.get('customer_code') == expected['customer_code']
        and data.get('reference') == expected['reference']
        and type(data.get('amount')) is int and data['amount'] == expected['amount']
        and data.get('currency') == expected['currency'] and data.get('domain') == expected['mode'])
    if expected['checkout']:
        metadata = data.get('metadata')
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (ValueError, TypeError):
                metadata = None
        matches = matches and isinstance(metadata, dict) and all(
            type(metadata.get(key)) is type(value) and metadata.get(key) == value
            for key, value in expected['metadata'].items())
    return dict(outcome='matched' if matches else 'needs_review',
                provider_status=STATUSES[data['status']], matches=bool(matches))


@admin_bp.post('/payments/<int:payment_id>/check')
@admin_required
def check_payment(payment_id):
    # Lock the actor and target briefly so concurrent requests cannot bypass the
    # audit-backed limits. Commit the claim before making the provider GET.
    actor = User.query.filter_by(id=current_user.id).populate_existing().with_for_update().one()
    if (actor.suspended_at or not (actor.role == 'admin' or actor.admin_enabled)
            or actor.admin_auth_version != session.get('admin_auth_version')):
        abort(403)
    payment = subscription_payments_query().filter(User.staff_only.is_(False),
        Payment.id == payment_id).populate_existing().with_for_update(of=Payment).first_or_404()
    if payment.status not in ('initialized', 'pending', 'failed'):
        return jsonify(error='Only pending or failed payments can be checked here.'), 409
    if not provider.configured():
        return jsonify(error='Paystack checks are unavailable in this environment.'), 503
    subscription = db.session.get(RecurringSubscription, payment.subscription_id) if payment.subscription_id else None
    if (payment.provider != 'paystack' or payment.currency != 'NGN' or payment.amount_kobo <= 0
            or not subscription or subscription.user_id != payment.user_id
            or subscription.business_id != payment.business_id
            or subscription.plan_code != payment.plan_code
            or subscription.billing_interval != payment.billing_interval
            or subscription.amount_kobo != payment.amount_kobo):
        return jsonify(error='Saved payment details need review before checking.'), 409
    try:
        reference = provider.identifier(payment.reference)
    except ValueError:
        return jsonify(error='Saved payment details need review before checking.'), 409
    now = datetime.utcnow()
    recent = AuditLog.query.filter(AuditLog.action == REQUESTED)
    actor_limited = recent.filter(AuditLog.actor_id == actor.id, AuditLog.created_at > now-timedelta(minutes=5)).count() >= 10
    if (actor_limited or recent.filter(AuditLog.target_type == 'payment', AuditLog.target_id == payment.id,
                             AuditLog.created_at > now-timedelta(seconds=60)).first()):
        return jsonify(error='Please wait before checking again.'), 429, {'Retry-After': '300' if actor_limited else '60'}
    owner = db.session.get(User, payment.user_id)
    expected = dict(reference=reference, amount=payment.amount_kobo, currency=payment.currency,
        email=owner.email, customer_code=subscription.provider_customer_code, mode=provider.mode(),
        checkout=reference == subscription.checkout_reference,
        metadata=dict(product='stockbridge_subscription', user_id=payment.user_id,
            subscription_id=subscription.id, plan_code=payment.plan_code, billing_interval=payment.billing_interval))
    identifier, business_id = payment.id, payment.business_id
    log(REQUESTED, 'Read-only Paystack lookup requested.', actor=actor,
        business_id=business_id, target_type='payment', target_id=identifier)
    db.session.commit()  # If the claim cannot be persisted, never contact Paystack.
    try:
        result = comparison(provider.verify(reference), expected)
    except Exception:
        log_failure('admin_payment_check_failed')
        result = dict(outcome='unavailable', provider_status='Unavailable', matches=False)
    log(RESULT, 'Read-only Paystack lookup: ' + result['outcome'] + '.', actor=current_user,
        business_id=business_id, target_type='payment', target_id=identifier)
    db.session.commit()
    result['message'] = ('Paystack reports ' + result['provider_status'].lower() + '. '
        + ('Saved payment details match. ' if result['matches'] else 'The saved payment details need review. ')
        + 'Payment status and access were not changed.') if result['outcome'] != 'unavailable' else (
        'Paystack could not be checked. Payment status and access were not changed.')
    # Reapply the session/MFA guard after the network wait, including revocation.
    return admin_required(lambda: jsonify(result))()
