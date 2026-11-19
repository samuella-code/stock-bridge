"""Opt-in, staging-scoped weekly reports with durable one-send claims.

Transport exceptions and interrupted sends are never automatically retried.
This worker cannot send customer reminders or alter inventory email policy.
"""
from datetime import datetime, timedelta, timezone
import hashlib
from hmac import compare_digest
import os
import re

import click
from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import and_, or_, true
from sqlalchemy.exc import IntegrityError

from app import db, email_service
from app.admin import report_data
from app.admin.routes import (admin_bp, admin_required, _admin_user, _admin_mfa_required, _admin_second_factor_satisfied,
    log, verified_payments_query)
from app.config_safety import STAGING_PROJECT_ID
from app.models import AdminAuthenticator, AdminReportDelivery, AdminReportPreference, AuditLog, User
from app.notifications.email_outbox import valid_recipient

ORIGIN = 'https://stock-bridge-staging.vercel.app'


def gate(config=None, environ=None):
    config = current_app.config if config is None else config
    environ = os.environ if environ is None else environ
    if config.get('ADMIN_REPORT_EMAILS_ENABLED') != 'true':
        return 'disabled', set()
    sha = environ.get('VERCEL_GIT_COMMIT_SHA', '')
    recipients = config.get('ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS', '')
    allowed = recipients.split(',') if isinstance(recipients, str) else []
    if not allowed or any(not valid_recipient(email) or email != email.lower() for email in allowed):
        return 'configuration_required', set()
    expected = dict(VERCEL='1', VERCEL_PROJECT_ID=STAGING_PROJECT_ID,
        VERCEL_ENV='preview', VERCEL_TARGET_ENV='preview', VERCEL_GIT_REPO_OWNER='samuella-code',
        VERCEL_GIT_REPO_SLUG='stock-bridge', VERCEL_GIT_COMMIT_REF='release/staging-phase6')
    if (any(environ.get(key) != value for key,value in expected.items())
            or not re.fullmatch(r'[0-9a-f]{40}', sha)
            or sha != config.get('ADMIN_REPORT_EMAILS_APPROVED_SHA')
            or config.get('ADMIN_REPORT_EMAIL_ORIGIN') != ORIGIN
            or not config.get('SMTP_HOST') or not config.get('SMTP_FROM_EMAIL')):
        return 'configuration_required', set()
    return 'ready', set(allowed)


def eligible(user, preference):
    active = bool(user and preference and preference.weekly_enabled and user.email_verified_at
        and not user.suspended_at and (user.role == 'admin' or user.admin_enabled)
        and preference.auth_version == user.admin_auth_version and valid_recipient(user.email))
    if active and _admin_mfa_required(user):
        factor = db.session.get(AdminAuthenticator, user.id)
        active = bool(preference.mfa_verified and factor and factor.enabled_at)
    return active


@admin_bp.post('/reports/weekly-email')
@admin_required
def preference():
    if request.form.get('weekly_email', '') not in ('', 'on'):
        abort(400)
    if not current_user.email_verified_at:
        abort(403)
    try:
        window = report_data.report_range(request.form, datetime.utcnow())
    except ValueError:
        abort(400)
    try:
        version = int(request.form.get('version', ''))
        if version < 0:raise ValueError
    except ValueError:
        abort(400)
    # A per-user lock also serializes creation when no preference row exists yet.
    user = User.query.filter_by(id=current_user.id).populate_existing().with_for_update().one()
    if (not _admin_user(user) or not user.email_verified_at or session.get('admin_auth_version') != user.admin_auth_version
            or not _admin_second_factor_satisfied(user)):
        abort(403)
    row = db.session.get(AdminReportPreference, user.id, populate_existing=True)
    if (row.version if row else 0) != version:
        abort(409, description='This preference changed. Refresh the report before trying again.')
    enabled = request.form.get('weekly_email') == 'on'
    if row is None:
        row = AdminReportPreference(user_id=user.id, version=0)
        db.session.add(row)
    row.weekly_enabled, row.auth_version = enabled, user.admin_auth_version
    row.mfa_verified = session.get('admin_mfa_verified') is True
    row.version += 1
    row.updated_at = datetime.utcnow()
    log('ADMIN_REPORT_PREFERENCE_UPDATED', 'Weekly report email preference saved.', actor=user)
    db.session.commit()
    flash('Weekly report email preference saved.', 'success')
    return redirect(url_for('admin.reports',period=window['period'],start=window['first'].isoformat(),
        end=window['last'].isoformat()), code=303)


def finish(row_id, status, now):
    row = db.session.get(AdminReportDelivery, row_id)
    if row is None:
        db.session.rollback()
        return status
    row.status, row.completed_at = status, now
    db.session.add(AuditLog(action='ADMIN_REPORT_EMAIL_RESULT', target_type='admin_report',
        target_id=row_id, description='Weekly admin report email status: '+status+'.'))
    db.session.commit()
    return status


def dispatch_one(now=None):
    now = now or datetime.utcnow()
    state, allowed = gate()
    if state != 'ready':return state  # No SQL or transport when disabled/misconfigured.
    local = now.replace(tzinfo=timezone.utc).astimezone(report_data.LAGOS)
    if local.weekday() != 0 or not 8 <= local.hour < 12:return 'not_due'
    monday = local.date()-timedelta(days=7)
    already = AdminReportDelivery.query.filter(AdminReportDelivery.user_id == User.id,
        AdminReportDelivery.week_start == monday).exists()
    pilot = {email.strip() for email in current_app.config['ADMIN_MFA_PILOT_EMAIL'].split(',') if email.strip()}
    needs_mfa = true() if current_app.config['ADMIN_MFA_REQUIRED'] else db.func.lower(User.email).in_(pilot)
    enrolled = AdminAuthenticator.query.filter(AdminAuthenticator.user_id == User.id,
        AdminAuthenticator.enabled_at.isnot(None)).exists()
    candidate = AdminReportPreference.query.join(User, User.id == AdminReportPreference.user_id).filter(
        AdminReportPreference.weekly_enabled.is_(True), User.email_verified_at.isnot(None),
        User.suspended_at.is_(None), or_(User.role == 'admin', User.admin_enabled.is_(True)),
        AdminReportPreference.auth_version == User.admin_auth_version,
        or_(~needs_mfa, and_(AdminReportPreference.mfa_verified.is_(True), enrolled)),
        db.func.lower(User.email).in_(sorted(allowed)), ~already).order_by(AdminReportPreference.user_id).with_for_update(
            skip_locked=True, of=AdminReportPreference).first()
    if candidate is None:
        db.session.rollback()
        return 'idle'
    user_id = candidate.user_id
    user = db.session.get(User, user_id, populate_existing=True)
    if not eligible(user, candidate):
        db.session.rollback()
        return 'suppressed'
    recipient_digest = hashlib.sha256(user.email.lower().encode()).hexdigest()
    row = AdminReportDelivery(user_id=user_id, week_start=monday, recipient_digest=recipient_digest,
        status='sending', created_at=now)
    db.session.add(row)
    try:
        db.session.flush()
        row_id = row.id
        db.session.add(AuditLog(action='ADMIN_REPORT_EMAIL_CLAIMED', target_type='admin_report',
            target_id=row_id, description='Weekly admin report email claimed.'))
        db.session.commit()  # Durable uniqueness and audit must precede rendering and SMTP.
    except IntegrityError:
        db.session.rollback()
        return 'busy'
    try:
        window = report_data.report_range(dict(period='custom',start=monday.isoformat(),
            end=(monday+timedelta(days=6)).isoformat()), now)
        metrics = report_data.report(window, verified_payments_query(), now)
        link = ORIGIN+'/admin/reports?period=custom&start='+monday.isoformat()+'&end='+(monday+timedelta(days=6)).isoformat()
        body = ('StockBridge weekly platform report\n'+monday.strftime('%d/%m/%Y')+' to '+(monday+timedelta(days=6)).strftime('%d/%m/%Y')
            +' (WAT)\nNew customers: '+str(metrics['new_customers'])+'\nCollected: '+report_data.money(metrics['collected'])
            +'\nActive owner accounts now: '+str(metrics['active'])+'\nPaying owner accounts now: '+str(metrics['paying'])
            +'\nCollected revenue is not recurring revenue.\n'+link+'\nTurn off Weekly email in Platform reports to stop these emails.')
        html = render_template('email/admin_weekly_report.html', metrics=metrics, money=report_data.money,
            first=monday.strftime('%d/%m/%Y'), last=(monday+timedelta(days=6)).strftime('%d/%m/%Y'), link=link)
    except Exception:
        db.session.rollback()
        return finish(row_id, 'failed', now)
    # Re-check current consent, admin privileges, auth reset version, recipient and deployment gate.
    db.session.expire_all()
    user = db.session.get(User, user_id)
    consent = db.session.get(AdminReportPreference, user_id)
    state, allowed = gate()
    if (state != 'ready' or not eligible(user, consent) or user.email.lower() not in allowed
            or hashlib.sha256(user.email.lower().encode()).hexdigest() != recipient_digest):
        return finish(row_id, 'suppressed', now)
    try:
        sent = email_service._send_email('Your weekly StockBridge platform report', user.email.lower(), body, html=html, timeout=5)
    except Exception:
        return finish(row_id, 'unknown', now)
    return finish(row_id, 'sent' if sent is True else 'failed' if sent is False else 'unknown', now)


@admin_bp.get('/internal/reports/weekly-dispatch')
def worker():
    # Non-cookie worker authentication; it never treats an admin session as scheduler authority.
    secret = current_app.config.get('ADMIN_REPORT_EMAIL_DISPATCH_SECRET', '')
    supplied = request.headers.get('Authorization', '')
    if not isinstance(secret, str) or len(secret) < 32 or not compare_digest(supplied.encode(), ('Bearer '+secret).encode()):
        abort(404)
    response = jsonify(result=dispatch_one())
    response.headers['Cache-Control'] = 'no-store, private'
    return response


@admin_bp.cli.command('dispatch-weekly-reports')
@click.option('--limit', type=click.IntRange(1, 100), default=20)
def command(limit):
    """Drain bounded due reports from an approved scheduler; disabled by default."""
    results = {}
    for _ in range(limit):
        result = dispatch_one()
        results[result] = results.get(result, 0)+1
        if result not in ('sent', 'failed', 'unknown', 'suppressed'):break
    click.echo(results)
