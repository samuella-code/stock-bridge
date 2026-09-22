import hashlib
import hmac
import json
from datetime import datetime
import pytest

from app import create_app, db
from app.models import Business, Payment, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test", "PAYSTACK_SECRET_KEY": "sk_test_secret", "PAYSTACK_PUBLIC_KEY": "pk_test_public", "LIFETIME_PRICE_NAIRA": 3000})
    with app.app_context():
        db.create_all(); yield app; db.session.remove(); db.drop_all()


@pytest.fixture
def client(app): return app.test_client()


def create_account(client, app):
    with app.app_context():
        user = User(full_name="Ada", email="ada@example.com", email_verified_at=datetime.utcnow()); user.set_password("password123")
        db.session.add(user); db.session.flush(); db.session.add(Business(user_id=user.id, name="Ada Mart", subscription_status="inactive")); db.session.commit()
    client.post("/auth/login", data={"email":"ada@example.com", "password":"password123"})


def transaction(payment, amount=300_000):
    return {"status":"success", "reference":payment.reference, "amount":amount, "currency":"NGN", "metadata":{"customer_email":payment.customer_email, "product":"stockbridge_lifetime"}}


def test_logged_in_user_initializes_payment(client, app):
    create_account(client, app)
    response = client.post("/payments/initialize")
    assert response.status_code == 200
    assert b"Paystack secure checkout" in response.data
    with app.app_context():
        payment = Payment.query.one(); assert payment.customer_email == "ada@example.com"; assert payment.amount_kobo == 300_000


def test_verified_payment_unlocks_stock_tools(client, app):
    create_account(client, app)
    with app.app_context():
        payment = Payment(customer_email="ada@example.com", reference="SB-test", amount_kobo=300_000, status="success"); db.session.add(payment); db.session.commit()
    response = client.get("/payments/callback?reference=SB-test")
    assert response.headers["Location"].endswith("/dashboard")
    with app.app_context():
        assert Business.query.one().has_write_access
        assert Payment.query.one().business_id == Business.query.one().id


def test_wrong_amount_does_not_unlock(client, app):
    create_account(client, app)
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-wrong",amount_kobo=300_000); db.session.add(payment); db.session.commit(); event={"event":"charge.success","data":transaction(payment,100)}
    payload=json.dumps(event,separators=(",",":")).encode(); signature=hmac.new(b"sk_test_secret",payload,hashlib.sha512).hexdigest()
    client.post("/payments/webhook",data=payload,content_type="application/json",headers={"x-paystack-signature":signature})
    with app.app_context(): assert not Business.query.one().has_write_access


def test_signed_webhook_confirms_payment(client, app):
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-hook",amount_kobo=300_000); db.session.add(payment); db.session.commit(); event={"event":"charge.success","data":transaction(payment)}
    payload=json.dumps(event,separators=(",",":")).encode(); signature=hmac.new(b"sk_test_secret",payload,hashlib.sha512).hexdigest()
    assert client.post("/payments/webhook",data=payload,content_type="application/json",headers={"x-paystack-signature":signature}).status_code==200
    with app.app_context(): assert Payment.query.one().status=="success"


def test_webhook_rejects_bad_signature(client):
    assert client.post("/payments/webhook",data=b"{}",content_type="application/json",headers={"x-paystack-signature":"wrong"}).status_code==401
