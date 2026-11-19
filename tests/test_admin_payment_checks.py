"""Provider checks use disposable SQLite and mocks; no Paystack requests."""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from app import db
from app.models import AuditLog, Business, Payment, RecurringSubscription, User, BillingEvent
from app.subscriptions import provider
from tests.test_admin_portal import client, admin_login
from tests.test_admin_subscription_payments import records

REAL_CONFIGURED = provider.configured


@pytest.fixture
def check(client, records, monkeypatch):
    row=db.session.get(Payment,records['payment'])
    row.status='pending';row.paid_at=None
    sub=db.session.get(RecurringSubscription,records['subscription'])
    sub.status='checkout_pending';sub.checkout_reference=row.reference
    sub.provider_customer_code='CUS_synthetic'
    db.session.commit()
    payload=dict(status='success',reference=row.reference,amount=row.amount_kobo,currency='NGN',domain='test',
        customer=dict(email='owner@example.com',customer_code='CUS_synthetic'),
        metadata=dict(product='stockbridge_subscription',user_id=row.user_id,subscription_id=sub.id,
            plan_code=row.plan_code,billing_interval=row.billing_interval),
        authorization=dict(secret='NEVER-RETURN-THIS'),gateway_response='PRIVATE PROVIDER TEXT')
    verify=Mock(return_value=payload)
    monkeypatch.setattr(provider,'verify',verify)
    monkeypatch.setattr(provider,'configured',lambda: True)
    client.application.config.update(PAYSTACK_PUBLIC_KEY='pk_test_synthetic',PAYSTACK_SECRET_KEY='sk_test_synthetic',
        SUBSCRIPTIONS_ENABLED=True,BILLING_PROVIDER_ENABLED=True)
    return dict(ids=records,url=f"/admin/payments/{row.id}/check",payload=payload,verify=verify)


def billing_state(ids):
    def columns(model,identifier):
        row=db.session.get(model,identifier)
        return {c.name:getattr(row,c.name) for c in model.__table__.columns}
    return (columns(Payment,ids['payment']),columns(RecurringSubscription,ids['subscription']),
            columns(Business,ids['business']),BillingEvent.query.count())


def test_check_audits_before_provider_and_never_grants_access(client, check):
    admin_login(client)
    before=billing_state(check['ids'])
    def lookup(reference):
        assert AuditLog.query.filter_by(action='ADMIN_PAYMENT_CHECK_REQUESTED',target_id=check['ids']['payment']).count()==1
        assert reference=='SUB-current'
        return check['payload']
    check['verify'].side_effect=lookup
    response=client.post(check['url'],json={})
    assert response.status_code==200 and response.json['outcome']=='matched'
    assert response.json['matches'] is True and response.json['provider_status']=='Success'
    assert 'were not changed' in response.json['message']
    assert 'NEVER-RETURN-THIS' not in response.get_data(as_text=True)
    assert 'PRIVATE PROVIDER TEXT' not in response.get_data(as_text=True)
    assert billing_state(check['ids'])==before
    events=AuditLog.query.filter(AuditLog.action.in_(('ADMIN_PAYMENT_CHECK_REQUESTED','ADMIN_PAYMENT_CHECK_RESULT'))).all()
    assert len(events)==2 and all(e.actor_id==check['ids']['admin'] for e in events)
    assert all('owner@example.com' not in e.description and 'SUB-current' not in e.description for e in events)


@pytest.mark.parametrize('field,value',[
    ('reference','another-payment'),('amount',True),('amount',500001),('currency','USD'),('domain','live'),
    ('customer',{}),('customer',dict(email='other@example.com',customer_code='CUS_synthetic')),
    ('customer',dict(email='owner@example.com',customer_code='CUS_other')),
    ('metadata',{}),('metadata','broken-json'),('metadata',dict(product='stockbridge_subscription',user_id=True)),
])
def test_mismatched_provider_data_never_reported_as_matching(client, check, field, value):
    admin_login(client)
    before=billing_state(check['ids'])
    check['payload'][field]=value
    response=client.post(check['url'],json={})
    assert response.status_code==200 and response.json['outcome']=='needs_review'
    assert response.json['matches'] is False and billing_state(check['ids'])==before


@pytest.mark.parametrize('status',['failed','pending','abandoned','ongoing','processing','queued','reversed'])
def test_provider_status_is_information_only(client, check, status):
    check['payload']['status']=status
    admin_login(client)
    before=billing_state(check['ids'])
    response=client.post(check['url'],json={})
    assert response.status_code==200 and response.json['provider_status']==status.title()
    assert billing_state(check['ids'])==before


@pytest.mark.parametrize('payload',[None,[],{},dict(status='PRIVATE STATUS'),dict(status=['success'])])
def test_malformed_response_is_sanitized(client, check, payload):
    check['verify'].return_value=payload
    admin_login(client)
    response=client.post(check['url'],json={})
    assert response.status_code==200 and response.json['outcome']=='unavailable'
    assert 'PRIVATE' not in response.get_data(as_text=True)


def test_provider_failure_is_sanitized_audited_and_not_automatically_retried(client, check, caplog):
    check['verify'].side_effect=RuntimeError('SECRET PROVIDER CONTENT')
    admin_login(client)
    before=billing_state(check['ids'])
    response=client.post(check['url'],json={})
    assert response.status_code==200 and response.json['outcome']=='unavailable'
    assert 'SECRET PROVIDER CONTENT' not in response.get_data(as_text=True)+caplog.text
    assert check['verify'].call_count==1
    assert AuditLog.query.filter_by(action='ADMIN_PAYMENT_CHECK_RESULT').count()==1
    assert billing_state(check['ids'])==before
    assert client.post(check['url'],json={}).status_code==429
    assert check['verify'].call_count==1


@pytest.mark.parametrize('checkout', [True, False])
def test_checkout_json_metadata_and_renewal_lookup(client, check, checkout):
    import json
    if checkout:
        check['payload']['metadata']=json.dumps(check['payload']['metadata'])
    else:
        db.session.get(RecurringSubscription,check['ids']['subscription']).checkout_reference='another-checkout'
        db.session.commit()
        check['payload'].pop('metadata')
    admin_login(client)
    assert client.post(check['url'],json={}).json['outcome']=='matched'


def test_global_payment_cooldown_and_actor_limit(client, check):
    admin_login(client)
    now=datetime.utcnow()
    db.session.add(AuditLog(actor_id=check['ids']['customer'],action='ADMIN_PAYMENT_CHECK_REQUESTED',description='Synthetic',
        target_type='payment',target_id=check['ids']['payment'],created_at=now))
    db.session.commit()
    response=client.post(check['url'],json={})
    assert response.status_code==429 and response.headers['Retry-After']=='60'
    assert not check['verify'].called
    AuditLog.query.filter_by(action='ADMIN_PAYMENT_CHECK_REQUESTED').delete()
    db.session.add_all(AuditLog(actor_id=check['ids']['admin'],action='ADMIN_PAYMENT_CHECK_REQUESTED',description='Synthetic',
        target_type='payment',target_id=100+i,created_at=now) for i in range(10))
    db.session.commit()
    assert client.post(check['url'],json={}).status_code==429
    assert not check['verify'].called


def test_no_provider_call_when_audit_commit_fails(client, check, monkeypatch):
    admin_login(client)
    monkeypatch.setattr(db.session,'commit',Mock(side_effect=RuntimeError('synthetic audit failure')))
    with pytest.raises(RuntimeError): client.post(check['url'],json={})
    assert not check['verify'].called
    db.session.rollback()


@pytest.mark.parametrize('kind',['success','legacy','foreign','staff','admin','invalid-reference','bad-amount','bad-plan','missing-subscription'])
def test_ineligible_saved_records_rejected_without_provider_call(client, check, kind):
    row=db.session.get(Payment,check['ids']['payment'])
    if kind=='success':row.status='success'
    elif kind=='legacy':row.product='lifetime'
    elif kind=='foreign':db.session.get(RecurringSubscription,check['ids']['subscription']).business_id=99999
    elif kind=='staff':db.session.get(User,check['ids']['customer']).staff_only=True
    elif kind=='admin':db.session.get(User,check['ids']['customer']).admin_enabled=True
    elif kind=='invalid-reference':row.reference='unsafe/path?reference'
    elif kind=='bad-amount':row.amount_kobo+=1
    elif kind=='bad-plan':row.plan_code='pro'
    elif kind=='missing-subscription':row.subscription_id=None
    db.session.commit();admin_login(client)
    response=client.post(check['url'],json={})
    assert response.status_code in (404,409)
    assert not check['verify'].called


def test_get_csrf_and_customer_sessions_cannot_lookup(client, check):
    assert client.post(check['url'],json={}).status_code==302
    admin_login(client)
    # The existing global route guard rejects unmatched methods before Flask's 405.
    assert client.get(check['url']).status_code==403
    client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post(check['url'],json={}).status_code==400
    assert not check['verify'].called
    import re
    page=client.get('/admin/payments').get_data(as_text=True)
    csrf=re.search(r'<meta name="csrf-token" content="([^"]+)"',page).group(1)
    assert client.post(check['url'],json={},headers={'X-CSRFToken':csrf}).status_code==200
    client.application.config['WTF_CSRF_ENABLED']=False
    client.post('/admin/logout')
    with client.session_transaction() as session:
        session['_user_id']=str(check['ids']['customer']);session['_fresh']=True
        session['admin_session']=True
    from flask import g
    g.pop('_login_user',None)
    check['verify'].reset_mock()
    assert client.post(check['url'],json={}).status_code==403
    assert not check['verify'].called


def test_existing_environment_policy_blocks_unapproved_preview_before_lookup(client, check, monkeypatch):
    monkeypatch.setattr(provider,'configured',REAL_CONFIGURED)
    monkeypatch.setenv('VERCEL_ENV','preview');monkeypatch.setenv('VERCEL_PROJECT_ID','wrong-project')
    admin_login(client)
    assert client.post(check['url'],json={}).status_code==503
    assert not check['verify'].called


def test_transport_is_existing_guarded_get_only(client, check, monkeypatch):
    monkeypatch.setattr(provider,'configured',REAL_CONFIGURED)
    monkeypatch.setattr(provider,'verify',lambda ref: provider.call('/transaction/verify/'+provider.identifier(ref)))
    for key in ('VERCEL_ENV','VERCEL','VERCEL_PROJECT_ID','VERCEL_TARGET_ENV'):
        monkeypatch.delenv(key,raising=False)
    transport=Mock(return_value=check['payload']);monkeypatch.setattr(provider,'_request',transport)
    admin_login(client)
    assert client.post(check['url'],json={}).json['outcome']=='matched'
    assert transport.call_args.args[0]=='/transaction/verify/SUB-current'
    assert transport.call_args.args[2:] == ('GET',None)


def test_revocation_during_provider_wait_hides_result(client, check):
    admin_login(client)
    def revoke(reference):
        actor=db.session.get(User,check['ids']['admin']);actor.suspended_at=datetime.utcnow();db.session.commit()
        return check['payload']
    check['verify'].side_effect=revoke
    response=client.post(check['url'],json={})
    assert response.status_code==403 and 'provider_status' not in response.get_data(as_text=True)
