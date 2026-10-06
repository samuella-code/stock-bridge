"""Slow diagnostics must not disclose request/session/stack values."""
import logging
from flask import Flask
import runtime_diagnostics as diagnostics


def test_slow_stack_logs_locations_without_locals(monkeypatch, caplog):
    callbacks=[]
    class Timer:
        def __init__(self, delay, callback): callbacks.append(callback)
        def start(self): pass
    monkeypatch.setattr(diagnostics.threading, 'Timer', Timer)
    sensitive_value = 'never-print-oauth-or-session-secret'
    with caplog.at_level(logging.WARNING, logger='stockbridge.runtime'):
        diagnostics._watch('request GET /')
        callbacks[0]()
    assert 'slow phase=request GET /' in caplog.text
    assert 'test_slow_stack_logs_locations_without_locals' in caplog.text
    assert sensitive_value not in caplog.text


def test_request_watch_is_cancelled_and_query_strings_are_not_logged(monkeypatch, caplog):
    timers=[]
    class Timer:
        def __init__(self, delay, callback): self.cancelled=False; timers.append(self)
        def start(self): pass
        def cancel(self): self.cancelled=True
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setattr(diagnostics.threading, 'Timer', Timer)
    app=Flask(__name__)
    diagnostics.register_request_diagnostics(app)
    app.add_url_rule('/probe','probe',lambda: 'OK')
    with caplog.at_level(logging.INFO):
        response=app.test_client().get('/probe?code=secret-code&email=private-email')
    assert response.status_code==200 and all(t.cancelled for t in timers)
    assert 'request start method=GET route=/probe' in caplog.text
    assert 'request end status=200' in caplog.text
    assert 'secret-code' not in caplog.text and 'private-email' not in caplog.text


def test_startup_watch_cancels_on_exception(monkeypatch):
    cancelled=[]
    class Timer:
        def cancel(self): cancelled.append(True)
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setattr(diagnostics,'_watch',lambda label: Timer())
    try:
        with diagnostics.startup_watch(): raise RuntimeError('startup failed')
    except RuntimeError: pass
    assert cancelled==[True]
