import hashlib
import hmac
import io
import json
from datetime import datetime
from urllib.error import HTTPError
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


def test_logged_in_user_cannot_initialize_retired_payment(client, app, monkeypatch):
    create_account(client, app)
    calls=[]
    monkeypatch.setattr("app.payments.routes.initialize_transaction", lambda *args: calls.append(args))
    assert client.post("/payments/initialize").status_code == 410
    assert calls == []
    with app.app_context(): assert Payment.query.count() == 0


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
    assert client.post("/payments/initialize").status_code==410
    with app.app_context(): assert Payment.query.count()==0


def test_paystack_http_error_retains_only_safe_diagnostic_fields(monkeypatch):
    from app.payments.service import PaystackError, initialize_transaction
    def rejected(_request, timeout):
        raise HTTPError("https://api.paystack.co/transaction/initialize", 401,
            "Unauthorized", {}, io.BytesIO(json.dumps({
                "status":False,"message":"Sensitive provider detail sk_live_do_not_log",
                "code":"invalid_api_key"}).encode()))
    monkeypatch.setattr("app.payments.service.urlopen", rejected)
    with pytest.raises(PaystackError) as failure:
        initialize_transaction("sk_live_do_not_log","ada@example.com",300000,"SB-error",
            "https://stock-bridge-one.vercel.app/payments/callback")
    assert failure.value.status_code==401
    assert failure.value.code=="invalid_api_key"
    assert "sk_live_do_not_log" not in str(failure.value)


def test_paystack_initialize_sends_documented_user_agent(monkeypatch):
    from app.payments.service import initialize_transaction
    seen=[]
    class Reply:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self): return b'{"status":true,"data":{"reference":"SB-ua","authorization_url":"https://checkout.paystack.com/abc"}}'
    def fake_open(request, timeout):
        seen.append(request)
        return Reply()
    monkeypatch.setattr("app.payments.service.urlopen",fake_open)
    initialize_transaction("sk_test_hidden","ada@example.com",300000,"SB-ua",
        "https://stock-bridge-one.vercel.app/payments/callback")
    assert seen[0].get_header("User-agent").startswith("Mozilla/5.0")
    assert seen[0].get_header("Authorization")=="Bearer sk_test_hidden"


def test_paystack_cloudflare_error_is_classified_without_logging_response(monkeypatch):
    from app.payments.service import PaystackError, verify_transaction
    def rejected(_request, timeout):
        raise HTTPError("https://api.paystack.co/transaction/verify/SB-ua",403,
            "Forbidden",{"cf-ray":"hidden"},io.BytesIO(b"<html>Cloudflare blocked request</html>"))
    monkeypatch.setattr("app.payments.service.urlopen",rejected)
    with pytest.raises(PaystackError) as failure:
        verify_transaction("sk_test_hidden","SB-ua")
    assert failure.value.status_code==403 and failure.value.code=="cloudflare_block"
    assert "Cloudflare blocked request" not in str(failure.value)


def test_live_checkout_auth_failure_is_clear_and_creates_no_payment(client, app, monkeypatch):
    from app.payments.service import PaystackError
    create_account(client, app)
    app.config.update(PAYSTACK_SECRET_KEY="sk_live_do_not_log",PAYSTACK_PUBLIC_KEY="pk_live_public")
    warnings=[]
    monkeypatch.setattr(app.logger,"warning",lambda *args: warnings.append(args))
    monkeypatch.setattr("app.payments.routes.initialize_transaction",lambda *args:
        (_ for _ in ()).throw(PaystackError("Rejected",status_code=401,code="invalid_api_key")))
    response=client.post("/payments/initialize",follow_redirects=True)
    assert response.status_code == 410 and b"View Basic and Plus plans" in response.data
    assert warnings == []
    assert "sk_live_do_not_log" not in repr(warnings)
    with app.app_context():
        assert Payment.query.count()==0


def test_webhook_rejects_bad_signature(client):
    assert client.post("/payments/webhook",data=b"{}",content_type="application/json",headers={"x-paystack-signature":"wrong"}).status_code==401
