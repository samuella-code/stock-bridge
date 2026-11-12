"""Staff-only opt-in delivery, independent of the paused inventory dispatcher.

Commit the invitation digest first. Claim/commit before SMTP. Never persist the
raw link or retry an uncertain send. Owner resend explicitly replaces the token.
"""
from datetime import datetime
import hashlib
import os
import re
from flask import current_app
from app import db, email_service
from app.models import StaffInvitation, User, AuditLog, StaffAuditEvent
from app.staff import service
from paystack_policy import STAGING_PROJECT_ID, STAGING_HOST


def gate(recipient, config, environ):
    flag = config.get('STAFF_INVITATION_EMAIL_ENABLED', '')
    if flag in ('', 'false', None): return 'disabled'
    if flag != 'true': return 'configuration_required'
    sha = environ.get('VERCEL_GIT_COMMIT_SHA', '')
    allowed = config.get('STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS', '')
    if not isinstance(allowed, str): return 'configuration_required'
    recipients = allowed.split(',')
    if not recipients or any(not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value)
                             or value != value.lower() or len(value)>180 for value in recipients):
        return 'configuration_required'
    if not (environ.get('VERCEL') == '1' and environ.get('VERCEL_PROJECT_ID') == STAGING_PROJECT_ID
            and environ.get('VERCEL_ENV') == 'preview' and environ.get('VERCEL_TARGET_ENV') == 'preview'
            and environ.get('VERCEL_GIT_COMMIT_REF') == 'release/staging-phase6'
            and isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha)
            and sha == config.get('STAFF_INVITATION_EMAIL_APPROVED_SHA')
            and config.get('STAFF_INVITATION_EMAIL_ORIGIN') == 'https://' + STAGING_HOST):
        return 'configuration_required'
    return 'ready' if recipient in recipients else 'suppressed'


def audit(row, status, *, actor_id=None):
    log = AuditLog(actor_id=row.owner_id if actor_id is None else actor_id, action='STAFF_DELIVERY_RESULT', target_type='staff',
        target_id=row.id, description='Staff invitation delivery status recorded.')
    db.session.add(log); db.session.flush()
    db.session.add(StaffAuditEvent(audit_log_id=log.id, owner_id=row.owner_id,
        details=dict(invitation_id=row.id, delivery_status=status)))


def finish(identifier, digest, status, *, actor_id=None):
    row = db.session.get(StaffInvitation, identifier)
    if not row: db.session.rollback(); return
    service.lock_owner(row.owner_id)
    row = db.session.get(StaffInvitation, identifier)
    if row.state in ('pending', 'accepted') and row.token_digest == digest and row.delivery_status == 'sending':
        row.delivery_status = status; audit(row, status, actor_id=actor_id)
    db.session.commit()


def deliver(identifier, raw, *, actor_id=None):
    """One bounded transport attempt; return fixed labels, never exception text."""
    row = db.session.get(StaffInvitation, identifier)
    if not row: db.session.rollback(); return 'suppressed'
    service.lock_owner(row.owner_id)
    now = datetime.utcnow()
    row = db.session.get(StaffInvitation, identifier)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    if row.state != 'pending' or row.token_digest != digest or row.delivery_status != 'pending':
        db.session.rollback(); return 'suppressed'
    status = gate(row.email, current_app.config, os.environ)
    try:
        service.invitation_for(raw, now=now)  # owner Pro, expiry, current role/assignments
    except ValueError:
        status = 'suppressed'
    if status != 'ready':
        row.delivery_status = status; audit(row, status, actor_id=actor_id); db.session.commit(); return status
    recipient = row.email
    row.delivery_status, row.delivery_attempted_at = 'sending', now
    db.session.commit()  # No open transaction/owner lock during SMTP.
    link = 'https://' + STAGING_HOST + '/staff/invite#token=' + raw
    from app.staff.sales_access import enabled
    workflow_note = ('Sales access requires verification, sales permissions and an assigned business. ' if enabled()
                     else 'Operational staff workflows are not yet enabled. ')
    from app.staff.stock_access import enabled as stock_enabled
    if stock_enabled():
        workflow_note = 'Enabled workflows require verification, matching role permissions and assigned businesses. '
    from app.staff.operations_access import enabled as operations_enabled
    if operations_enabled():
        workflow_note = 'Enabled workflows require verification, matching role permissions and assigned businesses. '
    body = ('You have been invited to StockBridge staff access. Open this private link:\n'
            + link + '\nIt expires within 48 hours and can be used once. '
            'If you do not have an account, choose your own credentials and complete '
            'the separate email-verification flow. No business, billing account or trial '
            'is created. ' + workflow_note +
            'Ignore this email if unexpected. Do not forward the link.')
    try:
        html = current_app.jinja_env.get_template('email/staff_invitation.html').render(
            invitation_url=link,
        )
    except Exception:
        # Rendering failed before SMTP was attempted.
        finish(identifier, digest, 'failed', actor_id=actor_id)
        return 'failed'
    try:
        sent = email_service._send_email('Your StockBridge staff invitation', recipient, body, html=html)
        status = 'sent' if sent is True else 'failed'
    except Exception:
        # A server may have accepted the message before the error. No retry or
        # exception logging: SMTP already emits sanitized fixed diagnostics.
        status = 'unknown'
    finish(identifier, digest, status, actor_id=actor_id)
    return status
