import hashlib
import hmac
import json
from datetime import datetime

import pytest

from app import create_app, db
from app.email_service import (
    password_reset_token, send_password_reset_email, send_verification_email,
    verification_token,
)
from app.models import Business, Payment, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test", "PAYSTACK_SECRET_KEY": "sk_test_dummy",
        "PAYSTACK_PUBLIC_KEY": "pk_test_dummy", "LIFETIME_PRICE_NAIRA": 3000,
        "SMTP_HOST": "smtp.example.test", "SMTP_FROM_EMAIL": "sender@example.test",
        "SMTP_FROM_NAME": "StockBridge", "SMTP_USE_TLS": False})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def messages(app, monkeypatch):
    sent = []
    class SMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_message(self, message): sent.append(message)
    monkeypatch.setattr("app.email_service.smtplib.SMTP", SMTP)
    return sent


def account(app, *, verified=True):
    with app.app_context():
        user = User(full_name="Ada Shopkeeper", email="ada@example.test",
            email_verified_at=datetime.utcnow() if verified else None)
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Business(user_id=user.id, name="Ada Mart",
            subscription_status="inactive", subscription_plan="starter"))
        db.session.commit()
        return user.id


def test_verification_resend_and_welcome_only_on_first_verification(app, messages, monkeypatch):
    user_id = account(app, verified=False)
    client = app.test_client()
    with app.test_request_context("/"):
        user = db.session.get(User, user_id)
        assert send_verification_email(user)
        token = verification_token(user.email)
    assert messages[0]["From"] == "StockBridge <sender@example.test>"
    assert messages[0].get_body(preferencelist=("html",)) is not None
    assert "expires in 24 hours" in messages[0].get_body(preferencelist=("plain",)).get_content()
    assert f"/auth/verify/{token}" in messages[0].get_body(preferencelist=("plain",)).get_content()
    client.post("/auth/login", data={"email": "ada@example.test", "password": "password123"})
    assert client.post("/auth/resend-verification").status_code == 302
    assert len(messages) == 2
    assert client.get(f"/auth/verify/{token}").status_code == 302
    assert len(messages) == 3
    assert "₦3,000 once" in messages[2].get_body(preferencelist=("plain",)).get_content()
    client.get(f"/auth/verify/{token}")
    assert len(messages) == 3


def test_password_reset_and_changed_notification(app, messages):
    user_id = account(app)
    client = app.test_client()
    with app.test_request_context("/"):
        user = db.session.get(User, user_id)
        assert send_password_reset_email(user)
        token = password_reset_token(user.email)
    assert f"/auth/reset-password/{token}" in messages[0].get_body(preferencelist=("html",)).get_content()
    assert "expires in 1 hour" in messages[0].get_body(preferencelist=("plain",)).get_content()
    client.post(f"/auth/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"})
    assert len(messages) == 2
    assert "password was changed" in messages[1]["Subject"]


def _signed_event(payment):
    event = {"event": "charge.success", "data": {"status": "success",
        "reference": payment.reference, "amount": 300000, "currency": "NGN",
        "domain": "test", "metadata": {"customer_email": payment.customer_email,
        "product": "stockbridge_lifetime"}}}
    payload = json.dumps(event).encode()
    signature = hmac.new(b"sk_test_dummy", payload, hashlib.sha512).hexdigest()
    return payload, signature


def test_verified_webhook_and_callback_send_only_one_receipt(app, messages, monkeypatch):
    user_id = account(app)
    client = app.test_client()
    with app.app_context():
        payment = Payment(customer_email="ada@example.test", reference="SB-receipt",
            amount_kobo=300000)
        db.session.add(payment)
        db.session.commit()
        payload, signature = _signed_event(payment)
    for _ in range(2):
        assert client.post("/payments/webhook", data=payload,
            headers={"x-paystack-signature": signature}).status_code == 200
    client.post("/auth/login", data={"email": "ada@example.test", "password": "password123"})
    assert client.get("/payments/callback?reference=SB-receipt").status_code == 302
    assert len(messages) == 1
    plain = messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "₦3,000.00" in plain and "NGN" in plain and "SB-receipt" in plain
    assert "One-time payment. No subscription. No recurring charges." in plain
    with app.app_context():
        assert Business.query.one().has_write_access
        assert Payment.query.one().receipt_email_sent_at


def test_unverified_payment_cannot_send_receipt(app, messages):
    account(app)
    client = app.test_client()
    with app.app_context():
        payment = Payment(customer_email="ada@example.test", reference="SB-invalid",
            amount_kobo=300000)
        db.session.add(payment)
        db.session.commit()
        payload, signature = _signed_event(payment)
    event = json.loads(payload)
    event["data"]["amount"] = 100
    payload = json.dumps(event).encode()
    signature = hmac.new(b"sk_test_dummy", payload, hashlib.sha512).hexdigest()
    client.post("/payments/webhook", data=payload, headers={"x-paystack-signature": signature})
    assert messages == []
    with app.app_context():
        assert not Business.query.one().has_write_access


def test_email_failure_does_not_undo_verified_access_and_can_retry(app, messages, monkeypatch):
    account(app)
    client = app.test_client()
    with app.app_context():
        payment = Payment(customer_email="ada@example.test", reference="SB-retry",
            amount_kobo=300000)
        db.session.add(payment)
        db.session.commit()
        payload, signature = _signed_event(payment)
    original = __import__("app.email_service", fromlist=["send_payment_success_email"]).send_payment_success_email
    monkeypatch.setattr("app.email_service.send_payment_success_email",
        lambda *args: (_ for _ in ()).throw(RuntimeError("SMTP unavailable")))
    assert client.post("/payments/webhook", data=payload,
        headers={"x-paystack-signature": signature}).status_code == 200
    with app.app_context():
        assert Business.query.one().has_write_access
        assert Payment.query.one().receipt_email_sent_at is None
    monkeypatch.setattr("app.email_service.send_payment_success_email", original)
    client.post("/payments/webhook", data=payload, headers={"x-paystack-signature": signature})
    assert len(messages) == 1


def test_production_urls_use_customer_host_and_no_preview_or_admin(app, messages, monkeypatch):
    user_id = account(app)
    monkeypatch.setenv("VERCEL_ENV", "production")
    with app.test_request_context("/", base_url="https://preview.example.test"):
        user = db.session.get(User, user_id)
        send_verification_email(user)
    text = messages[0].get_body(preferencelist=("plain",)).get_content()
    assert "https://stock-bridge-one.vercel.app/auth/verify/" in text
    assert "preview.example.test" not in text and "stock-bridge-admin" not in text


def test_admin_suspension_and_reactivation_send_after_commit(app, messages):
    user_id = account(app)
    with app.app_context():
        admin = User(full_name="Administrator", email="operator@example.test", role="admin",
            email_verified_at=datetime.utcnow())
        admin.set_password("long-safe-password123")
        db.session.add(admin)
        db.session.commit()
        business_id = Business.query.one().id
    client = app.test_client()
    assert client.post("/admin/login", data={"email": "operator@example.test",
        "password": "long-safe-password123"}).status_code == 302
    assert client.post(f"/admin/users/{user_id}/suspension",
        data={"confirm": "on", "reason": "Account review"}).status_code == 302
    assert len(messages) == 1 and "suspended" in messages[0]["Subject"]
    with app.app_context():
        assert db.session.get(User, user_id).suspended_at
    assert client.post(f"/admin/users/{user_id}/suspension",
        data={"confirm": "on", "reason": "Review complete"}).status_code == 302
    assert len(messages) == 2 and "reactivated" in messages[1]["Subject"]
    assert client.post(f"/admin/businesses/{business_id}/suspension",
        data={"confirm": "on", "reason": "Inventory review"}).status_code == 302
    assert client.post(f"/admin/businesses/{business_id}/suspension",
        data={"confirm": "on", "reason": "Resolved"}).status_code == 302
    assert len(messages) == 4
    assert "business has been suspended" in messages[2]["Subject"]
    assert "business has been reactivated" in messages[3]["Subject"]
    for message in messages:
        assert message.get_body(preferencelist=("html",))
        assert message.get_body(preferencelist=("plain",))
        assert "Account review" not in message.as_string()
