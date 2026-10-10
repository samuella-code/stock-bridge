"""Synthetic failures only: no hosted resources or provider calls."""
import logging
import re

import pytest
from flask import abort, current_app
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import TooManyRequests

from app import create_app, db
from app.error_handling import log_failure


@pytest.fixture
def application():
    app = create_app({'TESTING': True, 'PROPAGATE_EXCEPTIONS': False,
                      'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                      'SECRET_KEY': 'isolated-error-tests', 'SUBSCRIPTIONS_ENABLED': False})

    @app.get('/probe/<int:status>')
    def probe(status):
        if status == 429:
            raise TooManyRequests(retry_after=30)
        abort(status, description='private-provider-response')

    @app.get('/api/probe/<int:status>')
    def api_probe(status):
        abort(status, description='private-provider-response')

    @app.get('/failure/<token>')
    def failure(token):
        raise OperationalError('private-sql-query', {'password': 'private-sql-value'},
                               RuntimeError('private-provider-response'))

    @app.get('/caught')
    def caught():
        try:
            raise RuntimeError('private-exception-value')
        except RuntimeError:
            current_app.logger.exception('private-email@example.invalid %s', 'private-argument')
            log_failure('email_delivery_failed')
        return 'OK'

    return app


@pytest.mark.parametrize('status', [400, 403, 404, 429, 500])
def test_html_statuses_are_safe_accessible_and_correlated(application, status, caplog):
    with caplog.at_level(logging.WARNING):
        response = application.test_client().get(f'/probe/{status}',
                    headers={'X-Request-ID': 'untrusted-request-id'})
    assert response.status_code == status
    assert response.mimetype == 'text/html'
    assert b'StockBridge' in response.data and b'<main' in response.data
    assert b'name="viewport"' in response.data
    assert b'private-provider-response' not in response.data
    identifier = response.headers['X-Request-ID']
    assert re.fullmatch('[a-f0-9]{32}', identifier)
    assert identifier.encode() in response.data and identifier in caplog.text
    assert 'untrusted-request-id' not in caplog.text
    assert response.headers['Cache-Control'] == 'no-store'
    if status == 429:
        assert response.headers['Retry-After'] == '30'


@pytest.mark.parametrize('status', [400, 403, 404, 429, 500])
def test_api_json_statuses(application, status):
    response = application.test_client().get(f'/api/probe/{status}')
    assert response.status_code == status
    assert response.json['error']['status'] == status
    assert response.json['error']['request_id'] == response.headers['X-Request-ID']
    assert b'private-provider-response' not in response.data


def test_accept_json_and_explicit_401_preserved(application):
    client = application.test_client()
    assert client.get('/probe/400', headers={'Accept': 'application/json'}).is_json
    assert client.get('/probe/401').status_code == 401


def test_unhandled_sql_exception_never_logs_path_parameters_or_headers(application, caplog):
    with caplog.at_level(logging.ERROR):
        response = application.test_client().get(
            '/failure/private-reset-token?code=private-query-token',
            headers={'Authorization': 'Bearer private-bearer', 'Cookie': 'private-cookie'})
    assert response.status_code == 500
    assert 'OperationalError' in caplog.text
    assert response.headers['X-Request-ID'] in caplog.text
    for secret in ('private-reset-token', 'private-query-token', 'private-bearer',
                   'private-cookie', 'private-sql-query', 'private-sql-value', 'private-provider-response'):
        assert secret not in caplog.text and secret.encode() not in response.data
    assert all(record.exc_info is None for record in caplog.records)


def test_caught_exception_records_remain_visible_but_sanitized(application, caplog):
    with caplog.at_level(logging.ERROR):
        response = application.test_client().get('/caught')
    assert response.status_code == 200
    assert 'RuntimeError' in caplog.text and 'email_delivery_failed' in caplog.text
    assert response.headers['X-Request-ID'] in caplog.text
    for secret in ('private-exception-value', 'private-email@example.invalid', 'private-argument'):
        assert secret not in caplog.text


def test_early_csrf_failure_preserves_protection_and_has_identifier(application, caplog):
    application.config['WTF_CSRF_ENABLED'] = True
    with caplog.at_level(logging.WARNING):
        response = application.test_client().post('/auth/login', data={'email': 'private@example.invalid'})
    assert response.status_code == 400
    assert response.headers['X-Request-ID'] in caplog.text
    assert b'private@example.invalid' not in response.data


def test_error_rendering_skips_database_dependent_context(application, monkeypatch):
    def unavailable(*args, **kwargs):
        raise AssertionError('Error rendering attempted database access')
    with application.app_context():
        monkeypatch.setattr(db.session, 'execute', unavailable)
        monkeypatch.setattr(db.session, 'get', unavailable)
        client = application.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = '123'
        # Simulate a DB failure before the access gate; no DB is available to render.
        application.before_request_funcs[None].insert(1, lambda: abort(500))
        response = client.get('/dashboard')
        assert response.status_code == 500
        assert b'Something went wrong' in response.data


def test_database_health_explicit_response_preserved(application, monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise OperationalError('private-query', {'secret': 'private-value'}, RuntimeError('private-url'))
    with application.app_context():
        monkeypatch.setattr(db.session, 'execute', unavailable)
        with caplog.at_level(logging.ERROR):
            response = application.test_client().get('/health')
        assert response.status_code == 503 and response.json == {'status': 'unhealthy'}
        assert 'database_health_failed' in caplog.text
        assert 'private-' not in caplog.text


def test_authentication_redirect_and_explicit_webhook_response_preserved(application):
    client = application.test_client()
    response = client.get('/dashboard')
    assert response.status_code == 302 and '/auth/login' in response.location
    response = client.post('/payments/webhook', data='{}', content_type='application/json')
    assert response.status_code == 401 and response.json == {'status': 'invalid signature'}


def test_request_ids_are_fresh(application):
    client = application.test_client()
    assert client.get('/missing').headers['X-Request-ID'] != client.get('/missing').headers['X-Request-ID']


def test_signup_email_failure_keeps_account_and_logs_no_recipient(application, monkeypatch, caplog):
    from app.models import User
    def smtp_failure(*args, **kwargs):
        raise RuntimeError('private-smtp-password recipient=private@example.invalid')
    monkeypatch.setattr('app.auth.routes.send_verification_email', smtp_failure)
    with application.app_context():
        db.create_all()
        try:
            with caplog.at_level(logging.ERROR):
                response = application.test_client().post('/auth/signup', data={
                    'full_name': 'Owner', 'business_name': 'Example',
                    'email': 'private@example.invalid', 'password': 'test-password-123'})
            assert response.status_code == 302
            assert User.query.filter_by(email='private@example.invalid').count() == 1
            assert 'verification_email_failed' in caplog.text
            assert 'private@example.invalid' not in caplog.text
            assert 'private-smtp-password' not in caplog.text
        finally:
            db.session.remove(); db.drop_all()


def test_safe_email_wrapper_preserves_failure_result_without_exception_text(application, caplog):
    from app.email_service import safely_send
    def failure():
        raise RuntimeError('private-provider-url private-secret')
    with application.test_request_context('/probe?token=private-token'):
        with caplog.at_level(logging.ERROR):
            assert safely_send(failure) is False
        assert 'transactional_email_failed' in caplog.text
        assert 'private-' not in caplog.text


def test_errors_do_not_add_transaction_rollback(application, monkeypatch):
    with application.app_context():
        def forbidden():
            raise AssertionError('Error handler changed rollback semantics')
        monkeypatch.setattr(db.session, 'rollback', forbidden)
        assert application.test_client().get('/probe/500').status_code == 500


def test_slow_diagnostic_uses_same_correlation_without_sensitive_context(application, monkeypatch, caplog):
    import runtime_diagnostics as diagnostics
    callbacks = []
    class Timer:
        def __init__(self, delay, callback): callbacks.append(callback)
        def start(self): pass
        def cancel(self): pass
    monkeypatch.setattr(diagnostics.threading, 'Timer', Timer)
    @application.get('/slow/<token>')
    def slow(token):
        diagnostics._watch('request GET /slow/<token>')
        callbacks[-1]()
        return 'OK'
    with caplog.at_level(logging.WARNING):
        response = application.test_client().get('/slow/private-token')
    assert response.headers['X-Request-ID'] in caplog.text
    assert 'private-token' not in caplog.text


def test_branded_unmatched_404_never_echoes_url(application, caplog):
    with caplog.at_level(logging.WARNING):
        response = application.test_client().get('/private-token?password=private-secret')
    assert response.status_code == 404
    assert 'private-' not in caplog.text
    assert b'private-' not in response.data
