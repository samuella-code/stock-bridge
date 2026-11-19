"""Read-only, masked activity projections. Audit records remain unchanged."""
import re
from datetime import timedelta, timezone

from sqlalchemy import and_, cast, func, or_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import joinedload

from app import db
from app.admin.customer_data import LAGOS
from app.models import (AuditLog, Business, StaffAuditEvent, StaffInvitation,
                        StaffInvitationBusiness, StaffRole, User)
from app.staff.roles import TEMPLATES

CATEGORIES = [('all', 'All'), ('sales', 'Sales'), ('stock', 'Stock'),
              ('expenses', 'Expenses'), ('staff', 'Staff'), ('security', 'Admin sign-ins')]
LEGACY = {'account': 'Account actions', 'management': 'Account actions',
          'payments': 'Payments', 'admin': 'Admin actions', 'customers': 'Customer events'}
SECURITY = ('ADMIN_LOGIN_SUCCESS', 'ADMIN_LOGIN_FAILED', 'ADMIN_LOGOUT', 'ADMIN_PASSWORD_RESET',
            'ADMIN_MFA_ENROLLED', 'ADMIN_MFA_FAILED', 'ADMIN_MFA_RECOVERY_USED')
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
VERBS = {
    'SALE_CREATED': 'recorded a sale', 'SALE_VOIDED': 'voided a sale', 'SALE_DELETED': 'voided a sale',
    'EXPENSE_CREATED': 'added an expense', 'EXPENSE_VOIDED': 'voided an expense',
    'RESTOCK_CREATED': 'recorded a restock', 'PRODUCT_CREATED': 'added a product',
    'PRODUCT_UPDATED': 'updated a product', 'PRODUCT_IMPORT': 'imported products',
    'STOCK_ADJUSTED': 'adjusted stock', 'USER_REGISTERED': 'created an account',
    'USER_SUSPENDED': 'suspended an account', 'USER_REACTIVATED': 'restored an account',
    'BUSINESS_SUSPENDED': 'suspended a business', 'BUSINESS_REACTIVATED': 'restored a business',
    'ADMIN_LOGIN_SUCCESS': 'signed in to the admin portal', 'ADMIN_LOGIN_FAILED': 'had an unsuccessful admin sign-in',
    'ADMIN_LOGOUT': 'signed out of the admin portal', 'ADMIN_PASSWORD_RESET': 'reset an admin password',
    'ADMIN_MFA_ENROLLED': 'set up an authenticator', 'ADMIN_MFA_FAILED': 'had an unsuccessful verification attempt',
    'ADMIN_MFA_RECOVERY_USED': 'signed in with a recovery code',
    'STAFF_INVITED': 'invited an employee', 'STAFF_ACCEPTED': 'accepted the invitation',
    'STAFF_CHANGED': 'updated employee assignments', 'STAFF_CHANGE_BEFORE': 'reviewed employee assignments',
    'STAFF_REVOKED': 'removed staff access', 'STAFF_RECONFIRMED': 'restored staff access',
    'STAFF_INVITE_REPLACED': 'replaced a staff invitation', 'STAFF_DELIVERY_RESULT': 'recorded invitation delivery',
    'STAFF_ROLE_CREATED': 'created a staff role', 'STAFF_ROLE_CHANGED': 'updated a staff role',
    'STAFF_ROLE_REASSIGNED': 'changed a staff role assignment', 'STAFF_ROLE_REMOVED': 'archived a staff role',
    'ADMIN_EMAIL_REVEALED': 'viewed a full email address', 'ADMIN_NOTE_UPDATED': 'updated an internal note',
    'ADMIN_TRIAL_EXTENDED': 'extended an account trial', 'ADMIN_MESSAGE_REQUESTED': 'requested a customer message',
    'ADMIN_MESSAGE_RESULT': 'recorded a message delivery result',
    'ADMIN_PAYMENT_CHECK_REQUESTED': 'requested a Paystack check',
    'ADMIN_PAYMENT_CHECK_RESULT': 'recorded a Paystack check result',
    'ADMIN_STAFF_RESEND': 'resent a staff invitation', 'ADMIN_STAFF_CANCEL': 'cancelled a staff invitation',
    'ADMIN_STAFF_REMOVED': 'removed staff access',
}


def masked_email(value):
    local, separator, domain = value.partition('@')
    return local[:1] + '***@' + domain if separator and local else '***'


def safe_text(value):
    value = value if isinstance(value, str) else ''
    value = re.sub(r'https?://\S+', '[link hidden]', value)
    value = re.sub(r'(?i)\b(password|token|secret|authorization|credential|api[_ -]?key)\s*[:=]\s*\S+',
                   '[redacted]', value)
    return EMAIL.sub(lambda m: masked_email(m.group()), value)


def staff_business_clause(shop, dialect=None):
    dialect = dialect or db.engine.dialect.name
    if dialect == 'postgresql':
        assigned = cast(StaffAuditEvent.details, JSONB).contains({'businesses': [shop.id]})
    else:
        values = func.json_each(StaffAuditEvent.details, '$.businesses').table_valued('value', 'type')
        assigned = db.session.query(values.c.value).filter(values.c.type == 'integer',
            values.c.value == shop.id).correlate(StaffAuditEvent).exists()
    invitation = db.session.query(StaffInvitation.id).join(StaffInvitationBusiness,
        StaffInvitationBusiness.invitation_id == StaffInvitation.id).filter(
        StaffInvitation.id == StaffAuditEvent.details['invitation_id'].as_integer(),
        StaffInvitation.owner_id == shop.user_id, StaffInvitationBusiness.business_id == shop.id).exists()
    # A snapshot's business list is authoritative when present. Delivery-only
    # records use their explicitly owned invitation; shared role changes are owner-wide.
    fallback = and_(StaffAuditEvent.details['businesses'].as_string().is_(None), invitation)
    return StaffAuditEvent.query.filter(StaffAuditEvent.audit_log_id == AuditLog.id,
        StaffAuditEvent.owner_id == shop.user_id,
        or_(assigned, fallback, AuditLog.target_type == 'staff_role')).exists()


def filtered(q='', action='all', business=None, person=None):
    query = AuditLog.query.options(joinedload(AuditLog.actor), joinedload(AuditLog.business))
    if action == 'security':
        query = query.filter(AuditLog.action.in_(SECURITY))
    elif action in ('sales', 'expenses'):
        query = query.filter(AuditLog.action.startswith('SALE_' if action == 'sales' else 'EXPENSE_', autoescape=True))
    elif action == 'stock':
        query = query.filter(or_(*(AuditLog.action.startswith(prefix, autoescape=True)
                                  for prefix in ('STOCK_', 'RESTOCK_', 'PRODUCT_'))))
    elif action == 'staff':
        query = query.filter(or_(AuditLog.action.startswith('STAFF_', autoescape=True),
                                AuditLog.action.startswith('ADMIN_STAFF_', autoescape=True)))
    elif action in ('account', 'management'):
        query = query.filter(AuditLog.action.in_(('USER_SUSPENDED', 'USER_REACTIVATED', 'BUSINESS_SUSPENDED', 'BUSINESS_REACTIVATED')))
    elif action == 'payments':
        query = query.filter(or_(AuditLog.action.contains('PAYMENT'), AuditLog.action == 'LIFETIME_ACCESS_GRANTED'))
    elif action == 'admin':
        query = query.filter(AuditLog.action.startswith('ADMIN_', autoescape=True))
    elif action == 'customers':
        query = query.filter(or_(AuditLog.action.startswith('USER_', autoescape=True), AuditLog.action == 'LIFETIME_ACCESS_GRANTED'))
    if business:
        query = query.filter(or_(AuditLog.business_id == business.id, staff_business_clause(business)))
    if person is not None:
        query = query.filter(AuditLog.actor_id == person if person != 'system' else AuditLog.actor_id.is_(None))
    if q:
        matching_users = db.session.query(User.id).filter(or_(User.email.icontains(q, autoescape=True), User.full_name.icontains(q, autoescape=True)))
        invited = StaffAuditEvent.query.join(StaffInvitation,
            StaffInvitation.id == StaffAuditEvent.details['invitation_id'].as_integer()).filter(
            StaffAuditEvent.audit_log_id == AuditLog.id, StaffInvitation.owner_id == StaffAuditEvent.owner_id,
            StaffInvitation.email.icontains(q, autoescape=True)).exists()
        query = query.filter(or_(AuditLog.description.icontains(q, autoescape=True), AuditLog.action.icontains(q, autoescape=True),
            AuditLog.actor_id.in_(matching_users), AuditLog.business.has(Business.name.icontains(q, autoescape=True)),
            and_(AuditLog.target_type == 'user', AuditLog.target_id.in_(matching_users)), invited))
    return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


def serialize(events, now):
    events = list(events)
    facts = {r.audit_log_id: r for r in StaffAuditEvent.query.filter(StaffAuditEvent.audit_log_id.in_([e.id for e in events]))}
    details = {key: (r.details if isinstance(r.details, dict) else {}) for key, r in facts.items()}
    ids = {v['invitation_id'] for v in details.values() if type(v.get('invitation_id')) is int}
    invites = {r.id: r for r in StaffInvitation.query.filter(StaffInvitation.id.in_(ids))}
    owner_ids = {r.owner_id for r in facts.values()}
    roles = {(r.owner_id, r.key): r.name for r in StaffRole.query.filter(StaffRole.owner_id.in_(owner_ids))}
    shops = {r.id: r for r in Business.query.filter(Business.user_id.in_(owner_ids))}
    local_today = now.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()
    result = []
    for event in events:
        at = event.created_at.replace(tzinfo=timezone.utc).astimezone(LAGOS)
        day = at.date()
        actor = safe_text(event.actor.full_name) if event.actor else 'System'
        sentence = actor + ' ' + VERBS.get(event.action, 'updated a record')
        data = details.get(event.id, {})
        staff = facts.get(event.id)
        invite = invites.get(data.get('invitation_id')) if type(data.get('invitation_id')) is int else None
        if invite and staff and invite.owner_id == staff.owner_id and event.action == 'STAFF_INVITED':
            key = data.get('role') if isinstance(data.get('role'), str) else ''
            role = TEMPLATES[key][0] if key in TEMPLATES else roles.get((staff.owner_id, key), 'a staff role')
            sentence += ', ' + masked_email(invite.email) + ' as ' + safe_text(role)
        number = None
        if event.target_type in ('sale', 'expense', 'product', 'restock') and event.target_id is not None:
            number = event.target_type.title() + ' #' + str(event.target_id)
        elif event.action == 'EXPENSE_CREATED':
            match = re.fullmatch(r'Expense ([1-9][0-9]*) created\.', event.description)
            number = 'Expense #' + match.group(1) if match else None
        if event.action == 'SALE_CREATED' and number:
            sentence = actor + ' recorded ' + number
        businesses = [safe_text(event.business.name)] if event.business else []
        if not businesses and staff:
            business_ids = data.get('businesses', [])
            if isinstance(business_ids, list):
                businesses = [safe_text(shops[bid].name) for bid in business_ids
                    if type(bid) is int and bid in shops and shops[bid].user_id == staff.owner_id]
        if businesses:
            sentence += ' in ' + ', '.join(businesses)
        result.append(dict(id=event.id, actor=actor, avatar=actor[:1].upper(), sentence=sentence,
            business=', '.join(businesses), number=number, day=day.isoformat(),
            day_label='TODAY' if day == local_today else 'YESTERDAY' if day == local_today-timedelta(days=1) else day.strftime('%d/%m/%Y'),
            date=day.strftime('%d/%m/%Y'), time=at.strftime('%H:%M WAT'), timestamp=at.isoformat(),
            action=safe_text(event.action), target=safe_text(event.target_type.replace('_', ' ').title()) if event.target_type else None,
            target_id=event.target_id, description=safe_text(event.description)))
    return result


def grouped(events):
    groups = []
    for item in events:
        if not groups or groups[-1]['day'] != item['day']:
            groups.append(dict(day=item['day'], label=item['day_label'], events=[]))
        groups[-1]['events'].append(item)
    return groups
