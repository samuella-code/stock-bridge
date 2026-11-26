"""Disconnected admin customer regressions, using disposable in-memory data."""
from datetime import datetime, timedelta
import json
import re
from unittest.mock import Mock
import pytest
from sqlalchemy import event
from app import db
from app.models import (AccountBilling, AdminCustomerNote, AdminCustomerMessage, AuditLog, BillingEvent,
                        Business, StaffInvitation, StaffMembership, User)
from app.staff import service
from tests.test_admin_portal import client, seed, admin_login
from tests.test_admin_subscription_reporting import customer


@pytest.fixture
def records(client, monkeypatch):
    monkeypatch.setattr('app.email_service._send_email', Mock(side_effect=AssertionError('Unexpected transport')))
    admin_id, original_id, original_shop = seed(client)
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    owner, shops, sub = customer('pro', businesses=2)
    row, raw = service.invite(owner, 'employee@example.invalid', 'salesperson', '', False, [shops[0].id], now=datetime.utcnow())
    db.session.commit()
    employee, member = service.accept(raw, None, 'Employee', 'staff-password123', 'staff-password123', now=datetime.utcnow())
    employee.email_verified_at = datetime.utcnow()
    pending, _ = service.invite(owner, 'pending@example.invalid', 'manager', '', False, [shops[0].id], now=datetime.utcnow())
    expired, _ = service.invite(owner, 'expired@example.invalid', 'salesperson', '', False, [shops[1].id], now=datetime.utcnow())
    expired.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.session.commit()
    return dict(admin=admin_id, owner=owner.id, employee=employee.id, member=member.id, accepted=row.id,
                pending=pending.id, expired=expired.id, shop=shops[0].id, other_shop=shops[1].id,
                original=original_id, original_shop=original_shop, sub=sub.id)


def login(client):
    assert admin_login(client).status_code == 302


def test_listing_summaries_filters_masks_and_owner_employee_details(client, records):
    login(client)
    response = client.get('/admin/customers/data')
    assert response.status_code == 200
    values = response.json
    assert values['summary'] == dict(owners=2, employees=1, paying=1, trials_ended=0)
    assert {c['id'] for c in client.get('/admin/customers/data?status=owners').json['customers']} == {records['owner'], records['original']}
    employee = client.get('/admin/customers/data?status=employees').json['customers']
    assert [c['id'] for c in employee] == [records['employee']]
    assert employee[0]['plan'] == 'Covered by owner’s plan'
    assert [b['id'] for b in employee[0]['businesses']] == [records['shop']]
    by_shop = client.get('/admin/customers/data?status=employees&q=Metric%20shop').json['customers']
    assert [c['id'] for c in by_shop] == [records['employee']]
    paying = client.get('/admin/customers/data?status=paid').json['customers']
    assert [c['id'] for c in paying] == [records['owner']]
    assert paying[0]['employee_count'] == 1
    for email in ('employee@example.invalid', 'pending@example.invalid', 'expired@example.invalid'):
        assert email.encode() not in response.data
    owner = client.get(f"/admin/users/{records['owner']}/data").json
    assert owner['plan'] == 'Pro' and owner['staff_seats'] == dict(used=2, limit=5)
    assert {i['status'] for i in owner['team']} == {'Accepted', 'Invited', 'Expired'}
    assert owner['paid_so_far_kobo'] == 0
    details = client.get(f"/admin/users/{records['employee']}/data").json
    assert details['paid_so_far_kobo'] is None and details['staff_seats'] is None
    assert details['works_for'][0]['owner_id'] == records['owner']
    assert [b['id'] for b in details['works_for'][0]['businesses']] == [records['shop']]
    assert 'WAT' in details['invitations'][0]['sent']
    assert client.get('/admin/customers/data?status=test-accounts').status_code == 400
    assert not client.get('/admin/customers/data?q=%25').json['customers']


@pytest.mark.parametrize('kind', ['customer', 'invitation'])
def test_reveal_is_audited_before_email_is_returned_and_get_is_rejected(client, records, kind):
    login(client)
    url = f"/admin/users/{records['employee']}/email" if kind == 'customer' else f"/admin/staff-invitations/{records['pending']}/email"
    assert client.get(url).status_code in (403, 405)
    response = client.post(url, json={})
    assert response.status_code == 200 and response.json['email'].endswith('@example.invalid')
    audit = AuditLog.query.filter_by(action='ADMIN_EMAIL_REVEALED').one()
    assert audit.actor_id == records['admin']
    assert response.json['email'] not in audit.description
    assert response.headers['Cache-Control'] == 'no-store, private'


def test_reveal_does_not_disclose_if_audit_commit_fails(client, records, monkeypatch):
    login(client)
    def broken():
        raise RuntimeError('Synthetic failed audit')
    client.application.config['PROPAGATE_EXCEPTIONS'] = False
    monkeypatch.setattr(db.session, 'commit', broken)
    response = client.post(f"/admin/users/{records['employee']}/email", json={})
    assert response.status_code == 500 and b'employee@example.invalid' not in response.data
    db.session.rollback()


def test_notes_compare_versions_and_are_not_in_customer_facing_pages(client, records):
    login(client)
    url = f"/admin/users/{records['owner']}/note"
    assert client.post(url, json=dict(body='Private team note', version=0)).json['version'] == 1
    assert client.post(url, json=dict(body='Stale update', version=0)).status_code == 409
    assert db.session.get(AdminCustomerNote, records['owner']).body == 'Private team note'
    assert client.post(url, json=dict(body='Updated note', version=1)).json['version'] == 2
    assert AuditLog.query.filter_by(action='ADMIN_NOTE_UPDATED').count() == 2
    assert all('Private team note' not in row.description for row in AuditLog.query.all())
    assert client.post(url, json=dict(body='x'*5001, version=2)).status_code == 400


def test_trial_extension_requires_existing_verified_owner_and_exact_previous_end(client, records):
    from app.models import RecurringSubscription
    login(client)
    user = db.session.get(User, records['original'])
    account = AccountBilling(user_id=user.id, trial_started_at=datetime.utcnow()-timedelta(days=9), trial_ends_at=datetime.utcnow()-timedelta(days=2))
    db.session.add(account)
    db.session.commit()
    expected = account.trial_ends_at.isoformat()
    url = f'/admin/users/{user.id}/trial'
    values = dict(days=7, reason='Customer support adjustment', expected_end=expected)
    assert client.post(url, json=values).status_code == 200
    assert datetime.utcnow()+timedelta(days=6) < account.trial_ends_at < datetime.utcnow()+timedelta(days=8)
    assert client.post(url, json=values).status_code == 409
    assert client.post(f"/admin/users/{records['owner']}/trial", json=values).status_code == 409
    assert client.post(f"/admin/users/{records['employee']}/trial", json=values).status_code == 409
    assert AuditLog.query.filter_by(action='ADMIN_TRIAL_EXTENDED').count() == 1
    assert db.session.get(RecurringSubscription, records['sub']).plan_code == 'pro'


@pytest.mark.parametrize('result', ['sent', 'failed', 'unknown'])
def test_customer_message_is_claimed_before_smtp_and_retry_never_resends(client, records, monkeypatch, result):
    login(client)
    calls = []
    def send(subject, recipient, body):
        calls.append((subject, recipient, body))
        assert AdminCustomerMessage.query.count() == 1
        assert AdminCustomerMessage.query.first().status == 'sending'
        if result == 'unknown':
            raise RuntimeError('Transport result unknown')
        return result == 'sent'
    monkeypatch.setattr('app.email_service._send_email', send)
    url = f"/admin/users/{records['owner']}/message"
    values = dict(subject='StockBridge support', body='Requested support reply', request_key='safe-message-request-key')
    assert client.post(url, json=values).json['status'] == result
    assert client.post(url, json=values).json['status'] == result
    assert len(calls) == 1
    assert client.post(url, json={**values, 'body':'Different body'}).status_code == 409
    assert client.post(f"/admin/users/{records['employee']}/message", json=values).status_code == 409
    assert all('Requested support reply' not in log.description for log in AuditLog.query.all())


def test_csv_exports_all_filtered_pages_masks_email_and_neutralizes_formulas(client, records):
    login(client)
    owner = db.session.get(User, records['owner'])
    owner.full_name = '=HYPERLINK("unsafe")'
    db.session.add_all(User(full_name=f'Extra {n}', email=f'extra{n}@example.invalid', password_hash='unused') for n in range(103))
    db.session.commit()
    response = client.get('/admin/customers/export.csv?status=owners')
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert text.count('\r\n') == 106  # header plus 105 owners, across batch boundary
    assert "'=HYPERLINK" in text and 'metric-' not in text
    assert 'employee@example.invalid' not in text and '@example.invalid' not in text
    invitation_csv = client.get('/admin/staff-invitations/export.csv?status=Expired').get_data(as_text=True)
    assert 'Expired' in invitation_csv and 'pending@example.invalid' not in invitation_csv


def test_platform_invitation_filters_and_pagination(client, records):
    login(client)
    assert client.get('/admin/staff-invitations/data').json['total'] == 3
    assert client.get('/admin/staff-invitations/data?status=Expired').json['total'] == 1
    assert client.get('/admin/staff-invitations/data?status=Accepted').json['total'] == 1
    assert client.get('/admin/staff-invitations/data?q=Manager').json['total'] == 1
    assert client.get('/admin/staff-invitations/data?q=Metric%20shop').json['total'] == 3
    assert client.get('/admin/staff-invitations/data?status=invalid').status_code == 400


def test_admin_resend_preserves_policy_and_reports_delivery_without_leaking_token(client, records, monkeypatch):
    login(client)
    calls = []
    def deliver(identifier, raw, *, actor_id):
        assert db.session.get(StaffInvitation, identifier).state == 'pending'
        assert db.session.get(StaffInvitation, records['expired']).state == 'revoked'
        calls.append((identifier, raw, actor_id))
        return 'disabled'
    monkeypatch.setattr('app.staff.delivery.deliver', deliver)
    response = client.post(f"/admin/staff-invitations/{records['expired']}/resend", json=dict(reason='Requested resend'))
    assert response.status_code == 200 and response.json['status'] == 'disabled'
    assert len(calls) == 1 and calls[0][2] == records['admin']
    assert calls[0][1] not in response.get_data(as_text=True)
    assert AuditLog.query.filter_by(action='STAFF_INVITED', target_id=response.json['invitation_id']).one().actor_id == records['admin']
    # The old invitation is consumed and cannot resend again.
    assert client.post(f"/admin/staff-invitations/{records['expired']}/resend", json=dict(reason='Repeated')).status_code == 409
    assert len(calls) == 1


def test_admin_can_remove_after_pro_expires_without_extending_staff_access(client, records):
    from app.models import RecurringSubscription
    login(client)
    db.session.get(RecurringSubscription, records['sub']).current_period_end = datetime.utcnow()-timedelta(seconds=1)
    db.session.commit()
    url = f"/admin/staff-memberships/{records['member']}/remove"
    assert client.post(url, json=dict(reason='Owner requested removal')).status_code == 200
    assert db.session.get(StaffMembership, records['member']).state == 'revoked'
    assert client.post(f"/admin/staff-invitations/{records['pending']}/cancel", json=dict(reason='Cancelled')).status_code == 200
    assert client.post(f"/admin/staff-invitations/{records['accepted']}/cancel", json=dict(reason='Not pending')).status_code == 409


def test_read_only_inspection_preserves_identity_and_customer_data(client, records):
    login(client)
    before = (User.query.count(), Business.query.count(), AccountBilling.query.count(), BillingEvent.query.count())
    response = client.get(f"/admin/users/{records['employee']}/read-only")
    assert response.status_code == 200 and b'Read-only admin view' in response.data
    assert b'Complete sale' not in response.data and b'name="reason"' not in response.data
    assert before == (User.query.count(), Business.query.count(), AccountBilling.query.count(), BillingEvent.query.count())
    with client.session_transaction() as session:
        assert int(session['_user_id']) == records['admin'] and session['admin_session'] is True
    assert client.get(f"/admin/users/{records['employee']}/read-only?business_id={records['other_shop']}").status_code == 404
    assert client.get(f"/admin/users/{records['employee']}/read-only?business_id={records['original_shop']}").status_code == 404
    assert client.post(f"/admin/users/{records['owner']}/read-only").status_code in (403, 405)


@pytest.mark.parametrize('path', ['/admin/customers/data','/admin/customers/export.csv','/admin/staff-invitations/data','/admin/users/{owner}/data'])
def test_customer_tools_reject_non_admin_and_hide_admin_accounts(client, records, path):
    response = client.get(path.format(**records))
    assert response.status_code == 302 and '/admin/login' in response.location
    login(client)
    assert client.get(f"/admin/users/{records['admin']}/data").status_code == 404
    assert client.post(f"/admin/users/{records['admin']}/email",json={}).status_code == 404


def test_employee_billing_reminders_are_never_queued_or_sent(client, records, monkeypatch):
    from app.subscriptions.notifications import queue_due, deliver
    user = db.session.get(User, records['employee'])
    now = datetime.utcnow()
    db.session.add(AccountBilling(user_id=user.id, trial_started_at=now-timedelta(days=9), trial_ends_at=now-timedelta(days=1)))
    db.session.add(BillingEvent(user_id=user.id,event_key='old-staff-reminder',kind='trial_expired',message='Historical'))
    db.session.commit()
    send = Mock(return_value=True)
    monkeypatch.setattr('app.subscriptions.notifications._transactional',send)
    queue_due(now=now)
    assert BillingEvent.query.filter_by(user_id=user.id).count() == 1
    deliver()
    # Other eligible owners may have notices in this shared fixture. The
    # employee's address must never be passed to the customer email transport.
    assert all(call.args[2] != user.email for call in send.call_args_list)


def test_mutation_requires_csrf_and_customer_session_has_no_admin_authority(client, records):
    login(client)
    client.application.config['WTF_CSRF_ENABLED'] = True
    url = f"/admin/users/{records['owner']}/email"
    assert client.post(url,json={}).status_code == 400
    assert not AuditLog.query.filter_by(action='ADMIN_EMAIL_REVEALED').count()
    html = client.get('/admin/users').get_data(as_text=True)
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
    assert client.post(url,json={},headers={'X-CSRFToken':token}).status_code == 200
    client.application.config['WTF_CSRF_ENABLED'] = False
    client.post('/admin/logout')
    with client.session_transaction() as session:
        session['_user_id'] = str(records['original'])
        session['_fresh'] = True
    from flask import g
    g.pop('_login_user', None)
    assert client.post(url,json={}).status_code == 403
    assert client.get('/admin/customers/data').status_code == 403


def test_resend_after_pro_expiry_rolls_back_and_non_admin_cannot_select_admin_service_authority(client, records):
    from app.models import RecurringSubscription
    login(client)
    owner = db.session.get(User, records['owner'])
    with pytest.raises(ValueError,match='Administrator access'):
        service.resend(owner, records['pending'], now=datetime.utcnow(), actor=owner, administrator=True)
    db.session.rollback()
    db.session.get(RecurringSubscription,records['sub']).current_period_end = datetime.utcnow()-timedelta(seconds=1)
    db.session.commit()
    response = client.post(f"/admin/staff-invitations/{records['pending']}/resend",json=dict(reason='Support request'))
    assert response.status_code == 409
    assert db.session.get(StaffInvitation,records['pending']).state == 'pending'


@pytest.mark.parametrize('path,values', [
    ('/admin/users/{owner}/note', {'body':'Blocked', 'version':0}),
    ('/admin/users/{owner}/trial', {'days':7, 'expected_end':'2026-01-01', 'reason':'Blocked'}),
    ('/admin/users/{owner}/message', {'subject':'Blocked','body':'Blocked','request_key':'blocked-message-key'}),
    ('/admin/staff-invitations/{pending}/email', {}),
    ('/admin/staff-invitations/{pending}/resend', {'reason':'Blocked'}),
    ('/admin/staff-invitations/{pending}/cancel', {'reason':'Blocked'}),
    ('/admin/staff-memberships/{member}/remove', {'reason':'Blocked'}),
])
def test_every_customer_mutation_requires_an_admin_session(client, records, path, values):
    before = AuditLog.query.count()
    response = client.post(path.format(**records), json=values)
    assert response.status_code == 302 and '/admin/login' in response.location
    assert AuditLog.query.count() == before
    assert AdminCustomerNote.query.count() == AdminCustomerMessage.query.count() == 0
    assert db.session.get(StaffMembership, records['member']).state == 'active'


def test_customer_migration_matches_models_and_compiles_for_postgres(monkeypatch):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import Column, Integer, MetaData, Table
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    path = Path('migrations/versions/0020_admin_customer_tools.py')
    spec = importlib.util.spec_from_file_location('customer_tools_migration',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = MetaData()
    Table('user',metadata,Column('id',Integer,primary_key=True))
    tables = {}
    class Operations:
        def create_table(self, name, *parts):
            tables[name] = Table(name,metadata,*parts)
    monkeypatch.setattr(module,'op',Operations())
    module.upgrade()  # Only builds metadata through the mocked operations above.
    assert module.down_revision == '0019_admin_authenticator'
    for model in (AdminCustomerNote,AdminCustomerMessage):
        table = tables[model.__tablename__]
        assert set(table.columns.keys()) == set(model.__table__.columns.keys())
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert 'CREATE TABLE' in ddl and 'ON DELETE CASCADE' in ddl
    assert 'uq_admin_message_request' in str(CreateTable(tables['admin_customer_message']).compile(dialect=postgresql.dialect()))
