import pytest
from app import create_app, db
@pytest.fixture
def app():
 app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:","SECRET_KEY":"test"})
 with app.app_context():
  db.create_all();yield app;db.session.remove();db.drop_all()
@pytest.fixture
def client(app): return app.test_client()
from decimal import Decimal
from app.models import Business,Expense,Product,Sale,User
def seed(client):
 with client.application.app_context():
  u=User(full_name="Owner",email="money@example.com");u.set_password("password123");db.session.add(u);db.session.flush();b=Business(user_id=u.id,name="Shop");db.session.add(b);db.session.flush();p=Product(business_id=b.id,name="Water",buying_price=100,selling_price=150,stock_quantity=10,minimum_stock_level=3);db.session.add(p);db.session.commit();pid=p.id
 client.post("/login",data={"email":"money@example.com","password":"password123"});return pid
def test_sale_reduces_and_delete_restores_stock(client):
 pid=seed(client);r=client.post("/sales/",data={"product_id":pid,"quantity":2,"unit_price":150},follow_redirects=True);assert b"Sale recorded" in r.data
 with client.application.app_context(): sale=Sale.query.one();sid=sale.id;assert db.session.get(Product,pid).stock_quantity==8
 client.post(f"/sales/{sid}/delete")
 with client.application.app_context(): assert db.session.get(Product,pid).stock_quantity==10
def test_expense_and_dashboard(client):
 seed(client);client.post("/expenses/",data={"description":"Transport","amount":"2500"})
 with client.application.app_context(): assert Expense.query.one().amount==Decimal("2500.00")
 assert b"2,500" in client.get("/dashboard").data
