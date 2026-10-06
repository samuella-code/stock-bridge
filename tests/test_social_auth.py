"""Real Authlib state/JWKS/JWT validation with local keys and fake HTTP exchange."""
import time
from urllib.parse import parse_qs, urlsplit
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey, ECKey
from sqlalchemy.exc import IntegrityError
from test_multi_business import app, seed
from app import db
from app.models import User, Business, SocialIdentity, AccountBilling, BillingEvent
from app.auth.social import ISSUERS

@pytest.fixture
def oidc(app, monkeypatch):
    rsa = RSAKey.generate_key(2048, parameters={'kid': 'local-key'})
    ec = ECKey.generate_key('P-256', parameters={'kid': 'local-apple'})
    app.config.update(GOOGLE_CLIENT_ID='google-test', GOOGLE_CLIENT_SECRET='google-secret-test',
        GOOGLE_REDIRECT_URI='https://staging.example.invalid/auth/google/callback',
        APPLE_CLIENT_ID='apple-test', APPLE_TEAM_ID='team-test', APPLE_KEY_ID='local-apple',
        APPLE_PRIVATE_KEY=ec.as_pem(private=True).decode(),
        APPLE_REDIRECT_URI='https://staging.example.invalid/auth/apple/callback')
    clients={}
    for provider in ('google','apple'):
        client=app.extensions['stockbridge_oauth'].create_client(provider)
        client.client_id=f'{provider}-test'; client.client_secret=f'{provider}-secret-test'
        metadata={'issuer':ISSUERS[provider], 'authorization_endpoint':ISSUERS[provider]+'/authorize',
            'token_endpoint':ISSUERS[provider]+'/token', 'id_token_signing_alg_values_supported':['RS256'],
            'jwks':{'keys':[rsa.as_dict(private=False)]}}
        monkeypatch.setattr(client,'load_server_metadata',lambda data=metadata: data)
        clients[provider]=client
    return rsa, clients


def start(client, provider):
    response=client.post(f'/auth/{provider}/start?next=https://evil.invalid')
    assert response.status_code==302
    params=parse_qs(urlsplit(response.location).query)
    assert params['response_type']==['code'] and params['scope'][0].startswith('openid')
    return params


def callback(client, provider, params, fields=None):
    data={'code':'server-code','state':params['state'][0]}; data.update(fields or {})
    if provider=='google':return client.get('/auth/google/callback',query_string=data)
    bridge=client.post('/auth/apple/callback',data=data)
    assert bridge.status_code==200 and 'access_token' not in bridge.get_data(as_text=True)
    assert bridge.headers['Cache-Control']=='no-store'
    return client.post('/auth/apple/callback/complete',data=data)


def exchange(oidc, monkeypatch, provider, params, overrides=None, wrong_key=False):
    key,clients=oidc; now=int(time.time())
    claims={'iss':ISSUERS[provider], 'aud':f'{provider}-test', 'sub':'stable-subject',
        'iat':now, 'exp':now+300, 'nonce':params['nonce'][0],
        'email':'social@example.invalid', 'email_verified':True, 'name':'Signed provider name'}
    claims.update(overrides or {})
    if wrong_key:key=RSAKey.generate_key(2048, parameters={'kid':'local-key'})
    encoded=jwt.encode({'alg':'RS256','kid':'local-key'},claims,key)
    calls=[]
    def fetch(**kwargs):
        calls.append(kwargs)
        return {'id_token':encoded, 'access_token':'never-expose-access-token',
                'refresh_token':'never-expose-refresh-token','token_type':'Bearer'}
    monkeypatch.setattr(clients[provider], 'fetch_access_token', fetch)
    return encoded,calls


@pytest.mark.parametrize('provider',['google','apple'])
def test_new_social_user_verification_trial_and_returning_identity(app,oidc,monkeypatch,provider):
    client=app.test_client(); params=start(client,provider)
    if provider=='google':assert params['code_challenge_method']==['S256']
    else:assert params['response_mode']==['form_post']
    encoded,calls=exchange(oidc,monkeypatch,provider,params)
    response=callback(client,provider,params); assert response.location.endswith('/auth/social/finish')
    assert calls[0]['redirect_uri']==app.config[f'{provider.upper()}_REDIRECT_URI']
    if provider=='google':assert len(calls[0]['code_verifier'])>=43
    assert User.query.count()==0
    response=client.post('/auth/social/finish',data={'full_name':'Retail Owner','business_name':'First Shop'})
    assert response.location.endswith('/dashboard')
    user=User.query.one(); account=AccountBilling.query.one()
    assert user.email_verified_at and account.trial_started_at
    assert (account.trial_ends_at-account.trial_started_at).days==7
    initial=account.trial_started_at; password=user.password_hash
    assert SocialIdentity.query.one().provider_subject=='stable-subject'
    assert Business.query.count()==1
    with client.session_transaction() as session:
        assert encoded not in str(dict(session)) and 'never-expose' not in str(dict(session))
    client.post('/auth/logout')
    params=start(client,provider)
    # Returning Apple can omit name/email; a stable subject is sufficient.
    exchange(oidc,monkeypatch,provider,params,{'email':None,'name':None,'email_verified':False})
    response=callback(client,provider,params); assert response.location.endswith('/dashboard')
    assert User.query.count()==Business.query.count()==SocialIdentity.query.count()==1
    assert account.trial_started_at==initial and user.password_hash==password
    assert BillingEvent.query.filter_by(kind='trial_started').count()==1


@pytest.mark.parametrize('provider',['google','apple'])
@pytest.mark.parametrize('bad',['issuer','audience','nonce','signature','expired','unverified','subject','missing_nonce','nonce_opt_out','wrong_aud_correct_azp'])
def test_oidc_security_rejects_invalid_assertions(app,oidc,monkeypatch,provider,bad):
    client=app.test_client();params=start(client,provider)
    overrides={'issuer':{'iss':'https://evil.invalid'},'audience':{'aud':'other-client'},
        'nonce':{'nonce':'bad'}, 'expired':{'exp':int(time.time())-300},
        'unverified':{'email_verified':False}, 'subject':{'sub':''},
        'wrong_aud_correct_azp':{'aud':'different-client','azp':f'{provider}-test'},
        'missing_nonce':{'nonce':None}, 'nonce_opt_out':{'nonce':None,'nonce_supported':False}}.get(bad,{})
    exchange(oidc,monkeypatch,provider,params,overrides,wrong_key=bad=='signature')
    response=callback(client,provider,params)
    assert response.location.endswith('/auth/login')
    assert User.query.count()==Business.query.count()==SocialIdentity.query.count()==0
    with client.session_transaction() as session:assert 'social_pending' not in session


@pytest.mark.parametrize('provider',['google','apple'])
@pytest.mark.parametrize('bad',['state','cancel','expired_flow','provider_failure','missing_code','replay'])
def test_callback_flow_failures(app,oidc,monkeypatch,provider,bad):
    client=app.test_client();params=start(client,provider); encoded,calls=exchange(oidc,monkeypatch,provider,params)
    fields={}
    if bad=='state':fields={'state':'attacker'}
    if bad=='cancel':fields={'error':'access_denied','error_description':'secret malicious text'}
    if bad=='missing_code':fields={'code':''}
    if bad=='expired_flow':
        with client.session_transaction() as session:
            flow=session['social_flow'];flow['expires']=0;session['social_flow']=flow
    if bad=='provider_failure':
        def failing(**kw):raise ValueError('SECRET TOKEN PROVIDER ERROR')
        monkeypatch.setattr(oidc[1][provider],'fetch_access_token',failing)
    if bad=='replay':callback(client,provider,params)
    response=callback(client,provider,params,fields)
    assert response.location.endswith('/auth/login')
    html=client.get(response.location).get_data(as_text=True)
    assert 'SECRET TOKEN' not in html and encoded not in html and 'malicious text' not in html
    assert not User.query.count()


@pytest.mark.parametrize('provider',['google','apple'])
def test_existing_account_requires_password_and_preserves_paid_data(app,oidc,monkeypatch,provider):
    user,shops=seed('plus',2,email='social@example.invalid');user.set_password('existing-strong-password');db.session.commit()
    account=AccountBilling.query.one();old_hash=user.password_hash
    client=app.test_client();params=start(client,provider);exchange(oidc,monkeypatch,provider,params)
    callback(client,provider,params)
    html=client.get('/auth/social/finish').get_data(as_text=True);assert 'Link your existing account' in html
    assert client.post('/auth/social/finish',data={'password':'bad'}).status_code==400
    assert SocialIdentity.query.count()==0
    response=client.post('/auth/social/finish',data={'password':'existing-strong-password','email':'attacker@example.invalid','user_id':999})
    assert response.location.endswith('/dashboard')
    assert User.query.one().id==user.id and user.password_hash==old_hash
    assert len(user.businesses)==2 and account.trial_started_at is None
    assert SocialIdentity.query.one().user_id==user.id
    client.post('/auth/logout')
    assert client.post('/auth/login',data={'email':user.email,'password':'existing-strong-password'}).location.endswith('/dashboard')


@pytest.mark.parametrize('kind',['trial','basic','plus','lifetime','lifetime_plus'])
def test_linking_does_not_restart_trial_or_remove_entitlements(app,oidc,monkeypatch,kind):
    user,_=seed(kind,email='social@example.invalid');user.set_password('link-password');db.session.commit()
    account=AccountBilling.query.one(); started=account.trial_started_at; legacy=account.legacy_payment_id
    client=app.test_client();params=start(client,'google');exchange(oidc,monkeypatch,'google',params)
    callback(client,'google',params);client.post('/auth/social/finish',data={'password':'link-password'})
    assert account.trial_started_at==started and account.legacy_payment_id==legacy


def test_apple_relay_and_untrusted_raw_user_name(app,oidc,monkeypatch):
    client=app.test_client();params=start(client,'apple')
    exchange(oidc,monkeypatch,'apple',params,{'email':'relay@privaterelay.appleid.com','email_verified':'true','name':None})
    callback(client,'apple',params,{'user':'{"name":{"firstName":"attacker"},"email":"victim@example.invalid"}'})
    client.post('/auth/social/finish',data={'full_name':'Chosen Name','business_name':'Relay Shop'})
    assert User.query.one().email=='relay@privaterelay.appleid.com'
    assert User.query.one().full_name=='Chosen Name'


@pytest.mark.parametrize('restriction',['admin','admin_enabled','suspended','suspended_business'])
def test_social_cannot_bypass_account_restrictions(app,oidc,monkeypatch,restriction):
    from datetime import datetime
    user,shops=seed(email='social@example.invalid')
    if restriction=='admin':user.role='admin'
    elif restriction=='admin_enabled':user.admin_enabled=True
    elif restriction=='suspended':user.suspended_at=datetime.utcnow()
    else:shops[0].suspended_at=datetime.utcnow()
    db.session.commit(); client=app.test_client();params=start(client,'google');exchange(oidc,monkeypatch,'google',params)
    callback(client,'google',params)
    assert client.get('/auth/social/finish').status_code==403
    assert not SocialIdentity.query.count()


def test_identity_constraints_and_conflicting_second_link_rollback(app,oidc,monkeypatch):
    user,_=seed(email='social@example.invalid');user.set_password('existing-password')
    db.session.add(SocialIdentity(user_id=user.id,provider='google',provider_subject='original-subject'));db.session.commit()
    client=app.test_client();params=start(client,'google');exchange(oidc,monkeypatch,'google',params)
    callback(client,'google',params);response=client.post('/auth/social/finish',data={'password':'existing-password'})
    assert response.location.endswith('/auth/login') and SocialIdentity.query.count()==1
    db.session.add(SocialIdentity(user_id=user.id,provider='google',provider_subject='original-subject'))
    with pytest.raises(IntegrityError):db.session.commit()
    db.session.rollback()
    assert User.query.count()==1 and Business.query.count()==1


def test_unconfigured_oauth_is_disabled_not_decorative(app):
    client=app.test_client(); html=client.get('/auth/signup').get_data(as_text=True)
    assert 'Continue with Google' in html and 'Continue with Apple' in html and 'not available yet' in html
    assert client.post('/auth/google/start').location.endswith('/auth/login')
    assert client.post('/auth/arbitrary/start').status_code==404
    assert client.get('/auth/google/start').status_code==405


@pytest.mark.parametrize('uri',['http://staging.example.invalid/auth/google/callback','https://bad.invalid/auth/google/callback?next=evil','https://name:secret@bad.invalid/auth/google/callback','https://bad.invalid/wrong'])
def test_callback_configuration_fails_closed(app,oidc,uri):
    app.config['GOOGLE_REDIRECT_URI']=uri
    assert app.test_client().post('/auth/google/start').location.endswith('/auth/login')


def test_apple_short_lived_client_secret(app,oidc):
    from app.auth.social import client_for
    key=ECKey.import_key(app.config['APPLE_PRIVATE_KEY'])
    claims=jwt.decode(client_for('apple').client_secret,key).claims
    assert claims['iss']=='team-test' and claims['aud']==ISSUERS['apple']
    assert claims['sub']=='apple-test' and claims['exp']-claims['iat']==300


def test_callbacks_and_finish_csrf_boundaries(app,oidc,monkeypatch):
    app.config['WTF_CSRF_ENABLED']=True
    client=app.test_client()
    assert client.post('/auth/google/start').status_code==400
    # Only the OIDC state-protected callbacks accept provider cross-site posts.
    assert client.post('/auth/apple/callback',data={'state':'untrusted','code':'x'}).status_code==200
    assert client.post('/auth/apple/callback/complete',data={'state':'untrusted','code':'x'}).location.endswith('/auth/login')
    assert client.post('/auth/social/finish').status_code==400


@pytest.mark.parametrize('provider',['google','apple'])
def test_token_secret_non_exposure_and_fixed_destinations(app,oidc,monkeypatch,caplog,provider):
    import logging
    caplog.set_level(logging.DEBUG)
    client=app.test_client();params=start(client,provider)
    assert params['redirect_uri']==[app.config[f'{provider.upper()}_REDIRECT_URI']]
    assert 'evil.invalid' not in str(params)
    encoded,_=exchange(oidc,monkeypatch,provider,params)
    response=callback(client,provider,params,{'email':'victim@example.invalid','next':'https://evil.invalid'})
    assert response.location.endswith('/auth/social/finish') and 'evil.invalid' not in response.location
    html=client.get(response.location).get_data(as_text=True)
    for forbidden in (encoded,'never-expose-access-token','never-expose-refresh-token','google-secret-test',app.config['APPLE_PRIVATE_KEY']):
        assert forbidden not in html and forbidden not in caplog.text and forbidden not in str(response.headers)
        with client.session_transaction() as session:assert forbidden not in str(dict(session))
    assert 'victim@example.invalid' not in html


@pytest.mark.parametrize('provider',['google','apple'])
def test_non_ascii_state_rejected_without_server_error(app,oidc,provider):
    client=app.test_client();params=start(client,provider)
    assert callback(client,provider,params,{'state':'⚠️ attacker'}).location.endswith('/auth/login')


def test_finish_proof_expiry_and_new_workspace_conflict_rollback(app,oidc,monkeypatch):
    client=app.test_client();params=start(client,'google');exchange(oidc,monkeypatch,'google',params)
    callback(client,'google',params)
    with client.session_transaction() as session:
        pending=session['social_pending'];pending['expires']=0;session['social_pending']=pending
    assert client.post('/auth/social/finish',data={'full_name':'Expired','business_name':'No Shop'}).location.endswith('/auth/login')
    assert User.query.count()==Business.query.count()==0
    params=start(client,'google');exchange(oidc,monkeypatch,'google',params);callback(client,'google',params)
    original=db.session.flush; calls=0
    def conflicting(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise IntegrityError('simulated identity conflict',{},Exception('unique'))
        return original(*args,**kwargs)
    monkeypatch.setattr(db.session,'flush',conflicting)
    response=client.post('/auth/social/finish',data={'full_name':'Conflicted','business_name':'No Shop'})
    monkeypatch.setattr(db.session,'flush',original)
    assert response.location.endswith('/auth/login')
    assert User.query.count()==Business.query.count()==SocialIdentity.query.count()==0
