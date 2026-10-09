"""Retiring purchases must never retire historical access or payment evidence."""
from datetime import datetime
import pytest
from test_multi_business import app, seed, login
from app import db
from app.models import Payment
from app.subscriptions.entitlements import effective_access, PLANS

@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('provider', [False, True])
@pytest.mark.parametrize('configured', [False, True])
def test_retired_endpoints_cannot_create_payments(app, monkeypatch, enabled, provider, configured):
    app.config.update(SUBSCRIPTIONS_ENABLED=enabled, BILLING_PROVIDER_ENABLED=provider,
        PAYSTACK_SECRET_KEY='sk_test_fixture' if configured else '',
        PAYSTACK_PUBLIC_KEY='pk_test_fixture' if configured else '')
    user, _ = seed('trial'); client=app.test_client(); login(client,user)
    def forbidden(*args, **kwargs): raise AssertionError('Retired purchase contacted provider')
    monkeypatch.setattr('app.payments.routes.initialize_transaction', forbidden)
    count=Payment.query.count()
    for response in (client.get('/payments/checkout'), client.post('/payments/initialize', data={'amount':'1','product':'lifetime'})):
        assert response.status_code == 410
        assert b'View Basic and Plus plans' in response.data
        assert b'Lifetime' not in response.data
    assert Payment.query.count()==count

@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('path', ['/', '/plans/', '/auth/signup', '/auth/login', '/privacy', '/terms', '/refund-policy', '/subscription-terms', '/contact'])
def test_public_pages_have_no_retired_marketing(app, enabled, path):
    app.config['SUBSCRIPTIONS_ENABLED']=enabled
    response=app.test_client().get(path)
    assert response.status_code==200
    html=response.get_data(as_text=True).lower()
    assert 'lifetime' not in html
    assert '/payments/initialize' not in html and '/payments/checkout' not in html
    if path in ('/', '/plans/', '/subscription-terms'):
        for spec in PLANS.values(): assert f'{spec["amount"]/100:,.0f}' in html
    if not enabled and path in ('/', '/plans/'):
        assert 'checkout is not currently available' in html
        assert 'start free trial' not in html

@pytest.mark.parametrize('kind', ['lifetime', 'lifetime_plus'])
def test_preserved_access_uses_neutral_labels_without_mutation(app, kind):
    user, shops=seed(kind); client=app.test_client(); login(client,user)
    before=effective_access(user)
    record=Payment.query.one(); evidence=(record.product,record.reference,record.status,record.amount_kobo)
    for path in ('/dashboard','/plans/','/businesses/'):
        response=client.get(path)
        assert response.status_code==200
        assert 'lifetime' not in response.get_data(as_text=True).lower()
    after=effective_access(user)
    assert (after.kind,after.business_limit,after.can_write)==(before.kind,before.business_limit,before.can_write)
    assert (record.product,record.reference,record.status,record.amount_kobo)==evidence


def test_historical_receipt_stays_accurate(app, monkeypatch):
    from app.email_service import send_payment_success_email
    user, shops=seed('lifetime'); payment=Payment.query.one(); payment.paid_at=datetime.utcnow()
    captured=[]
    monkeypatch.setattr('app.email_service._send_email', lambda *args, **kwargs: captured.append((args,kwargs)) or True)
    with app.test_request_context():
        send_payment_success_email(user,shops[0],payment)
    text=repr(captured)
    assert 'Lifetime Access' in text and payment.reference in text
    assert '3,000.00' in text


def test_historical_billing_notice_is_presented_without_rewriting_evidence(app):
    from app.models import BillingEvent
    user, _=seed('lifetime'); client=app.test_client(); login(client,user)
    original='Your original Lifetime Access continues.'
    event=BillingEvent(user_id=user.id,event_key='historical-notice',kind='subscription_expired',message=original)
    db.session.add(event); db.session.commit()
    html=client.get('/plans/').get_data(as_text=True)
    assert 'Lifetime' not in html and 'existing account access continues.' in html
    db.session.refresh(event)
    assert event.message==original


def test_retired_purchase_keeps_authentication_and_csrf_protection(app):
    client=app.test_client()
    assert client.get('/payments/checkout').status_code==302
    user, _=seed('trial'); login(client,user)
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/payments/initialize').status_code==400
    assert Payment.query.count()==0
