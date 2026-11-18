"""Admin customer tools. Mutations keep the administrator as the audited actor."""
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta
from flask import abort, jsonify, render_template, request, Response, stream_with_context
from flask_login import current_user
from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload, joinedload
from app import db
from app.admin import customer_data as data
from app.admin.routes import admin_bp, admin_required, log, change_suspension
from app.error_handling import log_failure
from app.models import (AccountBilling, AdminCustomerNote, AdminCustomerMessage, AuditLog, Business,
                        StaffInvitation, StaffMembership, Product, Sale, Restock, Expense, User)
from app.staff import service, delivery
from app.subscriptions.entitlements import EntitlementSnapshot


def payload():
    value = request.get_json(silent=True) if request.is_json else request.form
    if not hasattr(value, 'get'):
        abort(400)
    return value


def text(value, name, maximum, *, required=True):
    result = value.get(name, '')
    if not isinstance(result, str) or len(result.strip()) > maximum or (required and not result.strip()):
        abort(400, description='Check the supplied fields.')
    return result.strip()


def number(value, name, minimum, maximum):
    raw = value.get(name)
    if isinstance(raw, bool) or not re.fullmatch(r'\d+', str(raw)):
        abort(400)
    result = int(raw)
    if not minimum <= result <= maximum:
        abort(400)
    return result


def filters():
    status = request.args.get('status', 'all')
    if status not in data.FILTERS:
        abort(400)
    return request.args.get('q', '').strip()[:100], status


def csv_cell(value):
    value = str(value if value is not None else '')
    if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def csv_response(filename, header, batches):
    def generate():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(header)
        yield '\ufeff' + output.getvalue()
        for rows in batches():
            output.seek(0)
            output.truncate(0)
            writer.writerows([[csv_cell(cell) for cell in row] for row in rows])
            yield output.getvalue()
    return Response(stream_with_context(generate()), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


@admin_bp.get('/customers/data')
@admin_required
def customers_data():
    q, status = filters()
    now = datetime.utcnow()
    page = data.filtered_customers(q, status, now).paginate(page=request.args.get('page', 1, type=int), per_page=25, error_out=False)
    loader = data.CustomerRows(page.items, now)
    return jsonify(customers=[loader.serialize(u) for u in page.items], summary=data.summary(now),
                   page=page.page, pages=page.pages, total=page.total)


@admin_bp.get('/customers/export.csv')
@admin_required
def customers_export():
    q, status = filters()
    now = datetime.utcnow()
    def batches():
        cursor = None
        while True:
            query = data.filtered_customers(q, status, now).filter(User.created_at <= now)
            if cursor:
                query = query.filter(or_(User.created_at < cursor[0], and_(User.created_at == cursor[0], User.id < cursor[1])))
            users = query.limit(100).all()
            if not users:
                return
            loader = data.CustomerRows(users, now)
            yield [[r['name'], 'Employee' if r['employee'] else 'Account owner', r['email'],
                    ', '.join(b['name'] for b in r['businesses']), r['plan'], r['joined'], r['last_active']]
                   for r in (loader.serialize(u) for u in users)]
            cursor = (users[-1].created_at, users[-1].id)
    return csv_response('StockBridge-customers.csv', ['Customer', 'Account type', 'Masked email', 'Business', 'Plan', 'Joined', 'Last active'], batches)


@admin_bp.get('/users/<int:user_id>/data')
@admin_required
def customer_data(user_id):
    return jsonify(data.details(data.customer(user_id), datetime.utcnow()))


@admin_bp.post('/users/<int:user_id>/email')
@admin_required
def reveal_customer_email(user_id):
    user = data.customer(user_id)
    log('ADMIN_EMAIL_REVEALED', f'Full email revealed for account {user.id}.', actor=current_user,
        target_type='user', target_id=user.id)
    db.session.commit()
    return jsonify(email=user.email)


@admin_bp.post('/users/<int:user_id>/note')
@admin_required
def save_customer_note(user_id):
    values = payload()
    body = text(values, 'body', 5000, required=False)
    version = number(values, 'version', 0, 2147483646)
    user = data.customer(user_id)
    service.lock_owner(user.id)
    note = db.session.get(AdminCustomerNote, user.id)
    if (note.version if note else 0) != version:
        abort(409, description='Another administrator updated this note. Reload before saving.')
    if note:
        note.body, note.version = body, version + 1
        note.updated_by, note.updated_at = current_user.id, datetime.utcnow()
    else:
        note = AdminCustomerNote(user_id=user.id, body=body, version=1, updated_by=current_user.id)
        db.session.add(note)
    log('ADMIN_NOTE_UPDATED', f'Internal note updated for account {user.id}.', actor=current_user,
        target_type='user', target_id=user.id)
    db.session.commit()
    return jsonify(version=note.version, updated_at=data.local_time(note.updated_at))


@admin_bp.post('/users/<int:user_id>/trial')
@admin_required
def extend_customer_trial(user_id):
    values = payload()
    user = data.customer(user_id)
    days = number(values, 'days', 1, 30)
    reason = text(values, 'reason', 300)
    expected = text(values, 'expected_end', 40)
    service.lock_owner(user.id)
    user = data.customer(user_id)
    snapshot = EntitlementSnapshot([user])
    account = snapshot.accounts.get(user.id)
    if (user.staff_only or user.suspended_at or not user.email_verified_at or not account or not account.trial_started_at
            or not account.trial_ends_at or snapshot.subscriptions.get(user.id) or snapshot.results[user.id].legacy):
        abort(409, description='Only a verified owner with an existing trial can receive a trial extension.')
    if account.trial_ends_at.isoformat() != expected:
        abort(409, description='Trial dates changed. Reload before extending.')
    now = datetime.utcnow()
    account.trial_ends_at = max(now, account.trial_ends_at) + timedelta(days=days)
    log('ADMIN_TRIAL_EXTENDED', f'Trial extended by {days} days. Reason: {reason}', actor=current_user,
        target_type='user', target_id=user.id)
    db.session.commit()
    return jsonify(trial_end=account.trial_ends_at.isoformat(), trial_end_local=data.local_time(account.trial_ends_at))


@admin_bp.post('/users/<int:user_id>/message')
@admin_required
def message_customer(user_id):
    values = payload()
    user = data.customer(user_id)
    if user.staff_only:
        abort(409, description='Plan messages are sent to account owners only.')
    subject = text(values, 'subject', 150)
    body = text(values, 'body', 5000)
    key = text(values, 'request_key', 64)
    if '\n' in subject or '\r' in subject or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', key):
        abort(400)
    digest = hashlib.sha256(json.dumps([user.id, subject, body], ensure_ascii=False).encode()).hexdigest()
    service.lock_owner(user.id)
    existing = AdminCustomerMessage.query.filter_by(actor_id=current_user.id, request_key=key).first()
    if existing:
        if existing.user_id != user.id or existing.content_digest != digest:
            abort(409, description='This message request was already used for different content.')
        return jsonify(status=existing.status, message_id=existing.id), 200
    row = AdminCustomerMessage(actor_id=current_user.id, user_id=user.id, request_key=key, content_digest=digest)
    db.session.add(row)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        abort(409, description='This message request is already being processed.')
    actor_id, identifier, recipient = current_user.id, row.id, user.email
    log('ADMIN_MESSAGE_REQUESTED', f'Customer message {row.id} claimed for delivery.', actor=current_user,
        target_type='user', target_id=user.id)
    db.session.commit()
    # Commit the unique claim before SMTP. Repeating the same key never resends,
    # including after a process interruption or uncertain transport result.
    from app.email_service import _send_email
    try:
        status = 'sent' if _send_email(subject, recipient, body) else 'failed'
    except Exception:
        log_failure('admin_customer_message_failed')
        status = 'unknown'
    db.session.execute(update(AdminCustomerMessage).where(AdminCustomerMessage.id == identifier).values(
        status=status, completed_at=datetime.utcnow()))
    log('ADMIN_MESSAGE_RESULT', f'Customer message {identifier} delivery status: {status}.', actor=current_user,
        target_type='user', target_id=user.id)
    db.session.commit()
    return jsonify(status=status, message_id=identifier), 200 if status == 'sent' else 202


@admin_bp.get('/staff-invitations/data')
@admin_required
def staff_invitations_data():
    q = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', 'all')
    if status not in ('all', 'Invited', 'Accepted', 'Expired', 'Removed'):
        abort(400)
    now = datetime.utcnow()
    page = data.invitation_query(q, status, now).paginate(page=request.args.get('page', 1, type=int), per_page=25, error_out=False)
    return jsonify(invitations=data.invitations(page.items, now), page=page.page, pages=page.pages, total=page.total)


@admin_bp.get('/staff-invitations/export.csv')
@admin_required
def staff_invitations_export():
    status = request.args.get('status', 'all')
    if status not in ('all', 'Invited', 'Accepted', 'Expired', 'Removed'):
        abort(400)
    q, now = request.args.get('q', '').strip()[:100], datetime.utcnow()
    def batches():
        cursor = None
        while True:
            query = data.invitation_query(q, status, now).filter(StaffInvitation.created_at <= now)
            if cursor:
                query = query.filter(or_(StaffInvitation.created_at < cursor[0],
                    and_(StaffInvitation.created_at == cursor[0], StaffInvitation.id < cursor[1])))
            rows = query.limit(100).all()
            if not rows:
                return
            yield [[r['email'], r['invited_by']['name'], ', '.join(b['name'] for b in r['businesses']),
                    r['role'], r['sent'], r['status'], r['email_delivery']] for r in data.invitations(rows, now)]
            cursor = (rows[-1].created_at, rows[-1].id)
    return csv_response('StockBridge-staff-invitations.csv', ['Invited email', 'Invited by', 'Business', 'Role', 'Sent', 'Status', 'Email delivery'], batches)


def invitation(identifier):
    return data.invitation_query().filter(StaffInvitation.id == identifier).first_or_404()


@admin_bp.post('/staff-invitations/<int:invitation_id>/email')
@admin_required
def reveal_invitation_email(invitation_id):
    row = invitation(invitation_id)
    log('ADMIN_EMAIL_REVEALED', f'Full email revealed for staff invitation {row.id}.', actor=current_user,
        target_type='staff', target_id=row.id)
    db.session.commit()
    return jsonify(email=row.email)


@admin_bp.post('/staff-invitations/<int:invitation_id>/<action>')
@admin_required
def manage_staff_invitation(invitation_id, action):
    if action not in ('resend', 'cancel'):
        abort(404)
    reason = text(payload(), 'reason', 300)
    row = invitation(invitation_id)
    owner = data.customer(row.owner_id)
    try:
        if action == 'resend':
            replacement, raw = service.resend(owner, row.id, now=datetime.utcnow(),
                                               actor=current_user._get_current_object(), administrator=True)
            replacement_id = replacement.id
        else:
            service.administrative_revoke(owner, 'invitation', row.id, current_user._get_current_object(), now=datetime.utcnow())
        log('ADMIN_STAFF_' + action.upper(), 'Staff invitation action. Reason: ' + reason,
            actor=current_user, target_type='staff', target_id=row.id)
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 409
    if action == 'resend':
        status = delivery.deliver(replacement_id, raw, actor_id=current_user.id)
        return jsonify(status=status, invitation_id=replacement_id)
    return jsonify(status='Removed')


@admin_bp.post('/staff-memberships/<int:membership_id>/remove')
@admin_required
def remove_staff_membership(membership_id):
    reason = text(payload(), 'reason', 300)
    row = StaffMembership.query.filter_by(id=membership_id).first_or_404()
    owner = data.customer(row.owner_id)
    try:
        service.administrative_revoke(owner, 'membership', row.id, current_user._get_current_object(), now=datetime.utcnow())
        log('ADMIN_STAFF_REMOVED', 'Staff membership removed. Reason: ' + reason,
            actor=current_user, target_type='staff', target_id=row.id)
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 409
    return jsonify(status='Removed')


@admin_bp.get('/users/<int:user_id>/read-only')
@admin_required
def inspect_customer(user_id):
    user = data.customer(user_id)
    now = datetime.utcnow()
    if user.staff_only:
        memberships = data.memberships_for(user, now)
        business_ids = {b['id'] for member in memberships if member['access_active'] for b in member['businesses']}
    else:
        business_ids = {b.id for b in Business.query.filter_by(user_id=user.id)}
    selected = request.args.get('business_id', type=int)
    if selected is not None and selected not in business_ids:
        abort(404)
    selected = selected if selected is not None else min(business_ids, default=None)
    shop = db.session.get(Business, selected) if selected is not None else None
    dataset = request.args.get('dataset', 'products')
    model = {'products': Product, 'sales': Sale, 'restocking': Restock, 'expenses': Expense}.get(dataset)
    if model is None:
        abort(400)
    query = model.query.filter_by(business_id=selected).order_by(model.id.desc())
    if dataset == 'sales':
        query = query.options(selectinload(Sale.items))
    elif dataset == 'restocking':
        query = query.options(joinedload(Restock.product))
    page = query.paginate(page=request.args.get('page', 1, type=int), per_page=25, error_out=False) if shop else None
    # Dedicated admin projection. Keep the current admin identity and session.
    log('ADMIN_READ_ONLY_OPENED', f'Admin inspection opened for account {user.id}.', actor=current_user,
        business_id=selected, target_type='user', target_id=user.id)
    db.session.commit()
    return render_template('admin/customer_read_only.html', customer=data.details(user, now), shop=shop,
        rows=page, dataset=dataset, local_time=data.local_time,
        businesses=Business.query.filter(Business.id.in_(business_ids)).order_by(Business.id).all())
