"""Guarded :memory: SQLite and mock-only staff foundation verification.

Real inter-transaction seat races require separately authorized PostgreSQL.
"""
from datetime import datetime, timedelta
import hashlib
import re
import json
import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import event, update
from sqlalchemy.dialects import postgresql
from app import db
from app.models import (User, Business, AccountBilling, BillingEvent, AuditLog, StaffInvitation,
    StaffMembership, StaffMembershipBusiness, RecurringSubscription, StaffAuditEvent, Payment)
from app.staff import service as s
from tests.test_business_insights import app, seed, login, subscription, NOW


@pytest.fixture(autouse=True)
def staff_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls): return NOW
    monkeypatch.setattr('app.staff.service.datetime', Clock)
    monkeypatch.setattr('app.staff.routes.datetime', Clock)
    monkeypatch.setattr('app.staff.roles.datetime', Clock)
    monkeypatch.setattr('app.staff.delivery.datetime', Clock)


def issue(owner,shops,email='staff@example.invalid',role='salesperson',finance=False):
    row,raw=s.invite(owner,email,role,'Evening shift',finance,[shops[0].id],now=NOW)
    db.session.commit();return row,raw


def accept_new(raw,now=NOW,verify=True):
    user,member=s.accept(raw,None,'Synthetic Staff','staff-password-123','staff-password-123',now=now)
    db.session.commit()
    if verify: complete_verification(user)
    return user,member


def complete_verification(user):
    from app.email_service import verification_token
    from flask import current_app
    client = current_app.test_client()
    with patch('app.auth.routes.send_welcome_email',side_effect=AssertionError('No staff welcome mail')):
        result = client.get('/auth/verify/'+verification_token(user.email))
    assert result.status_code == 302 and result.location.endswith('/staff/')
    assert user.email_verified_at is not None


def test_new_staff_accept_has_no_business_billing_trial_and_only_digest(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops)
    assert row.token_digest==hashlib.sha256(raw.encode()).hexdigest() and raw not in row.token_digest
    assert 'financial_view' not in s.role_service.resolve(owner.id,row.role).permissions
    before=(Business.query.count(),AccountBilling.query.count(),BillingEvent.query.count())
    user,member=accept_new(raw,verify=False)
    assert user.email_verified_at is None
    assert not s.member_businesses(member,user,now=NOW)
    complete_verification(user)
    assert user.staff_only and user.role=='user' and user.check_password('staff-password-123')
    assert user.email==row.email and user.email_verified_at is not None
    assert before==(Business.query.count(),AccountBilling.query.count(),BillingEvent.query.count())
    assert s.seat_count(owner.id,now=NOW)==1
    assert row.state=='accepted' and row.membership_id==member.id
    assert [b.id for b in s.member_businesses(member,user,now=NOW)]==[shops[0].id]
    assert not s.can_operate(member,user,'sale')
    assert raw not in '\n'.join(a.description for a in AuditLog.query.all())
    assert {a.action for a in AuditLog.query.all()}>={'STAFF_INVITED','STAFF_ACCEPTED'}


@pytest.mark.parametrize('role',list(s.ROLES))
def test_multiple_seats_may_share_roles_duty_and_explicit_assignments(app,role):
    owner,shops=seed('pro',businesses=2)
    for i in range(2):
        _,raw=issue(owner,shops,email=f'role-{i}@example.invalid',role=role)
        user,member=accept_new(raw)
        assert member.role==role
        assert 'financial_view' not in s.role_service.resolve(owner.id,member.role).permissions
        assert s.member_businesses(member,user,now=NOW)==[shops[0]]
    member=StaffMembership.query.first()
    s.change(owner,member.id,'manager','Stock supervision',False,[shops[1].id],now=NOW)
    db.session.commit()
    assert member.duty_label=='Stock supervision' and member.role=='manager'
    assert 'financial_view' not in s.role_service.resolve(owner.id,member.role).permissions and not s.can_operate(member)
    assert [a.business_id for a in StaffMembershipBusiness.query.filter_by(membership_id=member.id)]==[shops[1].id]
    assert AuditLog.query.filter_by(action='STAFF_CHANGED').count()==1


@pytest.mark.parametrize('invalid',['foreign','locked','suspended','none','role','finance','label'])
def test_assignment_and_role_rejection_is_atomic(app,invalid):
    owner,shops=seed('pro',businesses=6);other,foreign=seed('pro',email='foreign@example.invalid')
    role='manager';finance=False;label='Valid';ids=[shops[0].id]
    if invalid=='foreign':ids=[foreign[0].id]
    elif invalid=='locked':ids=[shops[5].id]
    elif invalid=='suspended':shops[0].suspended_at=NOW
    elif invalid=='none':ids=[]
    elif invalid=='role':role='owner'
    elif invalid=='finance':role='salesperson';finance=True
    else:label='x'*101
    db.session.commit()
    with pytest.raises(ValueError):s.invite(owner,'reject@example.invalid',role,label,finance,ids,now=NOW)
    db.session.rollback();assert StaffInvitation.query.count()==0


@pytest.mark.parametrize('kind',['basic','plus','trial','lifetime','lifetime_plus'])
def test_non_pro_owner_cannot_manage(app,kind):
    owner,shops=seed(kind);client=app.test_client();login(client,owner)
    assert client.get('/staff/manage').status_code==403
    with pytest.raises(ValueError):issue(owner,shops)
    db.session.rollback()


@pytest.mark.parametrize('identity',['anonymous','wrong','unverified','admin','suspended'])
def test_existing_invitee_must_authenticate_exact_verified_identity(app,identity):
    owner,shops=seed('pro');existing=User(full_name='Existing',email='existing@example.invalid',password_hash='unused',email_verified_at=NOW)
    db.session.add(existing);db.session.commit();row,raw=issue(owner,shops,email=existing.email)
    actor=existing
    if identity=='anonymous':actor=None
    elif identity=='wrong':actor=owner
    elif identity=='unverified':existing.email_verified_at=None
    elif identity=='admin':existing.role='admin'
    elif identity=='suspended':existing.suspended_at=NOW
    db.session.commit()
    with pytest.raises(ValueError):s.accept(raw,actor,'','ignored','ignored',now=NOW)
    db.session.rollback();assert row.state=='pending' and StaffMembership.query.count()==0


def test_existing_owner_acceptance_preserves_independent_owner_account(app):
    owner,shops=seed('pro');other,owned=seed('plus',email='other-owner@example.invalid')
    _,raw=issue(owner,shops,email=other.email)
    before=(Business.query.count(),AccountBilling.query.count())
    accepted,member=s.accept(raw,other,'','','',now=NOW);db.session.commit()
    assert accepted.id==other.id and not accepted.staff_only
    assert before==(Business.query.count(),AccountBilling.query.count())
    client=app.test_client();login(client,other)
    assert client.get('/dashboard').status_code==200
    assert client.post(f'/businesses/{shops[0].id}/switch').status_code==404
    assert not s.can_operate(member)


@pytest.mark.parametrize('state',['expired','exact-expiry','replay','revoked','malformed'])
def test_single_use_expiry_revocation_and_digest_validation(app,state):
    owner,shops=seed('pro');row,raw=issue(owner,shops)
    when=NOW
    if state=='expired':when=row.expires_at+timedelta(seconds=1)
    elif state=='exact-expiry':when=row.expires_at
    elif state=='replay':accept_new(raw)
    elif state=='revoked':s.revoke(owner,'invitation',row.id,now=NOW);db.session.commit()
    else:raw='invalid'
    with pytest.raises(ValueError):s.accept(raw,None,'Staff','staff-password-123','staff-password-123',now=when)
    db.session.rollback()


def test_five_seats_pending_acceptance_revocation_and_replacement(app):
    owner,shops=seed('pro');tokens=[issue(owner,shops,email=f'seat{i}@example.invalid') for i in range(5)]
    assert s.seat_count(owner.id,now=NOW)==5
    with pytest.raises(ValueError):issue(owner,shops,email='six@example.invalid')
    db.session.rollback()
    user,member=accept_new(tokens[0][1]);assert s.seat_count(owner.id,now=NOW)==5
    s.revoke(owner,'membership',member.id,now=NOW);db.session.commit()
    assert not s.member_businesses(member,user,now=NOW)
    assert s.seat_count(owner.id,now=NOW)==4
    replacement,raw=issue(owner,shops,email='replacement@example.invalid')
    assert s.seat_count(owner.id,now=NOW)==5
    assert StaffMembership.query.count()==1 and AuditLog.query.filter_by(action='STAFF_REVOKED').count()==1
    with pytest.raises(ValueError):s.reconfirm(owner,member.id,now=NOW)
    db.session.rollback()


def test_expired_invites_release_seats_and_duplicate_emails_cannot_reserve_twice(app):
    owner,shops=seed('pro');row,_=issue(owner,shops)
    with pytest.raises(ValueError):issue(owner,shops,email='STAFF@example.invalid')
    db.session.rollback();row.expires_at=NOW;db.session.commit()
    assert s.seat_count(owner.id,now=NOW)==0
    issue(owner,shops);assert s.seat_count(owner.id,now=NOW)==1


@pytest.mark.parametrize('state',['owner-suspended','member-suspended','business-suspended','locked','expired','downgrade'])
def test_membership_access_fails_closed(app,state):
    owner,shops=seed('pro',businesses=6);_,raw=issue(owner,shops);user,member=accept_new(raw)
    if state=='owner-suspended':owner.suspended_at=NOW
    elif state=='member-suspended':user.suspended_at=NOW
    elif state=='business-suspended':shops[0].suspended_at=NOW
    elif state=='locked':
        StaffMembershipBusiness.query.filter_by(membership_id=member.id).delete()
        db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=shops[5].id))
    elif state=='expired':subscription(owner).current_period_end=NOW
    else:subscription(owner).plan_code='plus'
    db.session.commit()
    assert not s.member_businesses(member,user,now=NOW)
    assert not s.can_operate(member,user)


def test_reupgrade_and_period_extension_need_explicit_reconfirmation(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops);user,member=accept_new(raw)
    original=subscription(owner);original.current_period_end=NOW+timedelta(seconds=1)
    member.access_until=original.current_period_end;db.session.commit()
    # Same subscription extended after its saved grant ends: no silent restoration.
    original.current_period_end=NOW+timedelta(days=30);db.session.commit()
    later=NOW+timedelta(seconds=2)
    assert not s.member_businesses(member,user,now=later)
    s.reconfirm(owner,member.id,now=later);db.session.commit()
    assert s.member_businesses(member,user,now=later)
    # A paid non-Pro generation then a new paid Pro generation does not restore.
    for i,plan in enumerate(['basic','pro']):
        db.session.add(RecurringSubscription(user_id=owner.id,business_id=shops[0].id,plan_code=plan,billing_interval='monthly',amount_kobo=300000 if plan=='basic' else 1000000,provider_plan_code='synthetic',status='active',current_period_start=later+timedelta(seconds=i+1),current_period_end=NOW+timedelta(days=30)))
    db.session.flush()
    for sub in RecurringSubscription.query.filter(RecurringSubscription.id>original.id).all():
        paid_period(owner,sub,sub.current_period_start,sub.current_period_end,sub.current_period_start,sub.current_period_start)
    db.session.commit();later+=timedelta(seconds=4)
    assert not s.member_businesses(member,user,now=later)
    s.reconfirm(owner,member.id,now=later);db.session.commit()
    assert s.member_businesses(member,user,now=later)


def test_owner_only_management_csrf_auditing_and_no_token_persistence(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops);user,member=accept_new(raw)
    client=app.test_client();login(client,user)
    assert client.get('/staff/manage').status_code==403
    assert client.post('/staff/manage',data={'action':'revoke','kind':'membership','id':member.id}).status_code==403
    other,other_shops=seed('pro',email='other-manage@example.invalid');login(client,other)
    response=client.post('/staff/manage',data={'action':'revoke','kind':'membership','id':member.id})
    assert response.status_code==400 and member.state=='active'
    login(client,owner);app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/staff/manage',data={'action':'invite'}).status_code==400
    app.config['WTF_CSRF_ENABLED']=False
    response=client.post('/staff/manage',data={'action':'invite','email':'ui@example.invalid','role':'manager','business_ids':[shops[0].id]})
    assert response.status_code==200 and b'<h2>Email delivery is off</h2>' in response.data
    assert b'#token=' not in response.data
    assert response.headers['Cache-Control']=='private, no-store'
    assert response.headers['Referrer-Policy']=='same-origin'
    with client.session_transaction() as state:
        assert not any('token' in k and k != 'csrf_token' for k in state)
    assert b'#token=' not in client.get('/staff/manage').data



@pytest.mark.parametrize('path',['/dashboard','/reports','/insights','/business-overview','/products/','/sales/','/expenses/','/restocking/','/businesses/','/subscriptions/','/profile/','/notifications/'])
def test_staff_only_no_operational_customer_routes_or_auto_trial(app,path):
    owner,shops=seed('pro');_,raw=issue(owner,shops);user,member=accept_new(raw)
    client=app.test_client();login(client,user)
    assert client.get(path).status_code==403
    assert db.session.get(AccountBilling,user.id) is None and not Business.query.filter_by(user_id=user.id).first()
    assert BillingEvent.query.filter_by(user_id=user.id).count()==0
    assert client.get('/staff/').status_code==200


def test_acceptance_http_secure_new_credentials_and_staff_login(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops);client=app.test_client()
    assert client.get('/staff/invite').status_code==200
    assert client.post('/staff/invite',data={'action':'review','token':raw}).status_code==200
    bad=client.post('/staff/invite',data={'action':'accept','token':raw,'full_name':'Staff','password':'weak','confirm':'weak'})
    assert bad.status_code==400 and User.query.filter_by(email='staff@example.invalid').first() is None
    response=client.post('/staff/invite',data={'action':'accept','token':raw,'full_name':'Staff','password':'staff-password-123','confirm':'staff-password-123'})
    assert response.status_code==302 and response.location.endswith('/auth/verify-pending')
    user=User.query.filter_by(email='staff@example.invalid').one();assert user.staff_only and user.email_verified_at is None
    assert not s.member_businesses(StaffMembership.query.one(),user,now=NOW)
    complete_verification(user)
    client.post('/auth/logout')
    response=client.post('/auth/login',data={'email':user.email,'password':'staff-password-123'})
    assert response.status_code==302 and response.location.endswith('/staff/')


def test_management_gets_are_select_only_and_never_dispatch_mail(app):
    owner,shops=seed('pro');issue(owner,shops)
    client=app.test_client();login(client,owner);statements=[]
    def record(conn,cursor,statement,params,context,many):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:
        with patch('app.email_service._send_email',side_effect=AssertionError('Email forbidden')):
            response=client.get('/staff/manage')
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert response.status_code==200 and all(q.lstrip().upper().startswith('SELECT') for q in statements)


def test_seat_writer_lock_protocol_and_fail_fast_mock(app,monkeypatch):
    owner,shops=seed('pro');seen=[]
    original=s.lock_owner
    def lock(uid):seen.append(('owner-lock',uid));original(uid)
    def count(uid,**kw):
        assert seen and seen[-1]==('owner-lock',uid)
        seen.append(('seat-read',uid));return 0
    monkeypatch.setattr(s,'lock_owner',lock);monkeypatch.setattr(s,'seat_count',count)
    _,raw=issue(owner,shops);seen.clear();accept_new(raw)
    assert [x[0] for x in seen]==['owner-lock','seat-read']
    def rejected(uid):raise RuntimeError('synthetic lock wait failure')
    monkeypatch.setattr(s,'lock_owner',rejected)
    before=StaffInvitation.query.count()
    with pytest.raises(RuntimeError):issue(owner,shops,email='no-retry@example.invalid')
    db.session.rollback();assert StaffInvitation.query.count()==before
    sql=str(update(User).where(User.id==owner.id).values(last_activity_at=User.last_activity_at).compile(dialect=postgresql.dialect()))
    assert 'UPDATE "user"' in sql and 'WHERE "user".id' in sql


def test_migration_is_additive_without_execution(app):
    path=Path(__file__).resolve().parents[1]/'migrations/versions/0018_staff_foundation.py'
    spec=importlib.util.spec_from_file_location('staff_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    class Ops:
        def __getattr__(self,name):
            assert name in {'add_column','create_table','create_index'}
            return lambda *a,**kw:calls.append((name,a,kw))
    with patch.object(module,'op',Ops()):module.upgrade()
    assert module.down_revision=='0017_transaction_submission'
    assert {a[0] for name,a,_ in calls if name=='create_table'}=={'staff_membership','staff_invitation','staff_membership_business','staff_invitation_business','staff_audit_event','staff_role'}
    with pytest.raises(RuntimeError):module.downgrade()


def test_acceptance_rechecks_expiry_after_owner_lock_wait(app,monkeypatch):
    owner,shops=seed('pro');row,raw=issue(owner,shops)
    original=s.lock_owner
    def delayed(uid):
        original(uid)
        class Later(datetime):
            @classmethod
            def utcnow(cls):return row.expires_at
        monkeypatch.setattr(s,'datetime',Later)
    monkeypatch.setattr(s,'lock_owner',delayed)
    with pytest.raises(ValueError):accept_new(raw)
    db.session.rollback()
    assert row.state=='pending' and StaffMembership.query.count()==0


def test_invitation_acceptance_requires_real_csrf_validation(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops);client=app.test_client()
    app.config['WTF_CSRF_ENABLED']=True
    values=dict(action='accept',token=raw,full_name='CSRF staff',password='staff-password-123',confirm='staff-password-123')
    assert client.post('/staff/invite',data=values).status_code==400
    assert StaffMembership.query.count()==0
    html=client.get('/staff/invite').data
    token=re.search(rb'name="csrf_token" value="([^"]+)"',html)[1].decode()
    response=client.post('/staff/invite',data=dict(values,csrf_token=token))
    assert response.status_code==302 and StaffMembership.query.count()==1


def test_pending_invitation_invalid_after_paid_non_pro_generation_even_if_old_pro_returns(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops)
    db.session.add(RecurringSubscription(user_id=owner.id,business_id=shops[0].id,plan_code='basic',billing_interval='monthly',amount_kobo=300000,provider_plan_code='synthetic',status='active',current_period_start=NOW+timedelta(seconds=1),current_period_end=NOW+timedelta(seconds=2)))
    db.session.flush()
    sub=RecurringSubscription.query.order_by(RecurringSubscription.id.desc()).first()
    paid_period(owner,sub,sub.current_period_start,sub.current_period_end,sub.current_period_start,sub.current_period_start)
    db.session.commit()
    with pytest.raises(ValueError):s.accept(raw,None,'New staff','staff-password-123','staff-password-123',now=NOW+timedelta(seconds=3))
    db.session.rollback();assert row.state=='pending' and StaffMembership.query.count()==0


@pytest.mark.parametrize('first,second,expected',[
    ('invite','invite',[True,False]),('accept','invite',[True,False]),
    ('invite','accept',[False,True]),('accept','accept',[True,False])])
def test_bounded_mock_seat_race_model_uses_actual_owner_lock(first,second,expected):
    """Row-lock protocol/ledger model, NOT PostgreSQL transaction evidence."""
    import threading
    from contextlib import nullcontext
    lock=threading.Lock();first_entered=threading.Event();second_attempted=threading.Event()
    second_entered=threading.Event();release=threading.Event();local=threading.local()
    ledger={'active':4,'pending':1 if 'accept' in (first,second) else 0}
    results=[None,None];errors=[]
    class Session:
        no_autoflush=nullcontext()
        def execute(self,statement):
            assert statement.compile().params['id_1']==42
            if threading.current_thread().name=='staff-worker-2':second_attempted.set()
            if not lock.acquire(timeout=2):raise RuntimeError('bounded mock lock timeout')
            local.held=True
            if threading.current_thread().name=='staff-worker-2':second_entered.set()
        def expire_all(self):pass
        def finish(self):
            if getattr(local,'held',False):local.held=False;lock.release()
    fake=Session()
    def worker(index,operation):
        try:
            s.lock_owner(42)
            if index==0:
                first_entered.set()
                assert release.wait(2)
            if operation=='invite':
                results[index]=ledger['active']+ledger['pending']<5
                if results[index]:ledger['pending']+=1
            else:
                results[index]=bool(ledger['pending'])
                if results[index]:ledger['pending']-=1;ledger['active']+=1
            assert ledger['active']+ledger['pending']<=5
        except BaseException as exc:errors.append(type(exc).__name__)
        finally:fake.finish()
    with patch.object(db,'session',fake):
        a=threading.Thread(target=worker,args=(0,first),name='staff-worker-1')
        b=threading.Thread(target=worker,args=(1,second),name='staff-worker-2')
        try:
            a.start();assert first_entered.wait(2)
            b.start();assert second_attempted.wait(2)
            assert not second_entered.is_set()  # first mock transaction still holds owner lock
        finally:
            release.set();a.join(3)
            if b.ident is not None:b.join(3)
        assert not a.is_alive() and not b.is_alive()
    assert not errors and results==expected
    assert ledger['active']+ledger['pending']==5


def paid_period(owner, sub, start, end, paid_at, recorded_at, reference=None):
    reference=reference or f'staff-period-{Payment.query.count()}'
    payment=Payment(user_id=owner.id,business_id=sub.business_id,subscription_id=sub.id,
        product='subscription',plan_code=sub.plan_code,billing_interval=sub.billing_interval,
        customer_email=owner.email,reference=reference,amount_kobo=sub.amount_kobo,
        provider='paystack',currency='NGN',status='success',paid_at=paid_at)
    db.session.add(payment);db.session.flush()
    receipt=BillingEvent(user_id=owner.id,subscription_id=sub.id,kind='provider_event',
        event_key='payment-period:'+hashlib.sha256(reference.encode()).hexdigest(),
        message=json.dumps([start.isoformat(),end.isoformat()],separators=(',',':')),created_at=recorded_at)
    db.session.add(receipt);db.session.flush()
    return payment,receipt


@pytest.mark.parametrize('delivery',['on-time','delayed-on-time','late-payment','gap'])
def test_verified_renewal_continuity_uses_original_period_and_paid_at(app,delivery):
    owner,shops=seed('pro');sub=subscription(owner)
    end=NOW+timedelta(days=1);sub.current_period_end=end;db.session.commit()
    _,raw=issue(owner,shops);user,member=accept_new(raw)
    next_start=end+timedelta(seconds=1) if delivery=='gap' else end
    paid=end+timedelta(seconds=1) if delivery=='late-payment' else end-timedelta(seconds=1)
    recorded=end+timedelta(hours=1) if delivery=='delayed-on-time' else paid+timedelta(microseconds=1)
    sub.current_period_start=next_start;sub.current_period_end=end+timedelta(days=30)
    paid_period(owner,sub,next_start,sub.current_period_end,paid,recorded);db.session.commit()
    assert bool(s.member_businesses(member,user,now=end+timedelta(hours=2))) is (delivery in ('on-time','delayed-on-time'))
    assert member.access_until==end  # read-only, immutable grant anchor; not overwritten by renewal
    if delivery in ('late-payment','gap'):
        s.reconfirm(owner,member.id,now=end+timedelta(hours=2));db.session.commit()
        assert s.member_businesses(member,user,now=end+timedelta(hours=2))


def test_out_of_order_replayed_periods_do_not_multiply_coverage(app):
    owner,shops=seed('pro');sub=subscription(owner);edge=NOW+timedelta(days=1)
    sub.current_period_end=edge;db.session.commit();_,raw=issue(owner,shops);user,member=accept_new(raw)
    # Receive the second invoice first. Both payments were on time for their periods.
    later_end=edge+timedelta(days=60)
    paid_period(owner,sub,edge+timedelta(days=30),later_end,edge+timedelta(days=30),edge+timedelta(days=30))
    _,original=paid_period(owner,sub,edge,edge+timedelta(days=30),edge,edge)
    # Another reference with the same paid interval cannot extend it twice.
    paid_period(owner,sub,edge,edge+timedelta(days=30),edge,edge)
    sub.current_period_start=edge+timedelta(days=30);sub.current_period_end=later_end;db.session.commit()
    from app.staff.lifecycle import coverage
    assert coverage(owner.id,member,now=edge+timedelta(days=45))==later_end
    assert s.member_businesses(member,user,now=edge+timedelta(days=45))
    assert not s.member_businesses(member,user,now=later_end)
    assert original.message==json.dumps([edge.isoformat(),(edge+timedelta(days=30)).isoformat()],separators=(',',':'))


@pytest.mark.parametrize('bad',['missing','malformed','wrong-owner','wrong-subscription','unverified-payment'])
def test_unknown_receipts_cannot_extend_staff_access(app,bad):
    owner,shops=seed('pro');sub=subscription(owner);edge=NOW+timedelta(days=1)
    sub.current_period_end=edge;db.session.commit();_,raw=issue(owner,shops);user,member=accept_new(raw)
    payment,receipt=paid_period(owner,sub,edge,edge+timedelta(days=30),edge,edge)
    if bad=='missing':db.session.delete(receipt)
    elif bad=='malformed':receipt.message='not a paid period'
    elif bad=='wrong-owner':
        other,_=seed('pro',email='wrong-receipt@example.invalid');receipt.user_id=other.id
    elif bad=='wrong-subscription':receipt.subscription_id=sub.id+100
    else:payment.status='initialized'
    sub.current_period_start=edge;sub.current_period_end=edge+timedelta(days=30);db.session.commit()
    assert not s.member_businesses(member,user,now=edge+timedelta(days=1))


def test_staff_verification_flow_does_not_send_or_create_owner_data(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops)
    with patch('app.email_service._send_email',side_effect=AssertionError('No email delivery')):
        user,member=accept_new(raw,verify=False)
        assert user.email_verified_at is None and not s.member_businesses(member,user,now=NOW)
        client=app.test_client();login(client,user)
        assert client.get('/staff/').status_code==302
        pending=client.get('/auth/verify-pending')
        assert pending.status_code==200 and b'Accepting the invitation did not send a verification email.' in pending.data
        complete_verification(user)
        assert s.member_businesses(member,user,now=NOW)
    assert not Business.query.filter_by(user_id=user.id).first()
    assert db.session.get(AccountBilling,user.id) is None
    assert BillingEvent.query.filter_by(user_id=user.id).count()==0


def test_maximum_structured_audit_and_postgres_schema(app):
    from datetime import datetime
    from sqlalchemy.schema import CreateTable
    owner=User(id=2147483647,full_name='Audit owner',email='max-owner@example.invalid',password_hash='unused',email_verified_at=NOW)
    employee=User(id=2147483646,full_name='Audit staff',email='max-staff@example.invalid',password_hash='unused',email_verified_at=NOW,staff_only=True)
    db.session.add_all([owner,employee]);db.session.flush()
    shops=[Business(id=2147483630+i,user_id=owner.id,name=f'Max {i}') for i in range(5)]
    db.session.add_all(shops);db.session.flush()
    sub=RecurringSubscription(id=2147483647,user_id=owner.id,business_id=shops[0].id,plan_code='pro',billing_interval='yearly',amount_kobo=10000000,provider_plan_code='synthetic',status='active',current_period_start=NOW,current_period_end=NOW+timedelta(days=365))
    db.session.add(sub);db.session.flush()
    s.role_service._initialize_locked(owner.id)
    member=StaffMembership(id=2147483647,owner_id=owner.id,user_id=employee.id,role='inventory_staff',duty_label='X'*100,state='active',grant_subscription_id=sub.id,grant_event_id=2147483647,grant_started_at=datetime.min,grant_pro_start=datetime.min,access_until=datetime.max,created_at=NOW)
    db.session.add(member);db.session.flush()
    for b in shops:db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=b.id))
    old=AuditLog(actor_id=owner.id,action='EXISTING_ACTION',description='Z'*300)
    db.session.add(old);db.session.flush()
    s.audit(owner.id,owner.id,'STAFF_CHANGED',membership=member);db.session.commit()
    event_row=StaffAuditEvent.query.join(AuditLog).filter(AuditLog.action=='STAFF_CHANGED').one();log=db.session.get(AuditLog,event_row.audit_log_id)
    assert len(json.dumps(event_row.details))>300
    assert len(log.description)<=300 and old.description=='Z'*300
    assert event_row.details['businesses']==[b.id for b in shops]
    assert 'X'*100 not in json.dumps(event_row.details)
    assert AuditLog.__table__.c.description.type.length==300
    ddl=str(CreateTable(StaffAuditEvent.__table__).compile(dialect=postgresql.dialect()))
    assert 'details JSON NOT NULL' in ddl and 'UNIQUE (audit_log_id)' in ddl
    assert 'FOREIGN KEY(audit_log_id) REFERENCES audit_log (id)' in ddl


def test_continuous_pro_replacement_is_not_bound_to_one_subscription_id(app):
    owner,shops=seed('pro');original=subscription(owner);edge=NOW+timedelta(days=1)
    original.current_period_end=edge;db.session.commit();_,raw=issue(owner,shops);user,member=accept_new(raw)
    replacement=RecurringSubscription(user_id=owner.id,business_id=shops[0].id,plan_code='pro',billing_interval='monthly',amount_kobo=1000000,provider_plan_code='synthetic',status='active',current_period_start=edge,current_period_end=edge+timedelta(days=30))
    db.session.add(replacement);db.session.flush()
    paid_period(owner,replacement,edge,replacement.current_period_end,edge,edge);db.session.commit()
    assert replacement.id != member.grant_subscription_id
    assert s.member_businesses(member,user,now=edge+timedelta(days=1))


def test_staff_social_linking_does_not_bypass_existing_email_verification(app):
    import time
    owner,shops=seed('pro');_,raw=issue(owner,shops);user,member=accept_new(raw,verify=False)
    client=app.test_client()
    with client.session_transaction() as state:
        state['social_pending']={'provider':'google','sub':'synthetic-staff-sub','email':user.email,'expires':time.time()+600}
    with patch('app.auth.social.send_welcome_email',side_effect=AssertionError('No welcome email')):
        response=client.post('/auth/social/finish',data={'password':'staff-password-123'})
    assert response.status_code==302 and response.location.endswith('/auth/verify-pending')
    assert user.email_verified_at is None and not s.member_businesses(member,user,now=NOW)
    assert not Business.query.filter_by(user_id=user.id).first() and db.session.get(AccountBilling,user.id) is None


@pytest.mark.parametrize('newer_basic',[False,True])
def test_delayed_non_pro_receipt_distinguishes_old_plan_from_intervening_downgrade(app,newer_basic):
    owner,shops=seed('pro');pro=subscription(owner)
    # Both receipts arrive after the explicit grant. Only the period ordered
    # after the original Pro period is evidence of an intervening downgrade.
    start=NOW-timedelta(hours=12) if newer_basic else NOW-timedelta(days=2)
    basic=RecurringSubscription(user_id=owner.id,business_id=shops[0].id,plan_code='basic',billing_interval='monthly',amount_kobo=300000,provider_plan_code='synthetic',status='cancelled',current_period_start=start,current_period_end=NOW-timedelta(hours=1))
    db.session.add(basic);db.session.commit()
    _,raw=issue(owner,shops);user,member=accept_new(raw)
    # A backfilled original interval may still overlap the grant timestamp;
    # do not use the subscription's subsequently advanced end as a baseline.
    paid_period(owner,basic,start,NOW+timedelta(days=10),start,NOW+timedelta(seconds=1));db.session.commit()
    assert bool(s.member_businesses(member,user,now=NOW+timedelta(seconds=2))) is (not newer_basic)
    if newer_basic:
        s.reconfirm(owner,member.id,now=NOW+timedelta(seconds=2));db.session.commit()
        assert s.member_businesses(member,user,now=NOW+timedelta(seconds=2))
