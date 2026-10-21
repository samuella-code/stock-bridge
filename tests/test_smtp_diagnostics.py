"""Offline SMTP transport diagnostics; no database or network connections."""
import logging
import re
import smtplib

import pytest
from flask import Flask

from app.email_service import _send_email


@pytest.mark.parametrize('failure', [
    'connection', 'starttls', 'authentication', 'message_transmission',
    'connection_closure', None,
])
def test_smtp_stage_diagnostics(monkeypatch, caplog, failure):
    app = Flask(__name__)
    app.config.update(SMTP_HOST='smtp.example.invalid', SMTP_PORT=587,
        SMTP_FROM_EMAIL='sender@example.invalid', SMTP_FROM_NAME='StockBridge',
        SMTP_USERNAME='private-user', SMTP_PASSWORD='private-password', SMTP_USE_TLS=True)
    calls = []
    error = smtplib.SMTPServerDisconnected('private-password private-token private-body')

    def step(stage):
        calls.append(stage)
        if failure == stage:
            raise error

    class SMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ('smtp.example.invalid', 587, 15)
            step('connection')
        def __enter__(self):
            return self
        def __exit__(self, *args):
            step('connection_closure')
        def starttls(self):
            step('starttls')
        def login(self, username, password):
            assert (username, password) == ('private-user', 'private-password')
            step('authentication')
        def send_message(self, message):
            assert message['To'] == 'recipient@example.invalid'
            step('message_transmission')

    monkeypatch.setattr('app.email_service.smtplib.SMTP', SMTP)
    ticks = iter([10.0, 10.125])
    monkeypatch.setattr('app.email_service.time.monotonic', lambda: next(ticks))
    with app.app_context(), caplog.at_level(logging.ERROR, logger=app.logger.name):
        if failure:
            with pytest.raises(smtplib.SMTPServerDisconnected) as caught:
                _send_email('private-subject', 'recipient@example.invalid', 'private-body')
            assert caught.value is error
        else:
            assert _send_email('private-subject', 'recipient@example.invalid', 'private-body') is True
    records = [r for r in caplog.records if r.getMessage().startswith('smtp_failure')]
    if failure:
        assert len(records) == 1
        assert re.fullmatch(
            rf'smtp_failure stage={failure} exception_type=SMTPServerDisconnected elapsed_ms=125',
            records[0].getMessage())
        assert records[0].exc_info is None
        if failure != 'connection':
            assert calls[-1] == 'connection_closure'
    else:
        assert records == []
        assert calls == ['connection', 'starttls', 'authentication',
                         'message_transmission', 'connection_closure']
    for secret in ('private-user', 'private-password', 'private-token', 'private-body',
                   'private-subject', 'sender@example.invalid', 'recipient@example.invalid',
                   'smtp.example.invalid'):
        assert secret not in caplog.text
