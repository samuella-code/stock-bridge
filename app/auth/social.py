"""Server-side OIDC. Only validated identities survive the callback; never tokens."""
import logging
import secrets
import time
from datetime import datetime
from urllib.parse import urlsplit
from authlib.integrations.flask_client import OAuth
from joserfc import jwt
from joserfc.jwk import ECKey
from flask import abort, current_app, flash, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from sqlalchemy.exc import IntegrityError
from app import csrf, db
from app.auth.routes import auth_bp
from app.models import AccountBilling, Business, SocialIdentity, User
from app.subscriptions.entitlements import start_trial
from app.email_service import safely_send, send_welcome_email

ISSUERS = {'google': 'https://accounts.google.com', 'apple': 'https://appleid.apple.com'}


def redirect_uri(provider):
    """Never derive a redirect origin from an incoming Host/next parameter."""
    uri = current_app.config.get(f'{provider.upper()}_REDIRECT_URI', '')
    parsed = urlsplit(uri)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path != f'/auth/{provider}/callback'):
        return None
    return uri


def configured(provider):
    fields = ('CLIENT_ID', 'CLIENT_SECRET') if provider == 'google' else ('CLIENT_ID', 'TEAM_ID', 'KEY_ID', 'PRIVATE_KEY')
    return bool(provider in ISSUERS and redirect_uri(provider) and
                all(current_app.config.get(f'{provider.upper()}_{field}') for field in fields))


def init_social(app):
    # Suppress library protocol debug records (PKCE verifiers/token request details).
    logging.getLogger('authlib.integrations.base_client.sync_app').setLevel(logging.WARNING)
    oauth = OAuth(app)
    app.extensions['stockbridge_oauth'] = oauth
    # Metadata/endpoints are application owned; never accept discovery URLs from requests.
    oauth.register('google', client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile', 'code_challenge_method': 'S256', 'timeout': 15})
    oauth.register('apple', client_id=app.config['APPLE_CLIENT_ID'],
        authorize_url='https://appleid.apple.com/auth/authorize',
        access_token_url='https://appleid.apple.com/auth/token',
        jwks_uri='https://appleid.apple.com/auth/keys', issuer=ISSUERS['apple'],
        id_token_signing_alg_values_supported=['RS256'],
        client_kwargs={'scope': 'openid email name', 'token_endpoint_auth_method': 'client_secret_post', 'timeout': 15})
    app.context_processor(lambda: {'social_ready': {p: configured(p) for p in ISSUERS}})


def client_for(provider):
    client = current_app.extensions['stockbridge_oauth'].create_client(provider)
    if provider == 'apple':
        now = int(time.time())
        # Apple's client secret is a short-lived ES256 JWT; private key stays server-side.
        client.client_secret = jwt.encode({'alg': 'ES256', 'kid': current_app.config['APPLE_KEY_ID']},
            {'iss': current_app.config['APPLE_TEAM_ID'], 'iat': now, 'exp': now + 300,
             'aud': ISSUERS['apple'], 'sub': current_app.config['APPLE_CLIENT_ID']},
            ECKey.import_key(current_app.config['APPLE_PRIVATE_KEY'].replace('\\n', '\n')))
    return client


def clear_state(provider):
    for key in list(session):
        if key.startswith(f'_state_{provider}_'):
            session.pop(key, None)
    session.pop('social_flow', None)


def fail(provider, message='Sign-in could not be verified. Please try again or use email and password.'):
    clear_state(provider)
    session.pop('social_pending', None)
    flash(message, 'warning')
    return redirect(url_for('auth.login'))


def allowed(user):
    return user.role == 'user' and not user.admin_enabled and not user.suspended_at and not any(b.suspended_at for b in user.businesses)


def sign_in(user):
    if not allowed(user):
        abort(403)
    # Rotate the signed session and remove any admin/other business context.
    session.clear()
    login_user(user)
    user.last_activity_at = datetime.utcnow()
    if current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        start_trial(user)
    db.session.commit()
    return redirect(url_for('main.dashboard' if user.email_verified_at else 'auth.verification_pending'))


@auth_bp.post('/<provider>/start')
def social_start(provider):
    if provider not in ISSUERS:
        abort(404)
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    if not configured(provider):
        flash(f'{provider.title()} sign-in is not available yet. Use email and password.', 'warning')
        return redirect(url_for('auth.login'))
    clear_state(provider)
    session.pop('social_pending', None)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    session['social_flow'] = {'provider': provider, 'state': state, 'nonce': nonce, 'expires': time.time() + 600}
    try:
        extras = {'response_mode': 'form_post'} if provider == 'apple' else {}
        return client_for(provider).authorize_redirect(redirect_uri(provider), state=state,
            nonce=nonce, **extras)
    except Exception:
        # Neither library exceptions nor provider descriptions are safe customer/log output.
        return fail(provider)


@auth_bp.route('/<provider>/callback', methods=['GET', 'POST'])
@csrf.exempt
def social_callback(provider):
    if provider not in ISSUERS:
        abort(404)
    if provider == 'apple':
        if request.method != 'POST':
            abort(405)
        # Lax cookies do not accompany Apple's cross-site POST. A same-origin form
        # restores the browser-bound session without weakening cookie policy.
        fields = {key: request.form.get(key, '')[:4096] for key in ('code', 'state', 'error')}
        response = make_response(render_template('auth/apple_return.html', fields=fields))
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response
    if request.method != 'GET':
        abort(405)
    return complete_callback(provider)


@auth_bp.post('/apple/callback/complete')
@csrf.exempt
def apple_complete():
    # This callback has OIDC state CSRF protection, not a generic CSRF exemption.
    return complete_callback('apple')


def complete_callback(provider):
    values = request.args if request.method == 'GET' else request.form
    flow = session.get('social_flow') or {}
    state = values.get('state', '')
    if (not configured(provider) or flow.get('provider') != provider or
            flow.get('expires', 0) < time.time() or not state or len(state) > 256 or not state.isascii() or
            not secrets.compare_digest(state, flow.get('state', ''))):
        return fail(provider)
    if values.get('error'):
        return fail(provider, 'Sign-in was cancelled or unavailable. You can try again or use email.')
    if not values.get('code') or current_user.is_authenticated:
        return fail(provider)
    try:
        token = client_for(provider).authorize_access_token(
            claims_options={'iss': {'essential': True, 'values': [ISSUERS[provider]] + (['accounts.google.com'] if provider == 'google' else [])},
                            'aud': {'essential': True, 'value': current_app.config[f'{provider.upper()}_CLIENT_ID']},
                            'sub': {'essential': True}}, leeway=30)
        # Authlib validates JWKS signature, issuer, audience, expiry and nonce.
        info = token.get('userinfo')
        if not token.get('id_token') or not info or info.get('nonce') != flow.get('nonce') or not isinstance(info.get('sub'), str) or not 0 < len(info['sub']) <= 255:
            raise ValueError('Invalid identity')
        identity = SocialIdentity.query.filter_by(provider=provider, provider_subject=info['sub']).first()
        clear_state(provider)
        if identity:
            return sign_in(identity.user)
        email = info.get('email')
        verified = info.get('email_verified') in (True, 'true')
        if not verified or not isinstance(email, str) or not 3 <= len(email) <= 180 or '@' not in email:
            raise ValueError('Verified email required')
        # Store only the verified identity proof, never access/refresh/ID tokens.
        session['social_pending'] = {'provider': provider, 'sub': info['sub'], 'email': email.strip().lower(),
                                     'expires': time.time() + 600}
        return redirect(url_for('auth.social_finish'))
    except Exception:
        db.session.rollback()
        return fail(provider)


@auth_bp.route('/social/finish', methods=['GET', 'POST'])
def social_finish():
    pending = session.get('social_pending') or {}
    if pending.get('expires', 0) < time.time() or pending.get('provider') not in ISSUERS or current_user.is_authenticated:
        session.pop('social_pending', None)
        return redirect(url_for('auth.login'))
    existing = User.query.filter_by(email=pending['email']).first()
    if existing and not allowed(existing):
        session.pop('social_pending', None)
        abort(403)
    if request.method == 'POST':
        try:
            if existing:
                if not existing.check_password(request.form.get('password', '')):
                    flash('Password is incorrect. Use account recovery if needed.', 'error')
                    return render_template('auth/social_finish.html', pending=pending, existing=existing), 400
                user = existing
            else:
                name = request.form.get('full_name', '').strip()
                business_name = request.form.get('business_name', '').strip()
                if not name or len(name) > 120 or not business_name or len(business_name) > 140:
                    flash('Enter your name and business name within the displayed limits.', 'error')
                    return render_template('auth/social_finish.html', pending=pending, existing=None), 400
                user = User(full_name=name, email=pending['email'])
                # Unusable random password keeps existing schema and reset-password compatible.
                user.set_password(secrets.token_urlsafe(64))
                db.session.add(user); db.session.flush()
                now = datetime.utcnow()
                business = Business(user_id=user.id, name=business_name, subscription_plan='starter',
                                    subscription_status='inactive', trial_started_at=now, trial_ends_at=now)
                db.session.add(business); db.session.flush()
                if current_app.config.get('SUBSCRIPTIONS_ENABLED'):
                    db.session.add(AccountBilling(user_id=user.id, trial_eligible=True, primary_business_id=business.id))
                from app.admin.routes import log
                log('USER_REGISTERED', f'User {user.id} registered.', actor=user, business_id=business.id)
                log('BUSINESS_CREATED', f'Business {business.id} created.', actor=user, business_id=business.id)
            first_verification = user.email_verified_at is None
            user.email_verified_at = user.email_verified_at or datetime.utcnow()
            db.session.add(SocialIdentity(user_id=user.id, provider=pending['provider'], provider_subject=pending['sub']))
            db.session.commit()
        except IntegrityError:
            # Flush and commit conflicts roll back the entire new workspace.
            db.session.rollback()
            return fail(pending['provider'], 'This identity was already linked. Please sign in again.')
        if first_verification:
            safely_send(send_welcome_email, user)
        return sign_in(user)
    return render_template('auth/social_finish.html', pending=pending, existing=existing)


@auth_bp.after_request
def private_auth_response(response):
    if request.endpoint and ('social' in request.endpoint or 'apple_complete' in request.endpoint):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
    return response
