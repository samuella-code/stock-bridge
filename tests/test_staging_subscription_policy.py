"""No network: deployment authorization uses synthetic server metadata."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest

from paystack_policy import staging_preview_authorized, staging_callback_origin
from app import db
from app.models import Business, Payment, RecurringSubscription, BillingEvent
from app.subscriptions import billing, provider
from app.subscriptions.entitlements import effective_access, immediate_upgrade, access_label
from app.businesses.service import accessible_business, selection_required, choose_primary
from tests.test_billing import app, client, fake, seed, purchase, legacy, login

SHA = 'a' * 40


def identity():
    return {'VERCEL_PROJECT_ID': 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv',
        'VERCEL_ENV': 'preview', 'VERCEL_TARGET_ENV': 'preview',
        'VERCEL_GIT_REPO_OWNER': 'samuella-code', 'VERCEL_GIT_REPO_SLUG': 'stock-bridge',
        'VERCEL_GIT_COMMIT_REF': 'release/staging-phase6', 'VERCEL_GIT_COMMIT_SHA': SHA}


def configuration():
    return {'STAGING_PAYSTACK_TEST_ENABLED': True, 'STAGING_PAYSTACK_APPROVED_SHA': SHA,
        'STAGING_PAYSTACK_CALLBACK_ORIGIN': 'https://stock-bridge-staging.vercel.app',
        'SUBSCRIPTIONS_ENABLED': True, 'BILLING_PROVIDER_ENABLED': True,
        'PAYSTACK_PUBLIC_KEY': 'pk_test_fixture', 'PAYSTACK_SECRET_KEY': 'sk_test_fixture'}


def authorize(app, monkeypatch):
    app.config.update(configuration())
    for key, value in identity().items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('key', list(identity()))
@pytest.mark.parametrize('value', [None, '', 'unexpected'])
def test_missing_or_wrong_metadata_denies(key, value):
    env = identity()
    if value is None:
        env.pop(key)
    else:
        env[key] = value
    assert not staging_preview_authorized(configuration(), env)


@pytest.mark.parametrize('key,value', [
    ('STAGING_PAYSTACK_TEST_ENABLED', False), ('STAGING_PAYSTACK_TEST_ENABLED', 'true'),
    ('SUBSCRIPTIONS_ENABLED', False), ('BILLING_PROVIDER_ENABLED', False),
    ('STAGING_PAYSTACK_APPROVED_SHA', 'b' * 40), ('STAGING_PAYSTACK_APPROVED_SHA', ''),
    ('PAYSTACK_PUBLIC_KEY', 'pk_live_fixture'), ('PAYSTACK_SECRET_KEY', 'sk_live_fixture'),
    ('PAYSTACK_PUBLIC_KEY', ''), ('PAYSTACK_SECRET_KEY', 'sk_test_invalid!'),
    ('STAGING_PAYSTACK_CALLBACK_ORIGIN', 'https://stock-bridge-one.vercel.app'),
    ('STAGING_PAYSTACK_CALLBACK_ORIGIN', 'http://stock-bridge-staging.vercel.app'),
    ('STAGING_PAYSTACK_CALLBACK_ORIGIN', 'https://stock-bridge-staging.vercel.app?next=evil'),
    ('STAGING_PAYSTACK_CALLBACK_ORIGIN', 'https://stock-bridge-staging.vercel.app/'),
])
def test_invalid_configuration_denies(key, value):
    cfg = configuration(); cfg[key] = value
    assert not staging_preview_authorized(cfg, identity())


def test_approved_release_redeployments_do_not_depend_on_host_or_request():
    env = identity()
    for hostname in ('first.vercel.app', 'redeployed.vercel.app'):
        env['VERCEL_URL'] = hostname
        assert staging_preview_authorized(configuration(), env)
    assert staging_callback_origin(configuration()) == 'https://stock-bridge-staging.vercel.app'


def test_preview_provider_and_callback_are_pinned(app, monkeypatch):
    authorize(app, monkeypatch)
    with app.test_request_context('/', base_url='https://untrusted.example.invalid'):
        assert provider.configured()
        assert provider.checkout_callback() == 'https://stock-bridge-staging.vercel.app/plans/callback'
    monkeypatch.setenv('VERCEL_GIT_COMMIT_REF', 'feature/arbitrary')
    assert not provider.configured()
    with app.test_request_context('/'):
        with pytest.raises(provider.PaystackError):
            provider.checkout_callback()


@pytest.mark.parametrize('mode', ['test', 'live'])
def test_production_provider_and_callback_unchanged(app, monkeypatch, mode):
    monkeypatch.setenv('VERCEL_ENV', 'production')
    app.config.update(PAYSTACK_PUBLIC_KEY=f'pk_{mode}_fixture',
        PAYSTACK_SECRET_KEY=f'sk_{mode}_fixture', CUSTOMER_HOST='customer.example.invalid')
    with app.test_request_context('/'):
        assert provider.configured()
        assert provider.checkout_callback() == 'https://customer.example.invalid/plans/callback'


@pytest.mark.parametrize('old,new,old_interval,new_interval,immediate', [
    ('basic','plus','monthly','monthly',True), ('basic','pro','yearly','yearly',True),
    ('plus','pro','monthly','monthly',True), ('pro','plus','monthly','monthly',False),
    ('pro','basic','yearly','yearly',False), ('basic','pro','monthly','yearly',False),
])
def test_upgrade_timing(old,new,old_interval,new_interval,immediate):
    assert immediate_upgrade(old,new,old_interval,new_interval) is immediate


def signed(client, event):
    body = json.dumps(event).encode()
    signature = hmac.new(b'sk_test_fixture', body, hashlib.sha512).hexdigest()
    return client.post('/payments/webhook', data=body, headers={'x-paystack-signature': signature})


def test_preview_pro_checkout_webhook_replay_and_invalid_signature(app, client, fake, monkeypatch):
    authorize(app, monkeypatch)
    user, business = seed(eligible=False)
    with app.test_request_context('/', base_url='https://untrusted.example.invalid'):
        billing.begin_checkout(user, 'pro', 'monthly')
    payload = next(data for path, _, data in fake.calls if path == '/transaction/initialize')
    assert payload['callback_url'] == 'https://stock-bridge-staging.vercel.app/plans/callback'
    event = {'event':'subscription.create', 'data':{'domain':'test','subscription_code':'SUB_new'}}
    assert client.post('/payments/webhook', data=json.dumps(event),
        headers={'x-paystack-signature':'bad'}).status_code == 401
    assert Payment.query.filter_by(status='success').count() == 0
    assert signed(client, {**event, 'data': {**event['data'], 'domain': 'live'}}).status_code == 503
    assert Payment.query.filter_by(status='success').count() == 0
    assert signed(client, event).status_code == 200
    assert signed(client, event).status_code == 200
    assert effective_access(user).business_limit == 5
    assert BillingEvent.query.filter_by(kind='subscription_paid').count() == 1
    assert Payment.query.filter_by(status='success').count() == 1
    # Callback replay also cannot extend the period or create another payment.
    sub = RecurringSubscription.query.one(); end = sub.current_period_end
    billing.verify_checkout(user, sub.checkout_reference)
    assert sub.current_period_end == end and Payment.query.count() == 1


def test_preview_never_activates_legacy_payments(app, client, monkeypatch):
    authorize(app, monkeypatch)
    user, business = seed(eligible=False)
    payment = Payment(customer_email=user.email, reference='legacy-test', amount_kobo=300000)
    db.session.add(payment); db.session.commit(); login(client, user)
    event = {'event':'charge.success','data':{'domain':'test','reference':payment.reference}}
    assert signed(client, event).status_code == 404
    assert client.get('/payments/callback?reference=legacy-test').status_code == 404
    assert client.get('/payments/status/legacy-test').status_code == 404
    assert client.post('/payments/initialize').status_code == 410
    assert payment.status != 'success' and not business.has_write_access


def test_unapproved_preview_never_initializes_or_processes(app, client, monkeypatch):
    monkeypatch.setenv('VERCEL_ENV', 'preview')
    user, _ = seed(eligible=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Unauthorized request reached transport')
    monkeypatch.setattr(provider, '_request', forbidden)
    with app.test_request_context('/'):
        with pytest.raises(provider.PaystackError):
            billing.begin_checkout(user, 'pro', 'monthly')
    assert Payment.query.count() == 0 and RecurringSubscription.query.count() == 0
    assert signed(client, {'event':'subscription.create','data':{}}).status_code == 404


def test_pro_business_slots_and_repeated_downgrade_choices(app, fake):
    user, business = seed(eligible=False)
    sub = purchase(user, 'pro')
    shops = [business] + [Business(user_id=user.id,name=f'Shop {n}') for n in range(2, 7)]
    db.session.add_all(shops[1:]); db.session.commit()
    shops[0].suspended_at = datetime.utcnow(); db.session.commit()
    other, foreign = seed(email='foreign@example.invalid', eligible=False)
    assert not accessible_business(user, foreign)
    assert not accessible_business(user, shops[0])
    assert accessible_business(user, shops[4]) and not accessible_business(user, shops[5])
    assert 'StockBridge Pro' in access_label(user)
    sub.plan_code = 'plus'; db.session.commit()
    assert accessible_business(user, shops[1]) and not accessible_business(user, shops[2])
    assert Business.query.filter_by(user_id=user.id).count() == 6
    sub.current_period_end = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
    # A Basic generation requires choosing one business; neither stage deletes data.
    from app.models import AccountBilling
    now = datetime.utcnow()
    basic = RecurringSubscription(user_id=user.id,business_id=business.id,plan_code='basic',
        billing_interval='monthly',amount_kobo=300000,provider_plan_code='PLN_basic_monthly',
        status='active',current_period_start=now,current_period_end=now+timedelta(days=30))
    db.session.add(basic); db.session.commit()
    assert selection_required(user)
    choose_primary(user, shops[1].id); db.session.commit()
    assert not selection_required(user)
    # A later Pro generation invalidates the old one-business choice on expiry.
    basic.current_period_end = now - timedelta(seconds=1)
    later = RecurringSubscription(user_id=user.id,business_id=business.id,plan_code='pro',
        billing_interval='monthly',amount_kobo=1000000,provider_plan_code='PLN_pro_monthly',
        status='active',current_period_start=now,current_period_end=now+timedelta(days=30))
    db.session.add(later); db.session.commit()
    assert not selection_required(user)
    later.current_period_end = now-timedelta(seconds=1)
    # Retained legacy fallback is a one-business entitlement.
    legacy(user, business); db.session.commit()
    assert selection_required(user)
    choose_primary(user, shops[2].id); db.session.commit()
    assert not selection_required(user) and accessible_business(user, shops[2])
    assert Business.query.filter_by(user_id=user.id).count() == 6


@pytest.mark.parametrize('plan,interval,amount,limit', [
    ('basic','monthly',300000,1), ('basic','yearly',3000000,1),
    ('plus','monthly',500000,2), ('plus','yearly',5000000,2),
    ('pro','monthly',1000000,5), ('pro','yearly',10000000,5),
])
def test_plan_review_and_public_prices(app, client, plan, interval, amount, limit):
    user, _ = seed(eligible=False); login(client, user)
    response = client.get(f'/plans/review?plan={plan}&interval={interval}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f"₦{amount//100:,}" in html
    assert ('1 business' if limit == 1 else f'Up to {limit} businesses') in html
    assert 'Continue to Paystack' in html
    assert 'StockBridge Pro' in client.get('/plans/').get_data(as_text=True)
    client.post('/auth/logout')
    assert '₦100,000' in client.get('/').get_data(as_text=True)


@pytest.mark.parametrize('old,new,immediate', [('basic','pro',True),('plus','pro',True),
                                           ('pro','plus',False),('pro','basic',False)])
def test_review_matches_actual_change_timing(app, client, fake, old, new, immediate):
    user, _ = seed(eligible=False); sub = purchase(user, old); login(client, user)
    html = client.get(f'/plans/{sub.id}/change?plan={new}&interval=monthly').get_data(as_text=True)
    assert ('new full-price checkout now' in html) is immediate
    assert ('No charge is due today' in html) is (not immediate)
    before = len(fake.calls)
    link = billing.change_plan(user, sub, new, 'monthly')
    assert bool(link) is immediate
    operations = [path for path, _, _ in fake.calls[before:]]
    assert ('/transaction/initialize' in operations) is immediate
    assert ('/subscription' in operations) is (not immediate)


def test_legacy_pro_label_and_fallback_preserved(app, fake):
    user, business = seed(eligible=False); original = legacy(user, business)
    sub = purchase(user, 'pro')
    assert effective_access(user).kind == 'legacy_lifetime_pro'
    assert access_label(user).startswith('Existing account access + Pro')
    sub.current_period_end = datetime.utcnow() - timedelta(seconds=1); db.session.commit()
    assert effective_access(user).kind == 'legacy_lifetime'
    assert effective_access(user).business_limit == 1
    assert db.session.get(Payment, original.id).status == 'success'


@pytest.mark.parametrize('field,value', [('domain','live'),('amount',1000001),
    ('currency','USD'),('interval','annually'),('plan_code','PLN_wrong')])
def test_pro_provider_metadata_must_match(app, field, value):
    from app.subscriptions.entitlements import plan_spec
    plan = {'domain':'test','amount':1000000,'currency':'NGN',
            'interval':'monthly','plan_code':'PLN_pro_monthly'}
    plan[field] = value
    assert not provider.validate_plan(plan, 'PLN_pro_monthly', plan_spec('pro','monthly'))


@pytest.mark.parametrize('missing', list(identity()))
def test_incomplete_runtime_identity_blocks_real_provider_gate(app, client, monkeypatch, missing):
    authorize(app, monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    assert not provider.configured()
    assert signed(client, {'event':'charge.success','data':{}}).status_code == 404


@pytest.mark.parametrize('key', ['PAYSTACK_SECRET_KEY', 'PAYSTACK_PUBLIC_KEY'])
@pytest.mark.parametrize('value', [None, True, 1, [], {}])
def test_malformed_credentials_fail_closed(key, value):
    cfg = configuration(); cfg[key] = value
    assert not staging_preview_authorized(cfg, identity())
