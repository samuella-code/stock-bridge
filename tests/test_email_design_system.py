"""Regression tests for the shared customer email shell and sender identity."""
from email.message import EmailMessage

import pytest

from app import create_app
from app.email_service import _send_email


@pytest.fixture
def app():
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "email-design-test",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": 587,
        "SMTP_FROM_EMAIL": "sender@example.test",
        "SMTP_USE_TLS": False,
    })


def test_shared_email_shell_uses_white_stockbridge_header_and_optional_footer(app):
    from flask import render_template_string

    with app.app_context():
        html = render_template_string(
            "{% extends 'email/base.html' %}{% block content %}<h1>Test</h1>{% endblock %}",
            preheader="A useful preview line for this test email.",
            email_reason="You received this because you requested a test.",
        )

    assert "max-width:600px" in html
    assert "background-color:#ffffff" in html
    assert ">SB</td>" in html
    assert "StockBridge</td>" in html
    assert "Know your stock. Understand your profit." in html
    assert "Business made clearer" not in html
    assert "#102437" not in html
    assert "A useful preview line for this test email." in html
    assert "[YOUR WHATSAPP NUMBER]" not in html
    assert "[COMPANY ADDRESS]" not in html


def test_sender_name_is_stockbridge_and_staging_is_marked_in_subject(app, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.email_service.smtplib.SMTP", SMTP)
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "feat/unified-customer-email-system")
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)

    with app.app_context():
        assert _send_email("Low stock", "customer@example.test", "Plain text body")

    assert sent[0]["From"] == "StockBridge <sender@example.test>"
    assert sent[0]["Subject"] == "[STAGING] Low stock"


def test_staging_project_production_deployment_is_marked(app, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.email_service.smtplib.SMTP", SMTP)
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "prj_yeCWXPd44jShVUGHAgJu9khMjOMv")

    with app.app_context():
        assert _send_email("Low stock", "customer@example.test", "Plain text body")

    assert sent[0]["Subject"] == "[STAGING] Low stock"


def test_production_sender_subject_is_not_staging_marked(app, monkeypatch):
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setattr("app.email_service.smtplib.SMTP", SMTP)
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setenv("VERCEL_PROJECT_ID", "prj_ahZe3mfUk4MvejILpj9Md9QPP4fd")
    monkeypatch.delenv("VERCEL_GIT_COMMIT_REF", raising=False)

    with app.app_context():
        assert _send_email("Payment received", "customer@example.test", "Plain text body")

    assert sent[0]["From"] == "StockBridge <sender@example.test>"
    assert sent[0]["Subject"] == "Payment received"


def test_remaining_customer_email_templates_use_shared_visual_system(app):
    from types import SimpleNamespace
    from flask import render_template

    payment = SimpleNamespace(
        currency="NGN",
        reference="SBS-1234567890",
    )
    business = SimpleNamespace(name="Sample Business")
    contexts = {
        "billing": {"name": "Amina Yusuf", "message": "Your trial ends soon.", "link": "https://example.test/billing"},
        "payment_success": {"name": "Amina Yusuf", "business": business, "payment": payment,
                            "amount": "₦3,000.00", "paid_at": "26/11/2026 12:00 WAT",
                            "link": "https://example.test/billing"},
        "account_status": {"name": "Amina Yusuf", "active": True, "kind": "account",
                           "link": "https://example.test/login"},
        "password_changed": {"name": "Amina Yusuf", "link": "https://example.test/reset"},
        "welcome": {"name": "Amina Yusuf", "link": "https://example.test/dashboard"},
        "staff_invitation": {"invitation_url": "https://example.test/staff/invite"},
    }

    with app.app_context():
        for template, context in contexts.items():
            html = render_template(f"email/{template}.html", preheader="A short preview line.", **context)
            assert "max-width:600px" in html, template
            assert "Know your stock. Understand your profit." in html, template
            assert "Business made clearer" not in html, template
            assert "#102437" not in html, template
            assert "min-height:44px" in html, template


def test_inventory_email_footer_can_link_to_alert_settings_and_daily_summary(app):
    from flask import render_template_string

    with app.app_context():
        html = render_template_string(
            "{% extends 'email/base.html' %}{% block content %}<h1>Low stock</h1>{% endblock %}",
            preheader="Only a few units remain.",
            email_reason="You get this email because low-stock alerts are on for Shop.",
            change_alerts_url="https://example.test/notifications/#notificationPreferences",
            summary_preference_url="https://example.test/notifications/#notificationPreferences",
        )

    assert "Change alerts" in html
    assert "Send me one daily summary instead" in html
    assert "low-stock alerts are on for Shop" in html
