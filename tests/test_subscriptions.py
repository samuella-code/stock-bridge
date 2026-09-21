import pytest

from app import create_app, db
from app.models import Business, User


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_public_offer_precedes_signup(client):
    response = client.get("/")
    assert response.status_code == 302
    response = client.get(response.headers["Location"])
    assert b"3,000" in response.data
    assert b"No free trial" in response.data
    assert b"Pay and get access" in response.data


def test_signup_requires_verified_payment(client):
    response = client.get("/auth/signup")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/plans/")


def test_inactive_legacy_account_cannot_open_dashboard(client, app):
    with app.app_context():
        user = User(full_name="Old User", email="old@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Business(user_id=user.id, name="Old Shop", subscription_status="inactive"))
        db.session.commit()
    client.post("/auth/login", data={"email": "old@example.com", "password": "password123"})
    response = client.get("/dashboard")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/plans/")
