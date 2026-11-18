from datetime import datetime
import pytest
import re
from html import unescape
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
    assert b"Create account" in response.data


def test_public_home_and_customer_login_link_to_signup(client):
    home = client.get("/")
    assert home.status_code == 200
    heading = re.search(r'<h1\b[^>]*id="home-title"[^>]*>(.*?)</h1>', home.get_data(as_text=True), re.S)
    assert heading is not None
    heading_text = ' '.join(unescape(re.sub(r'<[^>]+>', ' ', heading[1])).split())
    assert heading_text == 'Know what you have. Know what you sold. Know what you made.'
    assert b'href="/auth/signup"' in home.data
    assert b'href="/auth/login"' in home.data
    login = client.get("/auth/login")
    assert b"Forgot your password?" in login.data
    assert login.status_code == 200
    login_html = login.get_data(as_text=True)
    assert 'New to StockBridge?' in login_html
    assert re.search(r'<a\b[^>]*href="/auth/signup"[^>]*>\s*Create an account\s*</a>', login_html)


def test_unverified_signup_cannot_enter_dashboard_or_pay(client, app, monkeypatch):
    from app.email_service import verification_token
    monkeypatch.setattr("app.auth.routes.send_verification_email", lambda user: True)
    response = client.post("/auth/signup", data={"full_name":"Ada", "business_name":"Ada Mart",
        "email":"ada@example.com", "password":"password123"}, follow_redirects=True)
    assert b"Check your email to verify your StockBridge account" in response.data
    assert client.get("/dashboard").headers["Location"].endswith("/auth/verify-pending")
    assert client.get("/payments/checkout").headers["Location"].endswith("/auth/verify-pending")
    assert client.post("/payments/initialize").headers["Location"].endswith("/auth/verify-pending")
    with app.app_context():
        token = verification_token("ada@example.com")
    preview = client.get(f"/auth/verify/{token}", follow_redirects=True)
    assert b"Explore StockBridge" in preview.data
    assert b"View Basic, Plus and Pro plans" in preview.data
    assert b"Lifetime" not in preview.data
    assert client.get("/products/").headers["Location"].endswith("/plans/")


def test_verified_unpaid_user_can_dashboard_but_stock_tools_show_payment(client,app):
    with app.app_context():
        user=User(full_name="Owner",email="owner@example.com",email_verified_at=datetime.utcnow()); user.set_password("password123"); db.session.add(user); db.session.flush(); db.session.add(Business(user_id=user.id,name="Shop",subscription_status="inactive")); db.session.commit()
    client.post("/auth/login",data={"email":"owner@example.com","password":"password123"})
    assert client.get("/dashboard").status_code==200
    response=client.get("/products/")
    assert response.status_code==302
    assert response.headers["Location"].endswith("/plans/")
    page=client.get("/plans/")
    assert b"3,000/month" in page.data and b"5,000/month" in page.data
    assert b"Lifetime" not in page.data
    assert b"Subscription checkout is not currently available" in page.data
    assert b"create your account after confirmation" not in page.data
