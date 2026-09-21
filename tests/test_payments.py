import hashlib
import hmac
import json

import pytest

from app import create_app, db
from app.models import Business, Payment


@pytest.fixture
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SECRET_KEY": "test",
        "PAYSTACK_SECRET_KEY": "sk_test_secret",
        "LIFETIME_PRICE_NAIRA": 3000,
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def create_account(client):
    client.post("/auth/signup", data={"full_name": "Ada Owner", "business_name": "Ada Mini Mart", "email": "ada@example.com", "password": "password123"})


def successful_transaction(payment):
    return {"status": "success", "reference": payment.reference, "amount": payment.amount_kobo, "currency": "NGN", "metadata": {"business_id": payment.business_id, "product": "stockbridge_lifetime"}}


def test_initialize_creates_payment_and_redirects(client, app, monkeypatch):
    create_account(client)
    monkeypatch.setattr("app.payments.routes.initialize_transaction", lambda *args: {"authorization_url": "https://checkout.paystack.test/example"})
    response = client.post("/payments/initialize")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://checkout.paystack.test/example"
    with app.app_context():
        payment = Payment.query.one()
        assert payment.amount_kobo == 300_000
        assert payment.status == "initialized"


def test_callback_activates_lifetime_access(client, app, monkeypatch):
    create_account(client)
    with app.app_context():
        business = Business.query.one()
        payment = Payment(business_id=business.id, reference="SB-test", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        verified = successful_transaction(payment)
    monkeypatch.setattr("app.payments.routes.verify_transaction", lambda *args: verified)
    response = client.get("/payments/callback?reference=SB-test")
    assert response.status_code == 200
    assert b"permanent StockBridge access" in response.data
    with app.app_context():
        business = Business.query.one()
        assert business.subscription_plan == "lifetime"
        assert business.subscription_status == "active"
        assert business.subscription_ends_at is None


def test_wrong_amount_does_not_activate(client, app, monkeypatch):
    create_account(client)
    with app.app_context():
        business = Business.query.one()
        payment = Payment(business_id=business.id, reference="SB-wrong", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        verified = successful_transaction(payment)
        verified["amount"] = 100
    monkeypatch.setattr("app.payments.routes.verify_transaction", lambda *args: verified)
    response = client.get("/payments/callback?reference=SB-wrong", follow_redirects=True)
    assert b"could not be verified" in response.data
    with app.app_context():
        assert Business.query.one().subscription_plan == "starter"


def test_signed_webhook_activates_access(client, app):
    create_account(client)
    with app.app_context():
        business = Business.query.one()
        payment = Payment(business_id=business.id, reference="SB-hook", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        event = {"event": "charge.success", "data": successful_transaction(payment)}
    payload = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_secret", payload, hashlib.sha512).hexdigest()
    response = client.post("/payments/webhook", data=payload, content_type="application/json", headers={"x-paystack-signature": signature})
    assert response.status_code == 200
    with app.app_context():
        assert Payment.query.one().status == "success"
        assert Business.query.one().subscription_plan == "lifetime"


def test_webhook_rejects_bad_signature(client):
    response = client.post("/payments/webhook", data=b"{}", content_type="application/json", headers={"x-paystack-signature": "wrong"})
    assert response.status_code == 401
