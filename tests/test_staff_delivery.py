"""Mock-only staff email delivery and explicit replacement lifecycle."""
from datetime import timedelta
import hashlib
import json
from unittest.mock import patch
import pytest
from app import db
from app.models import StaffInvitation, StaffRole, StaffAuditEvent, AuditLog, User, Business, AccountBilling
from app.staff import delivery, roles, service
from tests.test_staff_foundation import app, seed, issue, accept_new, login, NOW, staff_clock


def policy():
    sha = 'a'*40
    return (dict(STAFF_INVITATION_EMAIL_ENABLED='true', STAFF_INVITATION_EMAIL_APPROVED_SHA=sha,
        STAFF_INVITATION_EMAIL_ORIGIN='https://'+delivery.STAGING_HOST,
        STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS='staff@example.invalid'),
        dict(VERCEL='1', VERCEL_PROJECT_ID=delivery.STAGING_PROJECT_ID, VERCEL_ENV='preview',
             VERCEL_TARGET_ENV='staging', VERCEL_GIT_COMMIT_REF='release/staging-phase6', VERCEL_GIT_COMMIT_SHA=sha))


@pytest.mark.parametrize('key,value', [('STAFF_INVITATION_EMAIL_ENABLED',''),
    ('STAFF_INVITATION_EMAIL_ENABLED','false'), ('STAFF_INVITATION_EMAIL_ENABLED',True),
    ('STAFF_INVITATION_EMAIL_ENABLED','TRUE'), ('STAFF_INVITATION_EMAIL_ENABLED',' true'),
    ('STAFF_INVITATION_EMAIL_ORIGIN','https://foreign.example.invalid'),
    ('STAFF_INVITATION_EMAIL_APPROVED_SHA','b'*40),
    ('STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS',''),
    ('STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS','*'),
    ('STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS','Staff@example.invalid')])
def test_disabled_malformed_and_unapproved_policy_never_calls_transport(app, key, value):
    owner, shops = seed('pro'); row, raw = issue(owner, shops)
    config, environ = policy(); config[key] = value; app.config.update(config)
    with patch.dict('os.environ', environ, clear=True), patch('app.staff.delivery.email_service._send_email') as send:
        result = delivery.deliver(row.id, raw)
    assert result in ('disabled','configuration_required') and not send.called
    assert service.seat_count(owner.id, now=NOW) == 1


@pytest.mark.parametrize('field', ['VERCEL','VERCEL_PROJECT_ID','VERCEL_ENV','VERCEL_TARGET_ENV',
    'VERCEL_GIT_COMMIT_REF','VERCEL_GIT_COMMIT_SHA'])
@pytest.mark.parametrize('value', [None, 'wrong'])
def test_missing_or_wrong_identity_never_sends(app, field, value):
    owner, shops = seed('pro'); row, raw = issue(owner, shops)
    config, environ = policy(); app.config.update(config)
    if value is None: environ.pop(field)
    else: environ[field] = value
    with patch.dict('os.environ', environ, clear=True), patch('app.staff.delivery.email_service._send_email') as send:
        assert delivery.deliver(row.id, raw) == 'configuration_required'
    assert not send.called


@pytest.mark.parametrize('outcome', ['success','not-configured','exception'])
def test_committed_digest_before_mock_smtp_results_and_no_secret_persistence(app, outcome, caplog):
    owner, shops = seed('pro'); row, raw = issue(owner, shops)
    config, environ = policy(); app.config.update(config)
    before = (User.query.count(), Business.query.count(), AccountBilling.query.count())
    def send(subject, recipient, body):
        assert not db.session().in_transaction()  # owner lock released before transport
        assert recipient == 'staff@example.invalid'
        assert ('https://'+delivery.STAGING_HOST+'/staff/invite#token='+raw) in body
        if outcome == 'exception': raise RuntimeError(raw)
        return outcome == 'success'
    with patch.dict('os.environ', environ, clear=True), patch('app.staff.delivery.email_service._send_email', side_effect=send) as transport:
        result = delivery.deliver(row.id, raw)
        assert delivery.deliver(row.id, raw) == 'suppressed'  # no automatic retry
    assert transport.call_count == 1
    assert result == {'success':'sent','not-configured':'failed','exception':'unknown'}[outcome]
    assert db.session.get(StaffInvitation,row.id).delivery_status == result
    assert before == (User.query.count(), Business.query.count(), AccountBilling.query.count())
    assert raw not in caplog.text
    for event in StaffAuditEvent.query.all(): assert raw not in json.dumps(event.details)
    assert all(raw not in str(getattr(row,c.name)) for c in row.__table__.columns)


def test_resend_at_five_seats_replaces_digest_and_invalidates_old_link(app):
    owner, shops = seed('pro'); old, raw = issue(owner, shops)
    for n in range(4): issue(owner, shops, email=f'other-{n}@example.invalid')
    old.delivery_status = 'failed'; db.session.commit()
    replacement, new_raw = service.resend(owner, old.id, now=NOW); db.session.commit()
    assert old.state == 'revoked' and old.token_digest != replacement.token_digest
    assert replacement.token_digest == hashlib.sha256(new_raw.encode()).hexdigest()
    assert replacement.email == old.email and replacement.role == old.role
    assert service.seat_count(owner.id, now=NOW) == 5
    with pytest.raises(ValueError): service.invitation_for(raw, now=NOW)
    assert service.invitation_for(new_raw, now=NOW).id == replacement.id
    user, _ = accept_new(new_raw)
    with pytest.raises(ValueError): service.resend(owner, replacement.id, now=NOW)
    db.session.rollback()
    assert service.seat_count(owner.id, now=NOW) == 5 and user.staff_only


def test_expired_resend_cannot_take_sixth_seat_and_rolls_back_revocation(app):
    owner, shops = seed('pro'); old, _ = issue(owner, shops)
    old.expires_at = NOW; db.session.commit()
    for n in range(5): issue(owner, shops, email=f'full-{n}@example.invalid')
    with pytest.raises(ValueError): service.resend(owner, old.id, now=NOW)
    db.session.rollback(); assert old.state == 'pending'
    assert service.seat_count(owner.id, now=NOW) == 5


def test_in_flight_and_crashed_delivery_have_no_automatic_retry(app):
    owner, shops = seed('pro'); row, raw = issue(owner, shops)
    row.delivery_status, row.delivery_attempted_at = 'sending', NOW; db.session.commit()
    with pytest.raises(ValueError): service.resend(owner, row.id, now=NOW+timedelta(seconds=119))
    db.session.rollback()
    replacement, _ = service.resend(owner, row.id, now=NOW+timedelta(minutes=2)); db.session.commit()
    delivery.finish(row.id, hashlib.sha256(raw.encode()).hexdigest(), 'sent')
    assert row.state == 'revoked' and replacement.state == 'pending'
    assert service.seat_count(owner.id, now=NOW) == 1


@pytest.mark.parametrize('case', ['recipient','revoked','expired','downgrade','role-archived','wrong-token'])
def test_delivery_revalidates_current_invitation_and_gate(app, case):
    owner, shops = seed('pro'); row, raw = issue(owner, shops)
    config, environ = policy(); app.config.update(config)
    if case == 'recipient': app.config['STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS'] = 'other@example.invalid'
    elif case == 'revoked': service.revoke(owner,'invitation',row.id,now=NOW)
    elif case == 'expired': row.expires_at = NOW
    elif case == 'downgrade': owner.suspended_at = NOW
    elif case == 'role-archived': roles.resolve(owner.id,row.role).archived = True
    else: raw = 'B'*43
    db.session.commit()
    with patch.dict('os.environ', environ, clear=True), patch('app.staff.delivery.email_service._send_email') as send:
        assert delivery.deliver(row.id, raw) == 'suppressed'
    assert not send.called


def test_owner_http_response_contains_status_only_and_resend_requires_owner_csrf(app):
    owner, shops = seed('pro'); client = app.test_client(); login(client, owner)
    config, environ = policy(); app.config.update(config); captured=[]
    def send(subject, recipient, body): captured.append(body); return True
    with patch.dict('os.environ', environ, clear=True), patch('app.staff.delivery.email_service._send_email', side_effect=send):
        response = client.post('/staff/manage', data=dict(action='invite', email='staff@example.invalid', role='manager', business_ids=[shops[0].id]))
    assert response.status_code == 200 and b'Invitation email status: sent' in response.data
    secret = captured[0].split('#token=')[1].split('\n')[0]
    assert secret.encode() not in response.data and b'#token=' not in response.data
    with client.session_transaction() as state: assert secret not in json.dumps(dict(state))
    row = StaffInvitation.query.filter_by(owner_id=owner.id).one()
    other, _ = seed('pro',email='other-owner@example.invalid'); login(client,other)
    assert client.post('/staff/manage',data=dict(action='resend',id=row.id)).status_code == 400
    login(client,owner); app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/staff/manage',data=dict(action='resend',id=row.id)).status_code == 400


def test_role_limit_and_initialization_are_inside_common_owner_lock(app):
    owner, _ = seed('pro'); observed=[]
    real = service.lock_owner
    def lock(identifier): real(identifier); observed.append('locked')
    real_initialize = roles._initialize_locked
    def initialize(identifier):
        assert observed == ['locked']; observed.append('initialize'); return real_initialize(identifier)
    with patch('app.staff.service.lock_owner',side_effect=lock), patch('app.staff.roles._initialize_locked',side_effect=initialize):
        roles.save(owner,None,'Fourth',[],now=NOW)
    db.session.commit(); assert roles.active_count(owner.id)==4
    roles.save(owner,None,'Fifth',[],now=NOW); db.session.commit()
    for name in ['Sixth','Seventh']:
        with pytest.raises(ValueError): roles.save(owner,None,name,[],now=NOW)
        db.session.rollback()
    assert roles.active_count(owner.id)==5


def test_http_resend_emails_replacement_only_without_adding_a_seat(app):
    owner, shops = seed('pro'); old, raw = issue(owner, shops)
    client = app.test_client(); login(client, owner)
    config, environ = policy(); app.config.update(config); bodies=[]
    with patch.dict('os.environ',environ,clear=True), patch('app.staff.delivery.email_service._send_email',
            side_effect=lambda subject,recipient,body: bodies.append((recipient,body)) or False):
        response = client.post('/staff/manage',data=dict(action='resend',id=old.id))
    assert response.status_code==200 and b'Invitation email status: failed' in response.data
    assert b'#token=' not in response.data and old.state=='revoked'
    assert bodies[0][0]==old.email and raw not in bodies[0][1]
    assert service.seat_count(owner.id,now=NOW)==1
    new=StaffInvitation.query.filter_by(owner_id=owner.id,state='pending').one()
    token=bodies[0][1].split('#token=')[1].split('\n')[0]
    assert new.token_digest==hashlib.sha256(token.encode()).hexdigest()
    assert service.invitation_for(token,now=NOW).id==new.id


@pytest.mark.parametrize('reason',['foreign-owner','downgrade','business-suspended'])
def test_replacement_revalidates_owner_and_assignments_atomically(app,reason):
    owner,shops=seed('pro');old,raw=issue(owner,shops)
    actor=owner
    if reason=='foreign-owner': actor,_=seed('pro',email='unrelated@example.invalid')
    elif reason=='downgrade': owner.suspended_at=NOW
    else: shops[0].suspended_at=NOW
    db.session.commit()
    with pytest.raises(ValueError): service.resend(actor,old.id,now=NOW)
    db.session.rollback()
    assert old.state=='pending' and old.token_digest==hashlib.sha256(raw.encode()).hexdigest()
    assert StaffInvitation.query.filter_by(owner_id=owner.id).count()==1


def test_delivery_schema_is_postgresql_compilable_without_migration(app):
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    ddl=str(CreateTable(StaffInvitation.__table__).compile(dialect=postgresql.dialect()))
    assert "delivery_status VARCHAR(24) DEFAULT 'pending' NOT NULL" in ddl
    assert 'delivery_attempted_at TIMESTAMP WITHOUT TIME ZONE' in ddl
    assert 'ck_staff_delivery_status' in ddl and 'configuration_required' in ddl


def test_acceptance_before_delivery_completion_updates_status_without_reopening_invite(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops)
    config,environ=policy();app.config.update(config)
    def send(subject,recipient,body):
        # Deterministic interleaving, not a real database-concurrency claim.
        service.accept(raw,None,'New staff','staff-password-123','staff-password-123',now=NOW)
        db.session.commit()
        return True
    with patch.dict('os.environ',environ,clear=True), patch('app.staff.delivery.email_service._send_email',side_effect=send):
        assert delivery.deliver(row.id,raw)=='sent'
    assert row.state=='accepted' and row.delivery_status=='sent'
    assert service.seat_count(owner.id,now=NOW)==1
    with pytest.raises(ValueError): service.invitation_for(raw,now=NOW)


@pytest.mark.parametrize('environment,target', [
    ('preview','staging'), ('production','production'), ('production','staging'),
    ('preview','production'), ('preview','preview'), ('staging','staging'),
    ('preview','unknown'), ('unknown','staging'), ('','staging'), ('preview','')])
def test_documented_staging_contract_and_rejected_identities(environment,target):
    config,environ=policy()
    environ['VERCEL_ENV']=environment;environ['VERCEL_TARGET_ENV']=target
    expected='ready' if (environment,target)==('preview','staging') else 'configuration_required'
    assert delivery.gate('staff@example.invalid',config,environ)==expected
