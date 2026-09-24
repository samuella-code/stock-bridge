from datetime import datetime
import pytest
from app import create_app, db
from app.models import AuditLog, Business, User, Payment

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

def test_admin_is_separate_and_never_needs_business_or_payment(client):
    admin_id,user_id,business_id=seed(client)
    assert client.get("/api/admin/users").status_code==401
    assert client.post("/auth/login",data={"email":"admin@example.com","password":"safe-admin-password123"}).status_code==403
    response=admin_login(client)
    assert response.status_code==302 and response.headers["Location"].endswith("/admin/")
    page=client.get("/admin/")
    assert page.status_code==200
    assert b"StockBridge Admin Portal" in page.data
    assert b"Shop" in client.get("/admin/businesses").data
    assert client.get("/api/admin/dashboard").json["businesses"]==1
    assert client.get("/dashboard").status_code==403
    assert client.get("/payments/checkout").status_code==403
    with client.application.app_context():
        admin=db.session.get(User,admin_id)
        assert admin.businesses==[]
        assert Payment.query.count()==0
        assert AuditLog.query.filter_by(action="ADMIN_LOGIN").count()==1
    client.post("/admin/logout")
    assert client.get("/api/admin/users").status_code==401

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
    response=client.post(f"/admin/users/{user_id}/suspension",data={"reason":"Fraud review"})
    assert response.status_code==302
    with client.application.app_context():
        assert db.session.get(User,user_id).suspended_at is not None
        assert AuditLog.query.filter_by(action="USER_SUSPENDED").count()==1
    client.post("/admin/logout")
    login=client.post("/auth/login",data={"email":"owner@example.com","password":"customer-password123"})
    assert login.status_code==403
    admin_login(client)
    client.post(f"/admin/users/{user_id}/suspension",data={"reason":"Review complete"})
    client.post(f"/admin/businesses/{bid}/suspension",data={"reason":"Compliance check"})
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
    assert client.get("/dashboard").status_code == 403
    assert client.post(f"/admin/users/{user_id}/suspension", data={"reason":"self"}).status_code == 403
    assert client.post(f"/admin/businesses/{business_id}/suspension", data={"reason":"self"}).status_code == 403
    client.post("/admin/logout")
    client.post("/auth/login", data={"email":"owner@example.com","password":"customer-password123"})
    assert client.get("/dashboard").status_code == 200
    with client.application.app_context():
        user = db.session.get(User, user_id)
        assert user.role == "user" and user.admin_enabled
        assert user.businesses[0].id == business_id

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
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()=="0009_dual_role_admins"
        assert User.query.one().admin_enabled is False
