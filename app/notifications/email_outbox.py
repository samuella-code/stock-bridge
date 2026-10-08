"""Short, separate-invocation SMTP dispatcher. Durable state lives only in SQL."""
from datetime import datetime, timedelta
from hmac import compare_digest
import re
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Blueprint, abort, current_app, g, has_request_context, jsonify, render_template, request
from sqlalchemy import and_, or_, update, event
from sqlalchemy.orm import Session
import requests
from app import csrf, db
from app.models import Business, EmailOutbox, NotificationPreference, User
from app import email_service

worker_bp = Blueprint('inventory_email_worker', __name__)
PREFERENCE_FIELDS = ('low_stock_email', 'out_of_stock_email', 'product_added', 'sales', 'restocking')


def enabled(business_id, field):
    preference = db.session.get(NotificationPreference, business_id)
    return preference is None or bool(getattr(preference, field))


def valid_recipient(email):
    return bool(email and len(email) <= 180 and re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email))


def queue_alert(product, quantity, kind, event_key):
    business = db.session.get(Business, product.business_id)
    owner = db.session.get(User, business.user_id)
    if (not enabled(business.id, kind + '_email') or not owner.email_verified_at
            or owner.suspended_at or business.suspended_at or owner.role == 'admin'
            or not valid_recipient(owner.email)):
        return None
    label = 'Low Stock Alert' if kind == 'low_stock' else 'Out of Stock'
    name = product.name.replace('\r', ' ').replace('\n', ' ')
    row = EmailOutbox(business_id=business.id, recipient_user_id=owner.id,
        recipient_email=owner.email, event_type=kind, event_key=event_key,
        subject=f'{label} — {name} | StockBridge'[:240],
        max_attempts=max(1, min(10, current_app.config['INVENTORY_EMAIL_MAX_ATTEMPTS'])),
        payload={'business_name': business.name, 'product_name': product.name,
            'quantity': quantity, 'threshold': product.minimum_stock_level, 'unit': product.unit})
    db.session.add(row)
    db.session.info['inventory_alert_created'] = True
    return row


def customer_origin():
    # Explicit origin: VERCEL_ENV=production also describes staging-project deploys.
    raw = current_app.config['INVENTORY_EMAIL_BASE_URL']
    url = urlsplit(raw)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/') or url.port not in (None, 443)):
        raise ValueError('Inventory email origin unavailable')
    return raw.rstrip('/')


def eligible(now):
    return or_(and_(EmailOutbox.status.in_(('pending', 'retry')), EmailOutbox.next_attempt_at <= now),
        and_(EmailOutbox.status == 'processing', EmailOutbox.claim_expires_at <= now))


def claim(now=None, user_id=None, business_id=None):
    """PG SKIP LOCKED plus a guarded UPDATE; SQLite uses the same CAS guard."""
    now = now or datetime.utcnow()
    # A crashed final attempt must become terminal rather than remain stuck forever.
    exhausted = update(EmailOutbox).where(eligible(now), EmailOutbox.attempt_count >= EmailOutbox.max_attempts)
    if user_id is not None:
        exhausted = exhausted.where(EmailOutbox.recipient_user_id == user_id, EmailOutbox.business_id == business_id)
    db.session.execute(exhausted.values(
            status='failed', claim_token=None, claim_expires_at=None,
            last_error='AttemptsExhausted', updated_at=now))
    query = EmailOutbox.query.filter(eligible(now), EmailOutbox.attempt_count < EmailOutbox.max_attempts)
    if user_id is not None:
        query = query.filter_by(recipient_user_id=user_id, business_id=business_id)
    row = query.order_by(EmailOutbox.next_attempt_at, EmailOutbox.id).with_for_update(skip_locked=True).first()
    if row is None:
        db.session.commit()
        return None
    row_id = row.id
    token = uuid4().hex
    changed = db.session.execute(update(EmailOutbox).where(EmailOutbox.id == row_id,
        eligible(now), EmailOutbox.attempt_count < EmailOutbox.max_attempts).values(
            status='processing', claim_token=token,
            claim_expires_at=now + timedelta(seconds=current_app.config['INVENTORY_EMAIL_LEASE_SECONDS']),
            attempt_count=EmailOutbox.attempt_count + 1, last_attempt_at=now, updated_at=now))
    db.session.commit()  # Lease is durable BEFORE any network work.
    return (row_id, token) if changed.rowcount == 1 else None


def finish(row_id, token, status, now, **values):
    result = db.session.execute(update(EmailOutbox).where(EmailOutbox.id == row_id,
        EmailOutbox.status == 'processing', EmailOutbox.claim_token == token).values(
            status=status, claim_token=None, claim_expires_at=None, updated_at=now, **values))
    db.session.commit()
    return result.rowcount == 1


def dispatch_one(user_id=None, business_id=None, now=None):
    if not current_app.config['INVENTORY_EMAILS_ENABLED']:
        return 'disabled'
    # Invalid configuration must not consume all jobs' attempts.
    try:
        origin = customer_origin()
    except (ValueError, TypeError):
        return 'configuration_required'
    now = now or datetime.utcnow()
    lease = claim(now, user_id, business_id)
    if lease is None:
        return 'idle'
    row_id, token = lease
    row = db.session.get(EmailOutbox, row_id)
    business = db.session.get(Business, row.business_id)
    owner = db.session.get(User, row.recipient_user_id)
    if (not business or not owner or business.user_id != owner.id or owner.role == 'admin'
            or owner.suspended_at or business.suspended_at or not owner.email_verified_at
            or owner.email != row.recipient_email or not valid_recipient(row.recipient_email)
            or row.event_type not in ('low_stock', 'out_of_stock')):
        finish(row_id, token, 'failed', now, last_error='RecipientUnavailable')
        return 'failed'
    if not enabled(row.business_id, row.event_type + '_email'):
        finish(row_id, token, 'suppressed', now, last_error=None)
        return 'suppressed'
    payload, subject, recipient = dict(row.payload), row.subject, row.recipient_email
    attempts, maximum, kind = row.attempt_count, row.max_attempts, row.event_type
    link = origin + ('/products/?view=inventory' if kind == 'low_stock' else '/restocking/')
    action = 'View Inventory' if kind == 'low_stock' else 'Restock Product'
    body = (f"Hello,\n\n{subject}\nBusiness: {payload['business_name']}\nProduct: {payload['product_name']}\n"
        f"Current stock: {payload['quantity']} {payload['unit']}\nLow-stock threshold: {payload['threshold']} {payload['unit']}\n\n"
        f"{action}: {link}\nSelect {payload['business_name']} if another business is active.\n\nStockBridge — Manage your business with confidence.")
    html = render_template('email/inventory_alert.html', alert=payload, kind=kind, action=action, link=link)
    db.session.commit()  # No database locks or open transaction during SMTP.
    try:
        if not email_service._send_email(subject, recipient, body, html=html):
            raise RuntimeError('TransportUnavailable')
    except Exception as error:
        # Exception strings can contain SMTP credentials/provider URLs. Never log them.
        error_type = type(error).__name__[:60]
        current_app.logger.warning('Inventory email job %s delivery failed (%s)', row_id, error_type)
        delay = min(3600, max(1, current_app.config['INVENTORY_EMAIL_RETRY_SECONDS']) * 2 ** (attempts - 1))
        status = 'failed' if attempts >= maximum else 'retry'
        finish(row_id, token, status, datetime.utcnow(), last_error=error_type,
            next_attempt_at=now + timedelta(seconds=delay))
        return status
    finish(row_id, token, 'sent', datetime.utcnow(), sent_at=datetime.utcnow(), last_error=None)
    return 'sent'


@worker_bp.route('/internal/inventory-emails/dispatch', methods=['GET', 'POST'])
@csrf.exempt
def dispatch():
    """Only this non-cookie, bearer-authenticated worker route is CSRF exempt."""
    secret = current_app.config['INVENTORY_EMAIL_DISPATCH_SECRET']
    supplied = request.headers.get('Authorization', '')
    if len(secret) < 32 or not compare_digest(supplied.encode('utf-8'), ('Bearer ' + secret).encode('utf-8')):
        abort(404)
    response = jsonify(result=dispatch_one())
    response.headers['Cache-Control'] = 'no-store'
    return response


@event.listens_for(Session, 'after_commit')
def committed_alert(session):
    if session.info.pop('inventory_alert_created', False) and has_request_context():
        g.inventory_alert_committed = True


@event.listens_for(Session, 'after_rollback')
def discard_alert_trigger(session):
    session.info.pop('inventory_alert_created', None)


def publish_committed_alert(response):
    """Optional durable-queue ingress, invoked only AFTER successful SQL commit.

    The configured ingress must acknowledge durable acceptance and invoke the
    authenticated dispatcher separately. The periodic poller recovers a failed
    publish. No SMTP runs here and no queue is silently provisioned.
    """
    if not g.pop('inventory_alert_committed', False) or not current_app.config['INVENTORY_EMAILS_ENABLED']:
        return response
    raw = current_app.config['INVENTORY_EMAIL_TRIGGER_URL']
    secret = current_app.config['INVENTORY_EMAIL_TRIGGER_SECRET']
    if not raw:
        return response
    try:
        url = urlsplit(raw)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or url.query or len(secret) < 32:
            raise ValueError('Invalid queue ingress')
        result = requests.post(raw, json={'event':'inventory_outbox_ready'},
            headers={'Authorization':'Bearer ' + secret}, timeout=3, allow_redirects=False)
        if not 200 <= result.status_code < 300:
            raise RuntimeError('Queue ingress unavailable')
    except Exception:
        current_app.logger.warning('Inventory email trigger unavailable; durable jobs await recovery dispatch.')
    return response
