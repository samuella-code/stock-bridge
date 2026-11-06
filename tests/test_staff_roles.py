"""Owner-defined roles: isolated in-memory data, no operational delegation."""
import json
from unittest.mock import patch
import pytest
from sqlalchemy.schema import CreateTable
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import StaffRole, StaffMembership, StaffInvitation, StaffAuditEvent, AuditLog
from app.staff import roles, service
from tests.test_staff_foundation import app, seed, issue, accept_new, login, NOW, staff_clock


def test_templates_and_get_are_read_only_and_finance_defaults_off(app):
    owner, shops = seed('pro'); client = app.test_client(); login(client, owner)
    response = client.get('/staff/manage')
    assert response.status_code == 200 and b'Salesperson/Cashier' in response.data
    assert b'Inventory/Restocking Officer' in response.data
    assert StaffRole.query.count() == 0 and AuditLog.query.count() == 0
    assert all('financial_view' not in row['permissions'] for row in roles.options(owner.id).values())
    assert service.seat_count(owner.id, now=NOW) == 0


def test_custom_restocking_role_shared_edits_names_do_not_grant_access(app):
    owner, shops = seed('pro')
    role = roles.save(owner, None, 'Restocking only', ['inventory_view', 'restocking_create', 'restocking_view'], now=NOW)
    db.session.commit(); key = role.key
    for n in range(2):
        _, raw = issue(owner, shops, email=f'custom-{n}@example.invalid', role=key)
        user, member = accept_new(raw)
        assert service.member_businesses(member, user, now=NOW)
        assert not service.can_operate(member, user, 'restocking_create')
    assert service.seat_count(owner.id, now=NOW) == 2
    roles.save(owner, key, 'Owner billing administrator', ['financial_view'], now=NOW)
    db.session.commit()
    assert StaffMembership.query.filter_by(owner_id=owner.id, role=key).count() == 2
    for member in StaffMembership.query.all():
        assert roles.resolve(owner.id, member.role).permissions == ['financial_view']
        assert not service.can_operate(member, 'billing')
    assert role.key == key and role.name == 'Owner billing administrator'
    event = StaffAuditEvent.query.join(AuditLog).filter(AuditLog.action == 'STAFF_ROLE_CHANGED').one()
    assert event.details['before']['permissions'] == ['inventory_view', 'restocking_create', 'restocking_view']
    assert event.details['after']['permissions'] == ['financial_view']


@pytest.mark.parametrize('invalid', [None, 'sales_create', {'sales_create': True}, ['exports'], ['voids'],
    ['stock_adjust'], ['billing'], ['subscriptions'], ['staff_invite'], ['seat_manage'], ['role_manage'],
    ['access_remove'], ['sales_view', 'sales_view'], [True], ['financial_view', 'unknown']])
def test_permission_catalogue_rejects_owner_only_and_malformed_data(app, invalid):
    owner, _ = seed('pro')
    with pytest.raises(ValueError): roles.save(owner, None, 'Manager', invalid, now=NOW)
    db.session.rollback(); assert StaffRole.query.count() == 0


@pytest.mark.parametrize('name', ['', ' '*5, 'X'*81, None])
def test_role_names_are_bounded(app, name):
    owner, _ = seed('pro')
    with pytest.raises(ValueError): roles.save(owner, None, name, [], now=NOW)
    db.session.rollback()


@pytest.mark.parametrize('plan', ['basic', 'plus', 'trial', 'lifetime'])
def test_roles_require_current_paid_pro_owner(app, plan):
    owner, _ = seed(plan)
    with pytest.raises(ValueError): roles.save(owner, None, 'Manager', [], now=NOW)
    db.session.rollback(); assert StaffRole.query.count() == 0


def test_foreign_role_assignment_edit_delete_and_replacement_rejected(app):
    db.session.execute(text('PRAGMA foreign_keys=ON'))
    assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1
    owner, shops = seed('pro'); other, _ = seed('pro', email='foreign-role@example.invalid')
    foreign = roles.save(other, None, 'Foreign', [], now=NOW); db.session.commit()
    for operation in [lambda: issue(owner, shops, role=foreign.key),
        lambda: roles.save(owner, foreign.key, 'Renamed', [], now=NOW),
        lambda: roles.remove(owner, foreign.key, None, now=NOW)]:
        with pytest.raises(ValueError): operation()
        db.session.rollback()
    invitation, raw = issue(owner, shops); _, member = accept_new(raw)
    with pytest.raises(ValueError): roles.remove(owner, member.role, foreign.key, now=NOW)
    db.session.rollback()
    member.role = foreign.key
    # Independent schema enforcement, not just route/service validation.
    with pytest.raises(IntegrityError): db.session.commit()
    db.session.rollback()


def test_remove_requires_atomic_reassignment_preserves_grants_seats_and_history(app):
    owner, shops = seed('pro'); _, raw = issue(owner, shops); _, member = accept_new(raw)
    pending, _ = issue(owner, shops, email='pending-role@example.invalid')
    original = (member.access_until, member.grant_started_at, member.grant_event_id)
    with pytest.raises(ValueError): roles.remove(owner, 'salesperson', None, now=NOW)
    db.session.rollback()
    assert member.role == pending.role == 'salesperson'
    roles.remove(owner, 'salesperson', 'inventory_staff', now=NOW); db.session.commit()
    assert member.role == pending.role == 'inventory_staff'
    assert original == (member.access_until, member.grant_started_at, member.grant_event_id)
    assert service.seat_count(owner.id, now=NOW) == 2
    assert db.session.get(StaffRole, (owner.id, 'salesperson')).archived
    assert 'salesperson' not in roles.options(owner.id)
    assert AuditLog.query.filter_by(action='STAFF_ROLE_REASSIGNED').count() == 2
    roles.save(owner, None, 'Another role', [], now=NOW); db.session.commit()
    assert 'salesperson' not in roles.options(owner.id)  # no template resurrection


def test_role_reassignment_failure_rolls_back_every_change(app):
    owner, shops = seed('pro'); _, raw = issue(owner, shops); _, member = accept_new(raw)
    pending, _ = issue(owner, shops, email='rollback-role@example.invalid')
    with patch('app.staff.roles.record', side_effect=ValueError('synthetic failure')):
        with pytest.raises(ValueError): roles.remove(owner, 'salesperson', 'manager', now=NOW)
    db.session.rollback()
    assert member.role == pending.role == 'salesperson'
    assert not roles.resolve(owner.id, 'salesperson').archived


def test_role_permissions_edit_before_acceptance_and_staff_control_plane_denial(app):
    owner, shops = seed('pro'); invitation, raw = issue(owner, shops)
    roles.save(owner, invitation.role, 'Cash desk', ['sales_view'], now=NOW); db.session.commit()
    user, member = accept_new(raw)
    assert roles.resolve(owner.id, member.role).permissions == ['sales_view']
    client = app.test_client(); login(client, user)
    for data in [dict(action='role_save', role_name='Owner', permissions=['financial_view']),
                 dict(action='role_remove', role_key='manager'), dict(action='invite'), dict(action='revoke')]:
        assert client.post('/staff/manage', data=data).status_code == 403
    assert client.get('/staff/manage').status_code == 403
    assert client.get('/staff/').status_code == 200


def test_role_ui_owner_edit_escaping_csrf_and_maximum_json_schema(app):
    owner, _ = seed('pro'); client = app.test_client(); login(client, owner)
    response = client.post('/staff/manage', data=dict(action='role_save', role_name='<script>'+('X'*60), permissions=list(roles.CATALOGUE)))
    assert response.status_code == 302
    page = client.get('/staff/manage'); assert b'&lt;script&gt;' in page.data
    assert b'Editing a role changes it for every employee and pending invitation using that role.' in page.data
    role = StaffRole.query.filter(StaffRole.key.notin_(roles.TEMPLATES)).one()
    roles.save(owner, role.key, 'N'*80, list(roles.CATALOGUE), now=NOW); db.session.commit()
    event = StaffAuditEvent.query.join(AuditLog).filter(AuditLog.action == 'STAFF_ROLE_CHANGED').one()
    assert len(json.dumps(event.details)) > 300
    assert len(db.session.get(AuditLog, event.audit_log_id).description) <= 300
    ddl = str(CreateTable(StaffRole.__table__).compile(dialect=postgresql.dialect()))
    assert 'permissions JSON NOT NULL' in ddl and 'PRIMARY KEY (owner_id, key)' in ddl
    for model in (StaffMembership, StaffInvitation):
        text = str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert 'FOREIGN KEY(owner_id, role) REFERENCES staff_role (owner_id, key)' in text
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/staff/manage', data=dict(action='role_save', role_name='No token')).status_code == 400


def test_role_and_employee_limits_are_separate_and_archiving_releases_role_slot(app):
    owner, shops = seed('pro')
    keys = [roles.save(owner, None, f'Custom {n}', [], now=NOW).key for n in range(2)]
    db.session.commit()
    assert roles.active_count(owner.id) == 5 and service.seat_count(owner.id, now=NOW) == 0
    with pytest.raises(ValueError): roles.save(owner, None, 'Sixth role', [], now=NOW)
    db.session.rollback()
    roles.save(owner, keys[0], 'Renamed', ['inventory_view'], now=NOW); db.session.commit()
    assert roles.active_count(owner.id) == 5
    for n in range(5): issue(owner, shops, email=f'seat-role-{n}@example.invalid', role=keys[0])
    with pytest.raises(ValueError): issue(owner, shops, email='sixth-role@example.invalid', role=keys[1])
    db.session.rollback()
    assert service.seat_count(owner.id, now=NOW) == 5
    roles.remove(owner, keys[1], None, now=NOW); db.session.commit()
    assert roles.active_count(owner.id) == 4
    roles.save(owner, None, 'Replacement', [], now=NOW); db.session.commit()
    assert roles.active_count(owner.id) == 5
    assert StaffRole.query.filter_by(owner_id=owner.id).count() == 6  # archived history retained


def test_corrupt_stored_permissions_fail_closed_and_role_changes_share_owner_lock(app):
    owner, shops = seed('pro'); _, raw = issue(owner, shops); user, member = accept_new(raw)
    role = roles.resolve(owner.id, member.role)
    role.permissions = ['billing']; db.session.commit()
    assert not service.member_businesses(member, user, now=NOW)
    with patch('app.staff.service.lock_owner', side_effect=ValueError('synthetic lock failure')) as lock:
        with pytest.raises(ValueError): roles.save(owner, None, 'New', [], now=NOW)
        with pytest.raises(ValueError): roles.remove(owner, 'manager', None, now=NOW)
    db.session.rollback(); assert lock.call_count == 2


def test_same_template_key_is_scoped_across_two_memberships(app):
    first, shops = seed('pro'); _, raw = issue(first, shops, role='manager'); user, original = accept_new(raw)
    other, other_shops = seed('pro', email='second-employer@example.invalid')
    roles.save(other, 'manager', 'Second employer lead', ['inventory_view'], now=NOW)
    db.session.commit()
    _, raw = issue(other, other_shops, email=user.email, role='manager')
    _, second = service.accept(raw, user, '', '', '', now=NOW); db.session.commit()
    assert roles.resolve(first.id, original.role).name == 'Manager'
    assert roles.resolve(other.id, second.role).permissions == ['inventory_view']
    assert service.member_businesses(original, user, now=NOW) == [shops[0]]
    assert service.member_businesses(second, user, now=NOW) == [other_shops[0]]
    client = app.test_client(); login(client, user)
    page = client.get('/staff/')
    assert page.status_code == 200 and b'<h2>Manager</h2>' in page.data
    assert b'<h2>Second employer lead</h2>' in page.data


def test_complete_catalogue_defaults_and_restocking_example_are_independent(app):
    expected = {'sales_view', 'sales_create', 'inventory_view', 'products_create',
        'products_edit', 'restocking_create', 'restocking_view', 'expenses_view',
        'expenses_create', 'financial_view'}
    assert set(roles.CATALOGUE) == expected
    assert set(roles.TEMPLATES['manager'][1]) == expected - {'financial_view'}
    assert set(roles.TEMPLATES['inventory_staff'][1]) == {
        'inventory_view', 'products_create', 'products_edit', 'restocking_create', 'restocking_view'}
    assert roles.TEMPLATES['salesperson'] == ('Salesperson/Cashier', ('sales_view', 'sales_create', 'inventory_view'))
    owner, _ = seed('pro')
    choices = roles.options(owner.id)
    roles._initialize_locked(owner.id); db.session.commit()
    for key, option in choices.items():
        assert set(roles.resolve(owner.id, key).permissions) == set(option['permissions'])
        assert 'financial_view' not in option['permissions']
    custom = roles.save(owner, None, 'Restocking only',
        ['inventory_view', 'restocking_create', 'restocking_view'], now=NOW)
    db.session.commit()
    assert not {'products_create', 'products_edit', 'financial_view'} & set(custom.permissions)
    # Initializing later never silently expands customized template permissions.
    roles.save(owner, 'inventory_staff', 'Custom stock role', ['inventory_view'], now=NOW)
    db.session.commit(); roles._initialize_locked(owner.id); db.session.commit()
    assert roles.resolve(owner.id, 'inventory_staff').permissions == ['inventory_view']


@pytest.mark.parametrize('permission', ['products_create', 'products_edit', 'restocking_view'])
def test_new_permissions_validate_independently_and_never_enable_operations(app, permission):
    owner, shops = seed('pro')
    custom = roles.save(owner, None, 'Single permission', [permission], now=NOW)
    db.session.commit()
    assert roles.resolve(owner.id, custom.key).permissions == [permission]
    _, raw = issue(owner, shops, role=custom.key); user, member = accept_new(raw)
    assert service.member_businesses(member, user, now=NOW)
    for operation in roles.CATALOGUE.keys() | {'stock_adjust', 'exports', 'voids', 'billing'}:
        assert not service.can_operate(member, user, operation)
    for malformed in [[permission, permission], [permission, 'stock_adjust'],
                      [permission, 'financial_view_all'], {permission: True}]:
        with pytest.raises(ValueError): roles.validate_permissions(malformed)


def test_rendered_permission_checkboxes_match_starter_defaults(app):
    from html.parser import HTMLParser
    class Forms(HTMLParser):
        def __init__(self):
            super().__init__(); self.current = None; self.rows = {}
        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if tag == 'form': self.current = dict(key=None, permissions=set(), action=None)
            if tag == 'input' and self.current is not None:
                if data.get('name') == 'role_key': self.current['key'] = data.get('value')
                if data.get('name') == 'action': self.current['action'] = data.get('value')
                if data.get('name') == 'permissions' and 'checked' in data:
                    self.current['permissions'].add(data['value'])
        def handle_endtag(self, tag):
            if tag == 'form' and self.current is not None:
                if self.current['action'] == 'role_save':
                    self.rows[self.current['key']] = self.current['permissions']
                self.current = None
    owner, _ = seed('pro'); client = app.test_client(); login(client, owner)
    response = client.get('/staff/manage'); parser = Forms(); parser.feed(response.get_data(as_text=True))
    for key, (_, permissions) in roles.TEMPLATES.items(): assert parser.rows[key] == set(permissions)
    assert parser.rows[''] == set()  # custom role starts with all permissions off
    assert b'Create products' in response.data and b'Edit product details' in response.data
    assert b'View restocking receipts and history' in response.data
    assert b'Editing product details does not allow stock adjustments or unrestricted financial access.' in response.data
