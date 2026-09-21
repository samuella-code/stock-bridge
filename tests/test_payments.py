import hashlib
import hmac
import json
import pytest
from datetime import datetime

from app import create_app, db
from app.models import Business, Payment, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test", "PAYSTACK_SECRET_KEY": "sk_test_secret", "LIFETIME_PRICE_NAIRA": 3000})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def transaction(payment, amount=300_000):
    return {"status": "success", "reference": payment.reference, "amount": amount, "currency": "NGN", "metadata": {"customer_email": payment.customer_email, "product": "stockbridge_lifetime"}}


def test_initialize_before_signup(client, app, monkeypatch):
    monkeypatch.setattr("app.payments.routes.initialize_transaction", lambda *args: {"authorization_url": "https://checkout.paystack.test/example"})
    response = client.post("/payments/initialize", data={"email": "ada@example.com"})
    assert response.headers["Location"] == "https://checkout.paystack.test/example"
    with app.app_context():
        payment = Payment.query.one()
        assert payment.business_id is None
        assert payment.customer_email == "ada@example.com"
        assert payment.amount_kobo == 300_000


def test_verified_payment_allows_one_account_and_dashboard(client, app, monkeypatch):
    monkeypatch.setattr("app.auth.routes.send_verification_email", lambda user: True)
    with app.app_context():
        payment = Payment(customer_email="ada@example.com", reference="SB-test", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        verified = transaction(payment)
    monkeypatch.setattr("app.payments.routes.verify_transaction", lambda *args: verified)
    response = client.get("/payments/callback?reference=SB-test", follow_redirects=True)
    assert b"Payment confirmed" in response.data
    response = client.post("/auth/signup", data={"full_name": "Ada Owner", "business_name": "Ada Mart", "password": "password123"}, follow_redirects=True)
    assert b"Verify your email" in response.data
    with app.app_context():
        assert User.query.one().email == "ada@example.com"
        assert Business.query.one().subscription_plan == "lifetime"
        assert Payment.query.one().business_id == Business.query.one().id


def test_wrong_amount_does_not_allow_signup(client, app, monkeypatch):
    with app.app_context():
        payment = Payment(customer_email="ada@example.com", reference="SB-wrong", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        verified = transaction(payment, amount=100)
    monkeypatch.setattr("app.payments.routes.verify_transaction", lambda *args: verified)
    response = client.get("/payments/callback?reference=SB-wrong", follow_redirects=True)
    assert b"could not be verified" in response.data
    assert client.get("/auth/signup").status_code == 302


def test_signed_webhook_confirms_payment(client, app):
    with app.app_context():
        payment = Payment(customer_email="ada@example.com", reference="SB-hook", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        event = {"event": "charge.success", "data": transaction(payment)}
    payload = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(b"sk_test_secret", payload, hashlib.sha512).hexdigest()
    assert client.post("/payments/webhook", data=payload, content_type="application/json", headers={"x-paystack-signature": signature}).status_code == 200
    with app.app_context():
        assert Payment.query.one().status == "success"
        assert Payment.query.one().claim_token


def test_webhook_rejects_bad_signature(client):
    assert client.post("/payments/webhook", data=b"{}", content_type="application/json", headers={"x-paystack-signature": "wrong"}).status_code == 401


def test_existing_inactive_account_can_pay_for_access(client, app, monkeypatch):
    with app.app_context():
        user = User(full_name="Old Owner", email="old@example.com", email_verified_at=datetime.utcnow())
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Business(user_id=user.id, name="Old Shop", subscription_status="inactive"))
        payment = Payment(customer_email=user.email, reference="SB-existing", amount_kobo=300_000)
        db.session.add(payment)
        db.session.commit()
        verified = transaction(payment)
    client.post("/auth/login", data={"email": "old@example.com", "password": "password123"})
    monkeypatch.setattr("app.payments.routes.verify_transaction", lambda *args: verified)
    response = client.get("/payments/callback?reference=SB-existing")
    assert response.headers["Location"].endswith("/dashboard")
    with app.app_context():
        assert Business.query.one().has_write_access
