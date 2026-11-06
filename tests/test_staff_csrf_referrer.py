"""Real HTTPS CSRF validation under the Staff response privacy policy."""
import pytest
from flask import Flask, request
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf
from app.staff.routes import protect


ORIGIN = 'https://stock-bridge-staging.vercel.app'


def harness(*, authentication_page=False):
    app = Flask(__name__)
    app.config.update(SECRET_KEY='synthetic-csrf-test-secret', WTF_CSRF_ENABLED=True,
                      WTF_CSRF_SSL_STRICT=True, TESTING=True)
    CSRFProtect(app)
    if authentication_page:
        from flask_login import LoginManager, UserMixin
        from app.auth.routes import private_staff_identity_response
        class StaffIdentity(UserMixin):
            id = '1'
            staff_only = True
        manager = LoginManager(app)
        manager.user_loader(lambda identifier: StaffIdentity() if identifier == '1' else None)
        app.after_request(private_staff_identity_response)
    else:
        app.after_request(protect)
    calls = []

    @app.route('/staff/probe', methods=['GET', 'POST'])
    def form():
        if request.method == 'GET':
            return generate_csrf()
        calls.append('handled')
        return 'accepted'

    client = app.test_client()
    if authentication_page:
        with client.session_transaction(base_url=ORIGIN) as session:
            session['_user_id'] = '1'
            session['_fresh'] = True
    return client, calls


def test_form_privacy_policy_supplies_same_origin_referer_for_https_csrf():
    client, calls = harness()
    page = client.get('/staff/probe', base_url=ORIGIN)
    assert page.headers['Referrer-Policy'] == 'same-origin'
    # Browser sends the source URL only to the same origin under this policy.
    response = client.post('/staff/probe', base_url=ORIGIN,
                           data={'csrf_token': page.get_data(as_text=True)},
                           headers={'Referer': ORIGIN + '/staff/probe'})
    assert response.status_code == 200
    assert calls == ['handled']
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['Referrer-Policy'] == 'same-origin'


@pytest.mark.parametrize('referer', [None, 'https://foreign.example/staff/probe',
                                    'http://stock-bridge-staging.vercel.app/staff/probe'])
def test_missing_or_foreign_https_referer_still_rejected(referer):
    client, calls = harness()
    page = client.get('/staff/probe', base_url=ORIGIN)
    headers = {} if referer is None else {'Referer': referer}
    response = client.post('/staff/probe', base_url=ORIGIN,
                           data={'csrf_token': page.get_data(as_text=True)}, headers=headers)
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize('token', [None, 'invalid'])
def test_missing_or_invalid_token_still_rejected_with_same_origin_referer(token):
    client, calls = harness()
    client.get('/staff/probe', base_url=ORIGIN)
    response = client.post('/staff/probe', base_url=ORIGIN,
                           data={} if token is None else {'csrf_token': token},
                           headers={'Referer': ORIGIN + '/staff/probe'})
    assert response.status_code == 400
    assert calls == []


@pytest.mark.parametrize('referer,expected', [(ORIGIN+'/auth/verify-pending',200),
    (None,400),('https://foreign.example/auth/verify-pending',400)])
def test_staff_auth_verification_page_keeps_strict_https_csrf(referer,expected):
    client,calls=harness(authentication_page=True)
    page=client.get('/staff/probe',base_url=ORIGIN)
    assert page.headers['Referrer-Policy']=='same-origin'
    assert page.headers['Cache-Control']=='private, no-store'
    response=client.post('/staff/probe',base_url=ORIGIN,
        data={'csrf_token':page.get_data(as_text=True)},
        headers={} if referer is None else {'Referer':referer})
    assert response.status_code==expected
    assert calls==(['handled'] if expected==200 else [])
