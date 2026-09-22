import pytest
from datetime import datetime
from app import create_app, db
from app.models import Business, Product, User

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

def login(client, email="owner@example.com"):
    with client.application.app_context():
        user = User(full_name="Test Owner", email=email, email_verified_at=datetime.utcnow())
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        db.session.add(Business(user_id=user.id, name="Test Shop", subscription_plan="lifetime", subscription_status="active"))
        db.session.commit()
    response = client.post("/auth/login", data={"email": email, "password": "password123"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")

def test_create_and_list_low_stock_product(client, app):
    login(client)
    response = client.post("/products/new", data={"name":"Eva Water","sku":"water-001","buying_price":"150","selling_price":"250","stock_quantity":"4","minimum_stock_level":"5","supplier_lead_time":"2","safety_stock":"3"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Eva Water" in response.data
    assert b"Low stock" in response.data
    with app.app_context():
        assert Product.query.one().sku == "WATER-001"

def test_business_ownership_blocks_cross_account_edit(client, app):
    login(client)
    with app.app_context():
        other = User(full_name="Other", email="other@example.com", email_verified_at=datetime.utcnow())
        other.set_password("password123")
        db.session.add(other)
        db.session.flush()
        business = Business(user_id=other.id, name="Other Shop", subscription_plan="lifetime", subscription_status="active")
        db.session.add(business)
        db.session.flush()
        product = Product(business_id=business.id, name="Private Product")
        db.session.add(product)
        db.session.commit()
        product_id = product.id
    assert client.get(f"/products/{product_id}/edit").status_code == 404
