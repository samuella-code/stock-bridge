"""Owner-scoped role configuration, never operational authorization by name.

Operational permissions remain stored for the later workflow patch. can_operate
stays false. Delegated team administration is explicit and bounded; export
authority is stored but no Staff export workflow is enabled.
"""
from datetime import datetime
import secrets
from app import db
from app.models import StaffRole, StaffMembership, StaffInvitation, StaffAuditEvent, AuditLog

MAX_ACTIVE_ROLES = 5

CATALOGUE = {
    'sales_view': 'View recorded sales',
    'sales_create': 'Record sales',
    'inventory_view': 'View products and stock',
    'products_create': 'Create products',
    'products_edit': 'Edit product details',
    'restocking_create': 'Record restocking',
    'restocking_view': 'View restocking receipts and history',
    'expenses_view': 'View operating expenses',
    'expenses_create': 'Record operating expenses',
    'financial_view': 'View financial reports',
}
TEMPLATES = {
    'manager': ('Manager', ('sales_view', 'sales_create', 'inventory_view', 'products_create', 'products_edit', 'restocking_create', 'restocking_view', 'expenses_view', 'expenses_create', 'financial_view')),
    'salesperson': ('Salesperson/Cashier', ('sales_view', 'sales_create', 'inventory_view')),
    'inventory_staff': ('Inventory/Restocking Officer', ('inventory_view', 'products_create', 'products_edit', 'restocking_create', 'restocking_view')),
}


def validate_permissions(values):
    from app.staff.delegation import CATALOGUE as delegated_catalogue
    if not isinstance(values, (list, tuple)) or any(type(v) is not str or v not in (CATALOGUE | delegated_catalogue) for v in values):
        raise ValueError('Choose only permissions from the staff catalogue.')
    if len(values) != len(set(values)):
        raise ValueError('Duplicate permissions are invalid.')
    return sorted(values)


def options(owner_id):
    rows = StaffRole.query.filter_by(owner_id=owner_id).order_by(StaffRole.key).all()
    if not rows:
        # GETs display starting choices without inserting anything.
        return {key: dict(name=name, permissions=list(perms)) for key, (name, perms) in TEMPLATES.items()}
    return {row.key: dict(name=row.name, permissions=validate_permissions(row.permissions)) for row in rows if not row.archived}


def active_count(owner_id):
    return StaffRole.query.filter_by(owner_id=owner_id, archived=False).count()


def _initialize_locked(owner_id):
    # Caller must hold the common owner lock and have verified paid Pro.
    # This private initializer never commits: templates and custom creation are atomic.
    if not StaffRole.query.filter_by(owner_id=owner_id).first():
        if len(TEMPLATES) > MAX_ACTIVE_ROLES:
            raise ValueError('Starting roles exceed the role limit.')
        for key, (name, perms) in TEMPLATES.items():
            row = StaffRole(owner_id=owner_id, key=key, name=name, permissions=list(perms), archived=False)
            db.session.add(row)
            record(owner_id, 'STAFF_ROLE_CREATED', None, dict(key=key, name=name, permissions=list(perms)))
        db.session.flush()


def resolve(owner_id, key):
    if not isinstance(key, str): raise ValueError('Choose an owned staff role.')
    row = StaffRole.query.filter_by(owner_id=owner_id, key=key, archived=False).first()
    if not row: raise ValueError('Choose an owned staff role.')
    validate_permissions(row.permissions)
    return row


def record(owner_id, action, before, after, *, actor_id=None):
    log = AuditLog(actor_id=owner_id if actor_id is None else actor_id, action=action, target_type='staff_role',
        description='Staff role change; structured details retained.')
    db.session.add(log); db.session.flush()
    db.session.add(StaffAuditEvent(audit_log_id=log.id, owner_id=owner_id, details=dict(before=before, after=after)))


def facts(row):
    return dict(key=row.key, name=row.name, permissions=validate_permissions(row.permissions), archived=row.archived)


def save(owner, key, name, permissions, *, now, actor=None, authority_permissions=None):
    from app.staff.service import lock_owner, owner_access
    lock_owner(owner.id); now = max(now, datetime.utcnow()); owner_access(owner, now=now)
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError('Enter a role name of 1–80 characters.')
    from app.staff import delegation_access as delegated
    from app.staff.delegation import OWNER_GRANTED
    actor = owner if actor is None else actor
    auth, _ = delegated.mutation(owner,actor,now=now)
    if auth.owner: _initialize_locked(owner.id)
    previous = resolve(owner.id,key).permissions if key else []
    # Existing ordinary owner form edits cannot silently strip sensitive grants;
    # explicit owner-only authority checkboxes live on the delegation page.
    permissions = validate_permissions(permissions)
    if authority_permissions is not None:
        if not auth.owner: raise ValueError('Only the owner grants administration or exports.')
        if not isinstance(authority_permissions,(list,tuple)) or len(authority_permissions)!=len(set(authority_permissions)) or not set(authority_permissions)<=OWNER_GRANTED:
            raise ValueError('Choose valid owner-granted authorities.')
        permissions = sorted((set(permissions)-OWNER_GRANTED)|set(authority_permissions))
    elif auth.owner:
        permissions = sorted(set(permissions)|(set(previous)&OWNER_GRANTED))
    delegated.shared(auth,key,previous,permissions)
    if key is None:
        if active_count(owner.id) >= MAX_ACTIVE_ROLES:
            raise ValueError('All five active role slots are occupied.')
        row = StaffRole(owner_id=owner.id, key=secrets.token_hex(16), name=name.strip(), permissions=permissions, archived=False)
        before = None; db.session.add(row)
    else:
        row = resolve(owner.id, key); before = facts(row)
        row.name, row.permissions = name.strip(), permissions
    db.session.flush()
    record(owner.id, 'STAFF_ROLE_CHANGED' if before else 'STAFF_ROLE_CREATED', before, facts(row), actor_id=actor.id)
    return row


def remove(owner, key, replacement, *, now, actor=None):
    from app.staff.service import lock_owner, owner_access
    lock_owner(owner.id); now = max(now, datetime.utcnow()); owner_access(owner, now=now)
    actor = owner if actor is None else actor
    from app.staff import delegation_access as delegated
    auth, _ = delegated.mutation(owner,actor,now=now)
    if auth.owner: _initialize_locked(owner.id)
    row = resolve(owner.id, key)
    members = StaffMembership.query.filter_by(owner_id=owner.id, role=key, state='active').all()
    pending = StaffInvitation.query.filter(StaffInvitation.owner_id == owner.id,
        StaffInvitation.role == key, StaffInvitation.state == 'pending', StaffInvitation.expires_at > now).all()
    target = resolve(owner.id, replacement) if replacement else None
    if target and target.key == key: raise ValueError('Choose a different replacement role.')
    delegated.shared(auth,key,row.permissions,target.permissions if target else [])
    if (members or pending) and not target:
        raise ValueError('Reassign affected memberships and pending invitations first.')
    # Preserve historical role identity instead of deleting referenced audit history.
    for model_rows, kind in ((members, 'membership'), (pending, 'invitation')):
        for item in model_rows:
            item.role = target.key
            record(owner.id, 'STAFF_ROLE_REASSIGNED', dict(kind=kind, id=item.id, role=key),
                   dict(kind=kind, id=item.id, role=target.key), actor_id=actor.id)
    before = facts(row); row.archived = True
    record(owner.id, 'STAFF_ROLE_REMOVED', before, facts(row), actor_id=actor.id)
