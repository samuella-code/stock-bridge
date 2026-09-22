from datetime import datetime
import pytest

from app import create_app, db
from app.email_service import verification_token
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


def test_only_configured_owner_can_view_user_counts(client, app):
    add_user(app, "owner@example.com", verified=True)
    client.post("/auth/login", data={"email": "owner@example.com", "password": "password123"})
    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"Total users" in response.data
    assert b"Lifetime access" in response.data


def test_retailer_cannot_open_owner_dashboard(client, app):
    add_user(app, "retailer@example.com", verified=True)
    client.post("/auth/login", data={"email": "retailer@example.com", "password": "password123"})
    assert client.get("/admin/").status_code == 403
