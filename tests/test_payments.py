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
    return {"status":"success", "reference":payment.reference, "amount":amount,
        "currency":"NGN", "domain":"test", "metadata":{"customer_email":payment.customer_email,
            "product":"stockbridge_lifetime"}}


def test_logged_in_user_initializes_payment(client, app, monkeypatch):
    create_account(client, app)
    calls=[]
    def start(key, email, amount, reference, callback):
        calls.append((key, email, amount, callback))
        return {"reference":reference,"authorization_url":"https://checkout.paystack.com/test-session"}
    monkeypatch.setattr("app.payments.routes.initialize_transaction", start)
    response = client.post("/payments/initialize")
    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.paystack.com/test-session"
    assert calls[0][:3] == ("sk_test_secret", "ada@example.com", 300_000)
    assert calls[0][3].endswith("/payments/callback")
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


def test_callback_verifies_with_paystack_before_unlocking(client, app, monkeypatch):
    create_account(client, app)
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-verify",amount_kobo=300_000)
        db.session.add(payment); db.session.commit()
        details=transaction(payment)
        details["metadata"]=json.dumps(details["metadata"])
    monkeypatch.setattr("app.payments.routes.verify_transaction",lambda key, ref: details)
    response=client.get("/payments/callback?reference=SB-verify")
    assert response.status_code==302 and response.headers["Location"].endswith("/dashboard")
    with app.app_context():
        assert Business.query.one().has_write_access
        assert Payment.query.one().status=="success"


def test_callback_rejects_invalid_amount_and_other_customer(client, app, monkeypatch):
    create_account(client, app)
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-no-charge",amount_kobo=300_000)
        db.session.add(payment); db.session.commit()
        bad=transaction(payment,amount=100)
    monkeypatch.setattr("app.payments.routes.verify_transaction",lambda key, ref: bad)
    assert client.get("/payments/callback?reference=SB-no-charge").status_code==200
    with app.app_context(): assert not Business.query.one().has_write_access
    client.post("/auth/logout")
    assert client.get("/payments/callback?reference=SB-no-charge").status_code==302


def test_webhook_rejects_test_mode_when_live_key_is_configured(client, app):
    app.config["PAYSTACK_SECRET_KEY"]="sk_live_secret"
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-live",amount_kobo=300_000)
        db.session.add(payment); db.session.commit()
        event={"event":"charge.success","data":transaction(payment)}
    payload=json.dumps(event).encode()
    signature=hmac.new(b"sk_live_secret",payload,hashlib.sha512).hexdigest()
    assert client.post("/payments/webhook",data=payload,headers={"x-paystack-signature":signature}).status_code==200
    with app.app_context(): assert Payment.query.one().status!="success"


def test_live_webhook_activates_existing_business(client, app):
    create_account(client, app)
    app.config["PAYSTACK_SECRET_KEY"]="sk_live_secret"
    app.config["PAYSTACK_PUBLIC_KEY"]="pk_live_public"
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-real",amount_kobo=300_000)
        db.session.add(payment); db.session.commit()
        details=transaction(payment)
        details["domain"]="live"
    payload=json.dumps({"event":"charge.success","data":details}).encode()
    signature=hmac.new(b"sk_live_secret",payload,hashlib.sha512).hexdigest()
    for _ in range(2):
        assert client.post("/payments/webhook",data=payload,
            headers={"x-paystack-signature":signature}).status_code==200
    with app.app_context():
        assert Business.query.one().has_write_access
        assert Payment.query.one().status=="success"
        assert Payment.query.count()==1


def test_preview_webhook_cannot_activate_production_data(client, app, monkeypatch):
    monkeypatch.setenv("VERCEL_ENV","preview")
    with app.app_context():
        payment=Payment(customer_email="ada@example.com",reference="SB-preview",amount_kobo=300_000)
        db.session.add(payment); db.session.commit()
        payload=json.dumps({"event":"charge.success","data":transaction(payment)}).encode()
    signature=hmac.new(b"sk_test_secret",payload,hashlib.sha512).hexdigest()
    assert client.post("/payments/webhook",data=payload,
        headers={"x-paystack-signature":signature}).status_code==404
    with app.app_context(): assert Payment.query.one().status!="success"


def test_failed_initialization_does_not_save_a_pending_payment(client, app, monkeypatch):
    from app.payments.service import PaystackError
    create_account(client, app)
    monkeypatch.setattr("app.payments.routes.initialize_transaction",
        lambda *args: (_ for _ in ()).throw(PaystackError("rejected")))
    assert client.post("/payments/initialize").status_code==302
    with app.app_context(): assert Payment.query.count()==0


def test_webhook_rejects_bad_signature(client):
    assert client.post("/payments/webhook",data=b"{}",content_type="application/json",headers={"x-paystack-signature":"wrong"}).status_code==401
