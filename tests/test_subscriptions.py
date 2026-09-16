from datetime import datetime, timedelta
import pytest

from app import create_app, db
from app.models import Business


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


def create_account(client):
    return client.post("/auth/signup", data={"full_name":"Ada Owner","business_name":"Ada Mini Mart","email":"ada@example.com","password":"password123"}, follow_redirects=True)


def test_signup_starts_fourteen_day_trial(client, app):
    response = create_account(client)
    assert b"14-day free trial" in response.data
    with app.app_context():
        business = Business.query.one()
        assert business.subscription_status == "trialing"
        assert business.subscription_plan == "starter"
        assert 13 <= (business.trial_ends_at-business.trial_started_at).days <= 14


def test_plans_page_shows_prices(client, app):
    create_account(client)
    response = client.get("/plans/")
    assert response.status_code == 200
    assert b"3,000" in response.data
    assert b"7,500" in response.data
    assert b"15,000" in response.data


def test_expired_trial_blocks_new_business_records(client, app):
    create_account(client)
    with app.app_context():
        business = Business.query.one()
        business.trial_ends_at = datetime.utcnow()-timedelta(days=1)
        db.session.commit()
    response = client.post("/expenses/", data={"description":"Transport","amount":"2500"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/plans/")
