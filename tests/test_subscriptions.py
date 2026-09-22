from datetime import datetime
import pytest
from app import create_app, db
from app.models import Business, User


@pytest.fixture
def app():
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:","SECRET_KEY":"test"})
    with app.app_context(): db.create_all(); yield app; db.session.remove(); db.drop_all()


@pytest.fixture
def client(app): return app.test_client()


def test_signup_is_available_without_payment(client):
    response=client.get("/auth/signup")
    assert response.status_code==200
    assert b"Create free account" in response.data


def test_verified_unpaid_user_can_dashboard_but_stock_tools_show_payment(client,app):
    with app.app_context():
        user=User(full_name="Owner",email="owner@example.com",email_verified_at=datetime.utcnow()); user.set_password("password123"); db.session.add(user); db.session.flush(); db.session.add(Business(user_id=user.id,name="Shop",subscription_status="inactive")); db.session.commit()
    client.post("/auth/login",data={"email":"owner@example.com","password":"password123"})
    assert client.get("/dashboard").status_code==200
    response=client.get("/products/")
    assert response.status_code==302
    assert response.headers["Location"].endswith("/plans/")
    page=client.get("/plans/")
    assert b"3,000" in page.data and b"Pay and unlock tools" in page.data
