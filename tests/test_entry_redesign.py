"""Entry, pricing and review layer regressions; no provider payments."""
import pytest
from test_multi_business import app, seed, login
from app import db
from app.models import RecurringSubscription, User, Business
from app.subscriptions.entitlements import PLANS


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('path',['/','/auth/signup','/auth/login','/plans/'])
def test_rollout_flag_controls_public_trial_promises(app,enabled,path):
    app.config.update(SUBSCRIPTIONS_ENABLED=enabled,BILLING_PROVIDER_ENABLED=False)
    html=app.test_client().get(path).get_data(as_text=True).lower()
    if not enabled:
        for promise in ('free trial','7-day trial','seven-day trial','7 days free','start your 7-day'):
            assert promise not in html
        assert 'lifetime' in html or path.startswith('/auth/')
    elif path!='/auth/login':
        assert 'trial' in html


@pytest.mark.parametrize('enabled',[False,True])
def test_rollout_flag_controls_social_completion_copy(app,enabled):
    import time
    app.config['SUBSCRIPTIONS_ENABLED']=enabled
    client=app.test_client()
    with client.session_transaction() as session:
        session['social_pending']={'provider':'google','email':'copy@example.invalid','sub':'copy-sub','expires':time.time()+600}
    html=client.get('/auth/social/finish').get_data(as_text=True)
    assert ('existing trial rules' in html)==enabled
    assert ('Business tools unlock with Lifetime Access' in html)==(not enabled)


@pytest.mark.parametrize('enabled',[False,True])
def test_rollout_flag_controls_email_signup_trial_and_customer_copy(app,enabled,monkeypatch):
    from datetime import timedelta
    from app.models import AccountBilling,BillingEvent
    from app.email_service import verification_token
    app.config.update(SUBSCRIPTIONS_ENABLED=enabled,BILLING_PROVIDER_ENABLED=False)
    messages=[]
    monkeypatch.setattr('app.email_service._send_email',lambda subject,recipient,body,html=None: messages.append(body) or True)
    client=app.test_client()
    client.post('/auth/signup',data={'full_name':'Flag Owner','business_name':'Flag shop','email':'flag@example.invalid','password':'test-password-only'})
    user=User.query.one()
    client.get('/auth/verify/'+verification_token(user.email))
    account=db.session.get(AccountBilling,user.id)
    if enabled:
        assert account.trial_ends_at-account.trial_started_at==timedelta(days=7)
        initial=(account.trial_started_at,account.trial_ends_at)
    else:
        assert account is None and not user.businesses[0].has_write_access
    for path in ('/dashboard','/plans/','/businesses/'):
        html=client.get(path).get_data(as_text=True).lower()
        if not enabled:
            assert 'free trial' not in html and 'seven-day trial' not in html and '7-day trial' not in html
    client.post('/auth/logout')
    client.post('/auth/login',data={'email':user.email,'password':'test-password-only'})
    client.get('/dashboard')
    if enabled:
        db.session.refresh(account)
        assert (account.trial_started_at,account.trial_ends_at)==initial
        assert BillingEvent.query.filter_by(kind='trial_started').count()==1
    else:
        assert db.session.get(AccountBilling,user.id) is None
        assert BillingEvent.query.filter_by(kind='trial_started').count()==0
        assert all('trial' not in message.lower() for message in messages)


def test_homepage_workflow_prices_navigation_faq_and_ctas(app):
    html=app.test_client().get('/').get_data(as_text=True)
    for anchor in ('features','how-it-works','pricing','faq'):
        assert f'href="#{anchor}"' in html and f'id="{anchor}"' in html
    for page in ('Products','Inventory','Sales','Restocking','Expenses','Dashboard','Reports'):
        assert page in html
    assert html.count('<details>')==12
    for label in ('Save ₦6,000/year','Save ₦10,000/year','7-day free trial','No card required','Up to 2'):
        assert label in html
    assert 'href="/auth/signup"' in html and 'href="/auth/login"' in html
    assert 'Illustrative StockBridge' in html and 'Example figures' in html
    assert 'quarterly' not in html and 'biannual' not in html
    assert 'data-price-monthly="₦3,000/month"' in html
    assert 'data-price-yearly="₦50,000/year"' in html


@pytest.mark.parametrize('path',['/auth/login','/auth/signup'])
def test_auth_labels_preserved_fields_and_social_actions(app,path):
    html=app.test_client().get(path).get_data(as_text=True)
    assert 'entry-auth' in html and '<label>Email address' in html and '<label>Password' in html
    assert 'action="/auth/google/start"' in html and 'action="/auth/apple/start"' not in html
    assert 'name="csrf_token"' in html and 'autocomplete="email"' in html
    if path.endswith('signup'):
        assert 'name="business_name"' in html and 'name="full_name"' in html
    else:
        assert 'href="/auth/forgot-password"' in html


def test_email_signup_login_forgot_password_unchanged(app):
    client=app.test_client()
    response=client.post('/auth/signup',data={'full_name':'Owner','business_name':'My Shop',
        'email':'entry@example.invalid','password':'password123'})
    assert response.location.endswith('/auth/verify-pending')
    assert User.query.one().email_verified_at is None and Business.query.count()==1
    client.post('/auth/logout')
    assert client.post('/auth/login',data={'email':'entry@example.invalid','password':'password123'}).location.endswith('/auth/verify-pending')
    client.post('/auth/logout')
    assert client.post('/auth/login',data={'email':'entry@example.invalid','password':'bad'}).status_code==200
    response=client.post('/auth/forgot-password',data={'email':'missing@example.invalid'})
    assert response.location.endswith('/auth/login')
    assert 'If that email belongs' in client.get(response.location).get_data(as_text=True)


@pytest.mark.parametrize('plan,interval',list(PLANS))
def test_review_server_prices_no_writes_and_price_tampering(app,plan,interval):
    user,_=seed('trial'); client=app.test_client();login(client,user)
    html=client.get(f'/plans/review?plan={plan}&interval={interval}&amount=1&business_limit=999&plan_code=evil').get_data(as_text=True)
    assert 'Subscription Summary' in html and user.email in html
    assert f'₦{PLANS[(plan,interval)]["amount"]/100:,.0f}' in html
    assert ('Up to 2 businesses' if plan=='plus' else '1 business') in html
    assert 'Amount due today' in html and 'Continue to Paystack' in html
    assert 'action="/plans/checkout"' in html
    assert 'name="amount"' not in html and 'name="business_limit"' not in html
    assert RecurringSubscription.query.count()==0 and Business.query.count()==1


@pytest.mark.parametrize('plan,interval',[('basic','quarterly'),('plus','annually'),('business','monthly'),('',''),('basic','MONTHLY')])
def test_review_rejects_invalid_plan_or_interval(app,plan,interval):
    user,_=seed('trial');client=app.test_client();login(client,user)
    response=client.get('/plans/review',query_string={'plan':plan,'interval':interval})
    assert response.location.endswith('/plans/') and RecurringSubscription.query.count()==0


@pytest.mark.parametrize('plan,interval,immediate',[('plus','monthly',True),('plus','yearly',False),('basic','yearly',False)])
def test_paid_review_preserves_existing_change_timing(app,plan,interval,immediate):
    user,_=seed('basic');sub=RecurringSubscription.query.one(); client=app.test_client();login(client,user)
    html=client.get(f'/plans/{sub.id}/change?plan={plan}&interval={interval}').get_data(as_text=True)
    assert f'action="/plans/{sub.id}/change"' in html
    assert ('Continue to Paystack' in html)==immediate
    assert ('Confirm scheduled change' in html)==(not immediate)
    if not immediate:assert '₦0' in html and sub.current_period_end.strftime('%d %b %Y %H:%M UTC') in html
    assert not sub.cancel_at_period_end and RecurringSubscription.query.count()==1


def test_review_current_selection_and_other_accounts_rejected(app):
    user,_=seed('plus');sub=RecurringSubscription.query.one();client=app.test_client();login(client,user)
    assert client.get('/plans/review?plan=plus&interval=monthly').location.endswith('/plans/')
    other,_=seed('basic',email='other-entry@example.invalid');login(client,other)
    assert client.get(f'/plans/{sub.id}/change?plan=basic&interval=yearly').status_code==404


@pytest.mark.parametrize('kind',['trial','basic','plus','lifetime','lifetime_plus'])
def test_current_plan_trial_and_legacy_presentation(app,kind):
    user,_=seed(kind); client=app.test_client();login(client,user)
    html=client.get('/plans/').get_data(as_text=True)
    assert 'Your current plan' in html and 'Save ₦6,000/year' in html
    if kind=='trial':
        from app.models import AccountBilling
        assert AccountBilling.query.one().trial_ends_at.strftime('%d %b %Y %H:%M UTC') in html
        assert '7 days remaining' in html
    else:assert 'Your trial has' not in html
    if kind.startswith('lifetime'):
        assert 'Your original Lifetime Access is preserved' in html
        assert 'Choose Basic' not in html
        assert client.get('/plans/review?plan=basic&interval=monthly').location.endswith('/plans/')


def test_review_handoff_calls_existing_checkout_with_validated_choice(app,monkeypatch):
    user,_=seed('trial');client=app.test_client();login(client,user);calls=[]
    def checkout(owner,plan,interval):
        assert (plan,interval) in PLANS
        calls.append((owner.id,plan,interval));return 'https://checkout.paystack.com/test-only'
    monkeypatch.setattr('app.subscriptions.billing.begin_checkout',checkout)
    # GET review doesn't initialize anything; confirmation uses the existing endpoint.
    client.get('/plans/review?plan=plus&interval=yearly');assert not calls
    response=client.post('/plans/checkout',data={'plan':'plus','interval':'yearly','amount':'1'})
    assert response.status_code==303 and response.location=='https://checkout.paystack.com/test-only'
    assert calls==[(user.id,'plus','yearly')]


def test_review_available_only_when_subscription_feature_enabled(app):
    user,_=seed();client=app.test_client();login(client,user)
    app.config['SUBSCRIPTIONS_ENABLED']=False
    assert client.get('/plans/review?plan=plus&interval=yearly').status_code==404


@pytest.mark.parametrize('path', ['/', '/auth/login', '/auth/signup'])
def test_public_visuals_render_without_database_queries(app, path):
    """Public demo figures must never turn anonymous rendering into DB work."""
    from sqlalchemy import event
    queries=[]
    def record_query(*args): queries.append(True)
    event.listen(db.engine, 'before_cursor_execute', record_query)
    try:
        response=app.test_client().get(path)
        assert response.status_code==200
        assert b'Representative demo' in response.data
        assert not queries
    finally:
        event.remove(db.engine, 'before_cursor_execute', record_query)
