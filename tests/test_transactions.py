import pytest
from datetime import datetime
from decimal import Decimal
from app import create_app, db
from app.models import Business, Expense, Product, Sale, User

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

def seed(client):
    with client.application.app_context():
        user = User(full_name="Owner", email="money@example.com", email_verified_at=datetime.utcnow())
        user.set_password("password123")
        db.session.add(user)
        db.session.flush()
        business = Business(user_id=user.id, name="Shop", subscription_plan="lifetime", subscription_status="active")
        db.session.add(business)
        db.session.flush()
        product = Product(business_id=business.id, name="Water", buying_price=100, selling_price=150, stock_quantity=10, minimum_stock_level=3)
        db.session.add(product)
        db.session.commit()
        product_id = product.id
    response = client.post("/auth/login", data={"email":"money@example.com", "password":"password123"})
    assert response.status_code == 302
    return product_id

def test_sale_reduces_and_delete_restores_stock(client):
    product_id = seed(client)
    response = client.post("/sales/", data={"product_id":product_id, "quantity":2, "unit_price":150}, follow_redirects=True)
    assert b"Sale recorded" in response.data
    with client.application.app_context():
        sale_id = Sale.query.one().id
        assert db.session.get(Product, product_id).stock_quantity == 8
    client.post(f"/sales/{sale_id}/delete")
    with client.application.app_context():
        assert db.session.get(Product, product_id).stock_quantity == 10

def test_expense_and_dashboard(client):
    seed(client)
    client.post("/expenses/", data={"description":"Transport", "amount":"2500"})
    with client.application.app_context():
        assert Expense.query.one().amount == Decimal("2500.00")
    assert b"2,500" in client.get("/dashboard").data

def test_restock_creates_record_and_updates_stock(client):
    from app.models import Restock
    product_id = seed(client)
    response = client.post("/restocking/receive", data={"product_id": product_id, "quantity": "4", "unit_cost": "120.50", "supplier": "Distributor"}, follow_redirects=True)
    assert b"Received 4 units" in response.data
    with client.application.app_context():
        assert db.session.get(Product, product_id).stock_quantity == 14
        assert Restock.query.one().total == Decimal("482.00")
    client.post("/sales/", data={"product_id": product_id, "quantity": "3"})
    with client.application.app_context():
        assert db.session.get(Product, product_id).stock_quantity == 11
        assert Sale.query.one().unit_cost == Decimal("120.50")

def test_invalid_restock_does_not_change_inventory(client):
    product_id = seed(client)
    client.post("/restocking/receive", data={"product_id": product_id, "quantity": "-1", "unit_cost": "120"})
    with client.application.app_context():
        assert db.session.get(Product, product_id).stock_quantity == 10
