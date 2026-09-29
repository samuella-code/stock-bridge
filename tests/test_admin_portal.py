from datetime import datetime
import re
import time
import pytest
from app import create_app, db
from app.models import AdminPasswordReset, AuditLog, Business, User, Payment

@pytest.fixture
def client():
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:",
        "SECRET_KEY":"test","ADMIN_EMAILS":"owner@example.com"})
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()

def seed(client):
    with client.application.app_context():
        admin=User(full_name="Operator",email="admin@example.com",role="admin",email_verified_at=datetime.utcnow())
        admin.set_password("safe-admin-password123")
        customer=User(full_name="Owner",email="owner@example.com",email_verified_at=datetime.utcnow())
        customer.set_password("customer-password123")
        db.session.add_all([admin,customer])
        db.session.flush()
        business=Business(user_id=customer.id,name="Shop",subscription_plan="lifetime",subscription_status="active")
        db.session.add(business)
        db.session.commit()
        return admin.id,customer.id,business.id

def admin_login(client):
    return client.post("/admin/login",data={"email":"admin@example.com","password":"safe-admin-password123"})

def paystack_details(reference, email="owner@example.com", amount=300000, domain="test"):
    return {"status":"success","reference":reference,"amount":amount,"currency":"NGN",
            "domain":domain,"metadata":{"product":"stockbridge_lifetime","customer_email":email}}

def test_admin_verification_checks_paystack_and_is_repeat_safe(client,monkeypatch):
    _,customer_id,business_id=seed(client)
    with client.application.app_context():
        business=db.session.get(Business,business_id)
        business.subscription_plan="starter"
        business.subscription_status="inactive"
        p=Payment(customer_email="OWNER@example.com",reference="SB-admin-verify",amount_kobo=300000)
        db.session.add(p)
        db.session.commit()
        payment_id=p.id
    client.application.config.update(PAYSTACK_PUBLIC_KEY="pk_test_dummy",PAYSTACK_SECRET_KEY="sk_test_dummy")
    admin_login(client)
    assert b"Verify with Paystack" in client.get(f"/admin/payments/{payment_id}").data
    assert client.post(f"/admin/payments/{payment_id}/verify").status_code==302
    assert client.get("/admin/reports?period=custom&start=2026-09-01&end=2026-09-30").status_code==200
    assert client.get("/admin/notifications").status_code==200
    assert client.get("/admin/activity?action=payments&q=SB-admin-verify").status_code==200
    for wrong in (paystack_details("wrong"),paystack_details("SB-admin-verify",amount=200000),
                  paystack_details("SB-admin-verify",email="someone@example.com"),
                  paystack_details("SB-admin-verify",domain="live")):
        monkeypatch.setattr("app.admin.routes.verify_transaction",lambda *a, result=wrong: result)
        assert client.post(f"/admin/payments/{payment_id}/verify",data={"confirm":"on"}).status_code==302
        with client.application.app_context():
            assert db.session.get(Payment,payment_id).status=="initialized"
            assert db.session.get(Business,business_id).subscription_status=="inactive"
    monkeypatch.setattr("app.admin.routes.verify_transaction",
                        lambda *a: paystack_details("SB-admin-verify"))
    for _ in range(2):
        assert client.post(f"/admin/payments/{payment_id}/verify",data={"confirm":"on"}).status_code==302
    with client.application.app_context():
        payment=db.session.get(Payment,payment_id)
        assert payment.status=="success" and payment.business_id==business_id and payment.paid_at
        assert db.session.get(Business,business_id).subscription_status=="active"
        assert Payment.query.count()==1
        assert AuditLog.query.filter_by(action="LIFETIME_ACCESS_GRANTED").count()==1
        assert AuditLog.query.filter_by(action="ACCESS_PAYMENT_SUCCESSFUL").count()==1
    metrics=client.get("/api/admin/dashboard").json
    assert metrics["successful_payments"]==1 and metrics["access_revenue_kobo"]==300000
    assert b"3,000.00" in client.get("/admin/reports?period=today").data

def test_admin_payment_verification_cannot_touch_admin_owned_or_mismatched_business(client,monkeypatch):
    admin_id,customer_id,customer_business=seed(client)
    with client.application.app_context():
        dual=User(full_name="Dual",email="dual@example.com",admin_enabled=True)
        dual.set_password("password123456")
        db.session.add(dual)
        db.session.flush()
        protected=Business(user_id=dual.id,name="Protected",subscription_status="inactive")
        db.session.add(protected)
        db.session.flush()
        payments=[
            Payment(customer_email="dual@example.com",business_id=protected.id,reference="SB-admin",
                    amount_kobo=300000),
            Payment(customer_email="owner@example.com",business_id=protected.id,reference="SB-mismatch",
                    amount_kobo=300000),
        ]
        db.session.add_all(payments)
        db.session.commit()
        ids=[p.id for p in payments]
        protected_id=protected.id
        dual_id=dual.id
    client.application.config.update(PAYSTACK_PUBLIC_KEY="pk_test_dummy",PAYSTACK_SECRET_KEY="sk_test_dummy")
    monkeypatch.setattr("app.admin.routes.verify_transaction",lambda *a: pytest.fail("Must not query Paystack"))
    admin_login(client)
    for payment_id in ids:
        assert client.post(f"/admin/payments/{payment_id}/verify",data={"confirm":"on"}).status_code==302
    assert client.post(f"/admin/businesses/{protected_id}/suspension",
                       data={"reason":"bad","confirm":"on"}).status_code==403
    assert client.get(f"/admin/users/{dual_id}").status_code==404
    with client.application.app_context():
        assert db.session.get(Business,protected_id).subscription_status=="inactive"
        assert all(db.session.get(Payment,pid).status=="initialized" for pid in ids)
    assert b"Protected" not in client.get("/admin/businesses").data
    assert client.get("/api/admin/dashboard").json["users"]==1

def test_admin_is_separate_and_never_needs_business_or_payment(client):
    admin_id,user_id,business_id=seed(client)
    assert client.get("/api/admin/users").status_code==401
    assert client.post("/auth/login",data={"email":"admin@example.com","password":"safe-admin-password123"}).status_code==403
    response=admin_login(client)
    assert response.status_code==302 and response.headers["Location"].endswith("/admin/")
    page=client.get("/admin/")
    assert page.status_code==200
    assert client.get("/").headers["Location"].endswith("/admin/")
    assert client.get("/auth/login").headers["Location"].endswith("/admin/")
    assert b"StockBridge Admin Portal" in page.data
    assert b"Shop" in client.get("/admin/businesses").data
    assert client.get("/api/admin/dashboard").json["businesses"]==1
    assert client.get("/dashboard").status_code==403
    assert client.get("/payments/checkout").status_code==403
    with client.application.app_context():
        admin=db.session.get(User,admin_id)
        assert admin.businesses==[]
        assert Payment.query.count()==0
        assert AuditLog.query.filter_by(action="ADMIN_LOGIN_SUCCESS").count()==1
    client.post("/admin/logout")
    assert client.get("/api/admin/users").status_code==401


def test_dashboard_customer_counts_exclude_dual_role_admin_and_owned_business(client):
    seed(client)
    with client.application.app_context():
        dual=User(full_name="Dual administrator",email="dual@example.com",admin_enabled=True,
                  email_verified_at=datetime.utcnow())
        dual.set_password("dual-admin-password123")
        db.session.add(dual)
        db.session.flush()
        db.session.add(Business(user_id=dual.id,name="Admin shop",subscription_plan="lifetime",
                                subscription_status="active"))
        db.session.commit()
    admin_login(client)
    metrics=client.get("/api/admin/dashboard").json
    assert metrics["users"]==1 and metrics["active_users"]==1
    assert metrics["businesses"]==1 and metrics["active_businesses"]==1
    assert metrics["paid_users"]==1 and metrics["new_today"]==1

def test_normal_user_cannot_self_promote_or_access_admin_api(client):
    _,user_id,_=seed(client)
    client.post("/auth/login",data={"email":"owner@example.com","password":"customer-password123"})
    for path in ("/admin/","/admin/users","/admin/payments","/api/admin/dashboard",
                 "/api/admin/users","/api/admin/payments","/api/admin/reports","/api/admin/activity"):
        assert client.get(path).status_code==403
    client.post("/profile/",data={"full_name":"Owner","business_name":"Shop","role":"admin"})
    with client.application.app_context():
        assert db.session.get(User,user_id).role=="user"
    client.post("/auth/logout")
    signup=client.post("/auth/signup",data={"full_name":"Impostor","business_name":"Other",
        "email":"new@example.com","password":"password123","role":"admin"})
    assert signup.status_code==302
    with client.application.app_context():
        assert User.query.filter_by(email="new@example.com").one().role=="user"

def test_suspension_is_audited_and_blocks_business_features(client):
    _,user_id,bid=seed(client)
    admin_login(client)
    response=client.post(f"/admin/users/{user_id}/suspension",data={"reason":"Fraud review","confirm":"on"})
    assert response.status_code==302
    with client.application.app_context():
        assert db.session.get(User,user_id).suspended_at is not None
        assert AuditLog.query.filter_by(action="USER_SUSPENDED").count()==1
    client.post("/admin/logout")
    login=client.post("/auth/login",data={"email":"owner@example.com","password":"customer-password123"})
    assert login.status_code==403
    admin_login(client)
    client.post(f"/admin/users/{user_id}/suspension",data={"reason":"Review complete","confirm":"on"})
    client.post(f"/admin/businesses/{bid}/suspension",data={"reason":"Compliance check","confirm":"on"})
    client.post("/admin/logout")
    assert client.post("/auth/login",data={"email":"owner@example.com","password":"customer-password123"}).status_code==403
    # Already signed-in users are blocked on their next protected request.
    with client.application.app_context():
        db.session.get(Business,bid).suspended_at=None
        db.session.commit()
    client.post("/auth/login",data={"email":"owner@example.com","password":"customer-password123"})
    with client.application.app_context():
        db.session.get(Business,bid).suspended_at=datetime.utcnow()
        db.session.commit()
    assert client.get("/products/").status_code==403

def test_admin_login_throttle_and_no_secret_leaks(client):
    seed(client)
    for i in range(5):
        assert client.post("/admin/login",data={"email":"admin@example.com","password":"wrong"}).status_code==401
    assert admin_login(client).status_code==429
    # Response never includes a password hash or token.
    assert b"safe-admin-password123" not in client.get("/admin/login").data


def test_admin_login_page_has_recovery_and_no_public_registration(client):
    page=client.get("/admin/login")
    assert page.status_code==200
    assert b"Administrator Sign In" in page.data
    assert b"Forgot Password?" in page.data
    assert b"Show password" in page.data
    assert b"/admin/register" not in page.data
    assert client.get("/admin/register").status_code==404
    assert page.headers["Cache-Control"]=="no-store, private"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert client.get("/admin/forgot-password").status_code==200


def test_admin_timeout_and_logout_revoke_access(client):
    seed(client)
    admin_login(client)
    with client.session_transaction() as state:
        state["admin_last_activity"]=time.time()-1900
    assert client.get("/api/admin/users").status_code==401
    assert client.get("/admin/").status_code==302
    admin_login(client)
    assert client.post("/admin/logout").status_code==302
    assert client.get("/admin/").status_code==302
    with client.application.app_context():
        assert AuditLog.query.filter_by(action="ADMIN_LOGOUT").count()==1


def test_admin_reset_is_single_use_and_invalidates_old_sessions(client,monkeypatch):
    admin_id,_,_=seed(client)
    sent=[]
    monkeypatch.setattr("app.admin.routes._send_email",lambda subject,recipient,body: sent.append((recipient,body)) or True)
    response=client.post("/admin/forgot-password",data={"email":"missing@example.com"})
    assert response.status_code==302 and not sent
    client.post("/admin/forgot-password",data={"email":"admin@example.com"})
    assert len(sent)==1 and sent[0][0]=="admin@example.com"
    token=re.search(r"/admin/reset-password/([^\s]+)",sent[0][1]).group(1)
    with client.application.app_context():
        reset=AdminPasswordReset.query.one()
        assert token not in reset.token_digest
    old_client=client.application.test_client()
    admin_login(old_client)
    assert old_client.get("/admin/").status_code==200
    assert client.post(f"/admin/reset-password/{token}",data={
        "password":"short","confirm_password":"short"}).status_code==400
    response=client.post(f"/admin/reset-password/{token}",data={
        "password":"new-secure-passphrase-2026","confirm_password":"new-secure-passphrase-2026"})
    assert response.status_code==302
    assert client.get(f"/admin/reset-password/{token}").status_code==400
    assert old_client.get("/api/admin/users").status_code==401
    with client.application.app_context():
        assert db.session.get(User,admin_id).check_password("new-secure-passphrase-2026")
        assert AuditLog.query.filter_by(action="ADMIN_PASSWORD_RESET").count()==1


def test_failed_reset_email_preserves_existing_link(client,monkeypatch):
    seed(client)
    monkeypatch.setattr("app.admin.routes._send_email",lambda *args: True)
    client.post("/admin/forgot-password",data={"email":"admin@example.com"})
    monkeypatch.setattr("app.admin.routes._send_email",lambda *args: False)
    client.post("/admin/forgot-password",data={"email":"admin@example.com"})
    with client.application.app_context():
        assert AdminPasswordReset.query.count()==1
        assert AdminPasswordReset.query.one().used_at is None


def test_business_password_reset_invalidates_dual_role_admin_session(client):
    from app.email_service import password_reset_token
    _,user_id,_=seed(client)
    with client.application.app_context():
        db.session.get(User,user_id).admin_enabled=True
        db.session.commit()
        token=password_reset_token("owner@example.com")
    client.post("/admin/login",data={"email":"owner@example.com","password":"customer-password123"})
    assert client.get("/admin/").status_code==200
    business_client=client.application.test_client()
    business_client.post(f"/auth/reset-password/{token}",data={
        "password":"new-business-passphrase","confirm_password":"new-business-passphrase"})
    assert client.get("/api/admin/users").status_code==401


def test_admin_password_rules_reject_common_passphrases():
    from app.admin.security import valid_admin_password
    assert not valid_admin_password("passwordpassword")
    assert not valid_admin_password("aaaaaaaaaaaa")
    assert valid_admin_password("a long unique passphrase")


def test_suspended_admin_and_confirmation_are_enforced_server_side(client):
    admin_id,user_id,business_id=seed(client)
    admin_login(client)
    assert client.post(f"/admin/users/{user_id}/suspension",data={"reason":"Review"}).status_code==302
    assert client.post(f"/admin/businesses/{business_id}/suspension",data={"reason":"Review"}).status_code==302
    with client.application.app_context():
        assert db.session.get(User,user_id).suspended_at is None
        assert db.session.get(Business,business_id).suspended_at is None
        db.session.get(User,admin_id).suspended_at=datetime.utcnow()
        db.session.commit()
    assert client.get("/api/admin/users").status_code==403
    client.post("/admin/logout")
    assert admin_login(client).status_code==401


def test_csrf_blocks_admin_management_without_token():
    app=create_app({"TESTING":False,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:","SECRET_KEY":"csrf-test"})
    with app.app_context():
        db.create_all()
        client=app.test_client()
        assert client.post("/admin/login",data={"email":"a@example.com","password":"wrong"}).status_code==400
        assert client.post("/admin/forgot-password",data={"email":"a@example.com"}).status_code==400
        assert client.post("/admin/users/1/suspension",data={"reason":"x","confirm":"on"}).status_code==400
        assert client.post("/admin/businesses/1/suspension",data={"reason":"x","confirm":"on"}).status_code==400
        db.session.remove()
        db.drop_all()


def test_secure_admin_login_keeps_referrer_for_csrf_validation():
    app=create_app({"TESTING":False,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:",
        "SECRET_KEY":"csrf-test"})
    with app.app_context():
        db.create_all()
        client=app.test_client()
        page=client.get("/admin/login",base_url="https://stockbridge.test")
        assert page.headers["Referrer-Policy"]=="same-origin"
        token=re.search(rb'name="csrf_token" value="([^"]+)"',page.data).group(1).decode()
        response=client.post("/admin/login",base_url="https://stockbridge.test",
            headers={"Referer":"https://stockbridge.test/admin/login"},
            data={"csrf_token":token,"email":"nobody@example.invalid","password":"wrong"})
        assert response.status_code==401
        db.session.remove()
        db.drop_all()


def test_existing_business_owner_can_use_both_logins_without_losing_data(client):
    _, user_id, business_id = seed(client)
    with client.application.app_context():
        db.session.get(User, user_id).admin_enabled = True
        db.session.commit()
    client.post("/auth/login", data={"email":"owner@example.com","password":"customer-password123"})
    assert client.get("/dashboard").status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 403
    assert client.get("/admin/login").status_code == 200
    response = client.post("/admin/login", data={"email":"owner@example.com","password":"customer-password123"})
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200
    assert client.get("/").headers["Location"].endswith("/admin/")
    assert b"Business workspace" in client.get("/admin/").data
    assert client.get("/dashboard").status_code == 403
    assert client.post(f"/admin/users/{user_id}/suspension", data={"reason":"self"}).status_code == 403
    assert client.post(f"/admin/businesses/{business_id}/suspension", data={"reason":"self"}).status_code == 403
    assert client.post("/admin/switch-to-business").headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 403
    with client.application.app_context():
        user = db.session.get(User, user_id)
        assert user.role == "user" and user.admin_enabled
        assert user.businesses[0].id == business_id

def test_customer_and_admin_hosts_keep_sessions_and_pages_separate():
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:",
        "SECRET_KEY":"test"})
    with app.app_context():
        db.create_all()
        client=app.test_client()
        _, user_id, _ = seed(client)
        db.session.get(User, user_id).admin_enabled = True
        db.session.commit()
    customer = "https://stock-bridge-one.vercel.app"
    admin = "https://stock-bridge-admin.vercel.app"
    assert client.get("/", base_url=customer).status_code == 200
    assert client.get("/admin/login", base_url=customer).headers["Location"] == admin + "/admin/login"
    assert client.get("/auth/login", base_url=admin).headers["Location"] == customer + "/auth/login"
    assert client.get("/", base_url=admin).headers["Location"].endswith("/admin/")
    login_page=client.get("/admin/login", base_url=admin)
    assert login_page.status_code == 200
    assert (customer + "/").encode() in login_page.data
    response = client.post("/admin/login", base_url=admin,
        data={"email":"owner@example.com", "password":"customer-password123"})
    assert response.status_code == 302
    assert client.get("/admin/", base_url=admin).status_code == 200
    assert b"Business sign in" in client.get("/admin/", base_url=admin).data
    # Admin cookies are scoped to the admin host; the customer host remains public.
    assert client.get("/", base_url=customer).status_code == 200
    assert client.get("/auth/login", base_url=customer).status_code == 200
    assert client.post("/admin/switch-to-business", base_url=admin).headers["Location"] == customer + "/auth/login"
    assert client.get("/admin/", base_url=admin).headers["Location"].endswith("/admin/login")
    assert client.post("/admin/login", base_url=customer).status_code == 404
    with app.app_context():
        db.session.remove()
        db.drop_all()

def test_existing_admin_cookie_on_customer_host_returns_to_customer_login(client):
    seed(client)
    admin_login(client)
    with client.session_transaction(base_url="https://stock-bridge-one.vercel.app") as state:
        state["_user_id"] = "1"
        state["admin_session"] = True
    response=client.get("/", base_url="https://stock-bridge-one.vercel.app")
    assert response.headers["Location"].endswith("/auth/login")
    assert client.get("/auth/login", base_url="https://stock-bridge-one.vercel.app").status_code == 200

def test_migration_keeps_customer_and_payment_without_promoting_email(tmp_path):
    from flask_migrate import upgrade
    from sqlalchemy import text
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'old.db'}",
        "SECRET_KEY":"test","ADMIN_EMAILS":"owner@example.com"})
    with app.app_context():
        upgrade(revision="0007_business_activity")
        db.session.execute(text("""INSERT INTO user (id,full_name,email,password_hash,created_at,email_verified_at)
            VALUES (1,'Owner','owner@example.com','x',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        db.session.execute(text("""INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at)
            VALUES (1,1,'Shop',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        db.session.execute(text("""INSERT INTO payment (id,business_id,customer_email,reference,provider,product,amount_kobo,currency,status,created_at)
            VALUES (1,1,'owner@example.com','SB-old','paystack','lifetime',300000,'NGN','success',CURRENT_TIMESTAMP)"""))
        db.session.commit()
        upgrade()
        assert User.query.one().role=="user"
        assert User.query.one().businesses[0].name=="Shop"
        assert Payment.query.one().amount_kobo==300000
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()=="0011_payment_receipt_email"
        assert User.query.one().admin_enabled is False


def test_security_migration_preserves_dual_role_and_audit_data(tmp_path):
    from flask_migrate import upgrade
    from sqlalchemy import text
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'admin.db'}","SECRET_KEY":"test"})
    with app.app_context():
        upgrade(revision="0009_dual_role_admins")
        db.session.execute(text("""INSERT INTO user (id,full_name,email,password_hash,created_at,role,admin_enabled)
            VALUES (1,'Owner','owner@example.com','x',CURRENT_TIMESTAMP,'user',1)"""))
        db.session.execute(text("""INSERT INTO audit_log (id,actor_id,action,description,created_at)
            VALUES (1,1,'ADMIN_ACCESS_GRANTED','Granted',CURRENT_TIMESTAMP)"""))
        db.session.commit()
        upgrade()
        owner=User.query.one()
        assert owner.admin_enabled and owner.admin_auth_version==0
        assert AuditLog.query.one().description=="Granted"
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()=="0011_payment_receipt_email"
