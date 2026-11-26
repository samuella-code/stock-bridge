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
from app.notifications.staging_email_policy import policy, stamp_test_job

worker_bp = Blueprint('inventory_email_worker', __name__)
PREFERENCE_FIELDS = ('low_stock_email', 'out_of_stock_email', 'product_added', 'sales', 'restocking', 'daily_summary_email', 'weekly_summary_email')


def enabled(business_id, field):
    preference = db.session.get(NotificationPreference, business_id)
    if field in ('daily_summary_email', 'weekly_summary_email'):
        return preference is not None and bool(getattr(preference, field, False))
    return preference is None or bool(getattr(preference, field))


def valid_recipient(email):
    return bool(email and len(email) <= 180 and re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email))


def daily_digest_replaces_low_stock(business_id, owner):
    if (not current_app.config.get('CUSTOMER_SUMMARY_EMAILS_ENABLED')
            or not enabled(business_id, 'daily_summary_email')):
        return False
    selected = policy()
    if selected.mode == 'blocked' or (selected.mode == 'staging' and not selected.allows(owner.email)):
        return False
    try:
        customer_origin()
    except (ValueError, TypeError):
        return False
    return True


def queue_alert(product, quantity, kind, event_key):
    business = db.session.get(Business, product.business_id)
    owner = db.session.get(User, business.user_id)
    # Opting into the daily digest replaces individual low-stock emails; an out-of-stock alert remains immediate.
    if kind == 'low_stock' and daily_digest_replaces_low_stock(business.id, owner):
        return None
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
        payload={'business_name': business.name, 'product_id': product.id, 'product_name': product.name,
            'quantity': quantity, 'threshold': product.minimum_stock_level, 'unit': product.unit})
    stamp_test_job(row)
    db.session.add(row)
    db.session.info['inventory_alert_created'] = True
    return row

def queue_restock_alert(business_id, items, event_key):
    """Queue one durable email for a successful restocking batch."""
    if not enabled(business_id, 'restocking'):
        return None

    business = db.session.get(Business, business_id)
    if business is None:
        return None

    owner = db.session.get(User, business.user_id)
    if (
        owner is None
        or not owner.email_verified_at
        or owner.suspended_at
        or business.suspended_at
        or owner.role == 'admin'
        or not valid_recipient(owner.email)
    ):
        return None

    if not items or len(items) > 100:
        return None

    payload_items = []
    for item in items:
        name = str(item['name']).replace('\r', ' ').replace('\n', ' ')
        unit = str(item['unit']).replace('\r', ' ').replace('\n', ' ')
        quantity = item['quantity']
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError('Restock quantities must be positive integers')
        payload_items.append({'name': name, 'quantity': quantity, 'unit': unit})

    count = len(payload_items)
    subject = (
        f"Stock Replenished — {payload_items[0]['name']} | StockBridge"
        if count == 1
        else f"Stock Replenished — {count} Products | StockBridge"
    )

    row = EmailOutbox(
        business_id=business.id,
        recipient_user_id=owner.id,
        recipient_email=owner.email,
        event_type='restock_recorded',
        event_key=event_key,
        subject=subject.replace('\r', ' ').replace('\n', ' ')[:240],
        max_attempts=max(
            1,
            min(10, current_app.config['INVENTORY_EMAIL_MAX_ATTEMPTS'])
        ),
        payload={
            'business_name': business.name,
            'items': payload_items,
        },
    )

    stamp_test_job(row)
    db.session.add(row)
    db.session.info['inventory_alert_created'] = True
    return row
def customer_origin():
    # Explicit origin: VERCEL_ENV=production also describes staging-project deploys.
    raw = (current_app.config.get('CUSTOMER_SUMMARY_EMAIL_BASE_URL') or current_app.config['INVENTORY_EMAIL_BASE_URL'])
    url = urlsplit(raw)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ('', '/') or url.port not in (None, 443)):
        raise ValueError('Inventory email origin unavailable')
    return raw.rstrip('/')


def eligible(now):
    return or_(and_(EmailOutbox.status.in_(('pending', 'retry')), EmailOutbox.next_attempt_at <= now),
        and_(EmailOutbox.status == 'processing', EmailOutbox.claim_expires_at <= now))


def claim(now=None, user_id=None, business_id=None, event_types=None):
    """PG SKIP LOCKED plus a guarded UPDATE; SQLite uses the same CAS guard."""
    now = now or datetime.utcnow()
    selected_policy = policy(now)
    if selected_policy.mode == 'blocked':
        return None
    safety_conditions = selected_policy.conditions()
    # A crashed final attempt must become terminal rather than remain stuck forever.
    exhausted = update(EmailOutbox).where(*safety_conditions, eligible(now), EmailOutbox.attempt_count >= EmailOutbox.max_attempts)
    if user_id is not None:
        exhausted = exhausted.where(EmailOutbox.recipient_user_id == user_id, EmailOutbox.business_id == business_id)
    if event_types:
        exhausted = exhausted.where(EmailOutbox.event_type.in_(event_types))
    db.session.execute(exhausted.values(
            status='failed', claim_token=None, claim_expires_at=None,
            last_error='AttemptsExhausted', updated_at=now))
    query = EmailOutbox.query.filter(*safety_conditions, eligible(now), EmailOutbox.attempt_count < EmailOutbox.max_attempts)
    if user_id is not None:
        query = query.filter_by(recipient_user_id=user_id, business_id=business_id)
    if event_types:
        query = query.filter(EmailOutbox.event_type.in_(event_types))
    row = query.order_by(EmailOutbox.next_attempt_at, EmailOutbox.id).with_for_update(skip_locked=True).first()
    if row is None:
        db.session.commit()
        return None
    row_id = row.id
    token = uuid4().hex
    changed = db.session.execute(update(EmailOutbox).where(*safety_conditions, EmailOutbox.id == row_id,
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


def dispatch_one(user_id=None, business_id=None, now=None, event_types=None):
    if not (current_app.config['INVENTORY_EMAILS_ENABLED'] or current_app.config.get('CUSTOMER_SUMMARY_EMAILS_ENABLED')):
        return 'disabled'
    if policy(now).mode == 'blocked':
        return 'disabled'
    # Invalid configuration must not consume all jobs' attempts.
    try:
        origin = customer_origin()
    except (ValueError, TypeError):
        return 'configuration_required'
    now = now or datetime.utcnow()
    summary_only = not current_app.config['INVENTORY_EMAILS_ENABLED']
    lease = claim(now, user_id, business_id,
        event_types=event_types or (('daily_summary', 'weekly_summary') if summary_only else None))
    if lease is None:
        return 'idle'
    row_id, token = lease
    row = db.session.get(EmailOutbox, row_id)
    business = db.session.get(Business, row.business_id)
    owner = db.session.get(User, row.recipient_user_id)
    if (not business or not owner or business.user_id != owner.id or owner.role == 'admin' or owner.staff_only
            or owner.suspended_at or business.suspended_at or not owner.email_verified_at
            or owner.email != row.recipient_email or not valid_recipient(row.recipient_email)
           or row.event_type not in ('low_stock', 'out_of_stock', 'restock_recorded', 'daily_summary', 'weekly_summary')):
        finish(row_id, token, 'failed', now, last_error='RecipientUnavailable')
        return 'failed'
    preference_field = ({
        'restock_recorded': 'restocking',
        'daily_summary': 'daily_summary_email',
        'weekly_summary': 'weekly_summary_email',
    }.get(row.event_type, row.event_type + '_email'))
    if not enabled(row.business_id, preference_field):
        finish(row_id, token, 'suppressed', now, last_error=None)
        return 'suppressed'
    payload, subject, recipient = dict(row.payload), row.subject, row.recipient_email
    created_at = row.created_at
    first_name = owner.full_name.split()[0] if owner.full_name and owner.full_name.split() else 'there'
    attempts, maximum, kind = row.attempt_count, row.max_attempts, row.event_type
    if kind in ('daily_summary', 'weekly_summary') and not current_app.config.get('CUSTOMER_SUMMARY_EMAILS_ENABLED'):
        finish(row_id, token, 'suppressed', now, last_error=None)
        return 'suppressed'
    try:
        if kind in ('daily_summary', 'weekly_summary'):
            link = origin + ('/restocking/' if kind == 'daily_summary' else '/')
            summary = payload
            if kind == 'daily_summary':
                body_lines = [f"- {item['name']}: {item['quantity']} {item['unit']} left" for item in summary['low_stock_products']]
                body = (f"Hi {first_name},\n\n{summary['low_stock_count']} products need restocking in {summary['business_name']}.\n"
                    + "\n".join(body_lines) + f"\n\nRestock these {summary['low_stock_count']} products: {link}\n\nStockBridge — Know your stock. Understand your profit.")
                preheader = f"{summary['low_stock_count']} products need restocking in {summary['business_name']}."[:90]
            else:
                best = summary.get('best_seller')
                low_lines = [f"- {item['name']}: {item['quantity']} {item['unit']} left" for item in summary['low_stock_products']]
                body = (f"Hi {first_name},\n\nWeekly summary for {summary['business_name']} ({summary['period_label']}).\n"
                    f"Sales: ₦{summary['sales']}\nCost of goods: ₦{summary['cost_of_goods']}\n"
                    f"Expenses: ₦{summary['expenses']}\nKept: ₦{summary['kept']}\nUnits sold: {summary['units_sold']}\n"
                    + (f"Best seller: {best['name']} ({best['quantity']} {best['unit']}{'' if best['quantity'] == 1 else 's'}).\n" if best else "")
                    + (f"\nProducts needing restock:\n" + "\n".join(low_lines) + "\n" if low_lines else "")
                    + f"\nView business overview: {link}\n\nStockBridge — Know your stock. Understand your profit.")
                preheader = f"Your weekly numbers for {summary['business_name']} are ready."[:90]
            email_reason = (
                f"You get this email because daily restock summaries are on for {summary['business_name']}."
                if kind == 'daily_summary' else
                f"You get this email because weekly business summaries are on for {summary['business_name']}."
            )
            html = render_template('email/customer_summary.html', summary=summary, link=link,
                first_name=first_name, preheader=preheader, email_reason=email_reason,
                change_alerts_url=origin + '/profile/')
        elif kind == 'restock_recorded':
            link = origin + '/restocking/'
            action = 'View Restocking'
            item_lines = [
                f"- {item['name']}: +{item['quantity']} {item['unit']}{'' if item['quantity'] == 1 else 's'}"
                for item in payload['items']
            ]
            body = (
                f"Hi {first_name},\n\nYour stock has been replenished.\n"
                f"Business: {payload['business_name']}\n\n"
                "Products restocked:\n" + "\n".join(item_lines)
                + f"\n\nView Restocking: {link}\n\n"
                "StockBridge — Know your stock. Understand your profit."
            )
            html = render_template(
                'email/restock_alert.html',
                alert=payload,
                action=action,
                link=link,
                first_name=first_name,
                preheader=f"Your stock has been replenished in {payload['business_name']}.",
            )
        else:
            product_link = (
                f"{origin}/notifications/email/product/{business.id}/{payload['product_id']}"
            )
            link = product_link + ('?action=restock' if kind == 'low_stock' else '?action=restock')
            action = f"Restock {payload['product_name']}" if kind == 'low_stock' else 'Restock now'
            body = (
                f"Hi {first_name},\n\n{payload['product_name']} "
                f"{'is running low' if kind == 'low_stock' else 'is out of stock'} in {payload['business_name']}.\n"
                f"Left in stock: {payload['quantity']} {payload['unit']}{'' if payload['quantity'] == 1 else 's'}\n"
                f"Alert level: {payload['threshold']} {payload['unit']}{'' if payload['threshold'] == 1 else 's'}\n\n"
                f"{action}: {link}\n\n"
                "StockBridge — Know your stock. Understand your profit."
            )
            html = render_template(
                'email/inventory_alert.html',
                alert=payload,
                kind=kind,
                action=action,
                link=link,
                first_name=first_name,
                preheader=(f"Only {payload['quantity']} {payload['unit']}{'' if payload['quantity'] == 1 else 's'} left in {payload['business_name']}." if kind == 'low_stock' else f"{payload['product_name']} is out of stock in {payload['business_name']}.")[:90],
                inventory_link=product_link,
            )

        db.session.commit()  # No database locks or open transaction during SMTP.
        current_policy = policy()
        if not current_policy.permits_job(recipient, payload, created_at):
            finish(row_id, token, 'suppressed', datetime.utcnow(), last_error=None)
            return 'suppressed'
        if not email_service._send_email(subject, recipient, body, html=html):
            raise RuntimeError('TransportUnavailable')
    except Exception as error:
        db.session.rollback()
        # Exception strings can contain SMTP credentials/provider URLs. Never log them.
        error_type = type(error).__name__[:60]
        current_app.logger.warning(
            'Inventory email job %s delivery failed (%s)', row_id, error_type
        )
        delay = min(
            3600,
            max(1, current_app.config['INVENTORY_EMAIL_RETRY_SECONDS']) * 2 ** (attempts - 1)
        )
        status = 'failed' if attempts >= maximum else 'retry'
        finish(
            row_id, token, status, datetime.utcnow(),
            last_error=error_type,
            next_attempt_at=now + timedelta(seconds=delay)
        )
        return status

    finish(
        row_id, token, 'sent', datetime.utcnow(),
        sent_at=datetime.utcnow(), last_error=None
    )
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


@worker_bp.post('/internal/customer-email-summaries/run')
@csrf.exempt
def run_customer_summaries():
    """Bearer-authenticated scheduler entry point; drains summary jobs only."""
    secret = current_app.config.get('CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET', '')
    supplied = request.headers.get('Authorization', '')
    if (not isinstance(secret, str) or len(secret) < 32
            or not compare_digest(supplied.encode('utf-8'), ('Bearer ' + secret).encode('utf-8'))):
        abort(404)
    if not current_app.config.get('CUSTOMER_SUMMARY_EMAILS_ENABLED'):
        response = jsonify(queued=0, result='disabled')
        response.headers['Cache-Control'] = 'no-store'
        return response, 200
    from app.notifications.summaries import queue_due_summaries
    queued = queue_due_summaries()
    # One SMTP delivery per HTTP invocation keeps serverless execution bounded.
    result = dispatch_one(event_types=('daily_summary', 'weekly_summary'))
    response = jsonify(queued=queued, result=result)
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
    if policy().mode == 'blocked':
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
