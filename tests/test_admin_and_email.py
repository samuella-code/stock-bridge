from datetime import datetime
import pytest

from app import create_app, db
from app.email_service import password_reset_token, verification_token
from app.models import Business, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test", "ADMIN_EMAILS": "owner@example.com"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def add_user(app, email, verified=False):
    with app.app_context():
        user = User(full_name="Owner", email=email, email_verified_at=datetime.utcnow() if verified else None)
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Business(user_id=user.id, name="Shop", subscription_plan="lifetime", subscription_status="active"))
        db.session.commit()


def test_verification_link_unlocks_dashboard(client, app):
    add_user(app, "new@example.com")
    with app.app_context():
        token = verification_token("new@example.com")
    response = client.get(f"/auth/verify/{token}", follow_redirects=True)
    assert b"Email verified" in response.data
    with app.app_context():
        assert User.query.one().email_verified_at is not None


def test_password_reset_updates_login_password(client, app):
    add_user(app, "reset@example.com", verified=True)
    with app.app_context():
        token = password_reset_token("reset@example.com")

    response = client.post(
        f"/auth/reset-password/{token}",
        data={"password": "newpassword123", "confirm_password": "newpassword123"},
        follow_redirects=True,
    )
    assert b"password has been updated" in response.data
    response = client.post(
        "/auth/login",
        data={"email": "reset@example.com", "password": "newpassword123"},
        follow_redirects=True,
    )
    assert b"Welcome back" in response.data


def test_forgot_password_does_not_reveal_unknown_email(client):
    response = client.post(
        "/auth/forgot-password",
        data={"email": "missing@example.com"},
        follow_redirects=True,
    )
    assert b"If that email belongs to a StockBridge account" in response.data


def test_email_allowlist_does_not_grant_admin_access(client, app):
    add_user(app, "owner@example.com", verified=True)
    client.post("/auth/login", data={"email": "owner@example.com", "password": "password123"})
    assert client.get("/admin/").status_code == 403
    assert client.get("/api/admin/users").status_code == 403


def test_retailer_cannot_open_owner_dashboard(client, app):
    add_user(app, "retailer@example.com", verified=True)
    client.post("/auth/login", data={"email": "retailer@example.com", "password": "password123"})
    assert client.get("/admin/").status_code == 403
