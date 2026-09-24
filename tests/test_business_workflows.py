from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from app import create_app, db
from app.models import Business, Expense, Product, Restock, Sale, SaleItem, StockMovement, User
from app.main.routes import figures

@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SECRET_KEY": "test"})
    with app.app_context():
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()

def owner(client, email="owner@example.com"):
    with client.application.app_context():
        u=User(full_name=email,email=email,email_verified_at=datetime.utcnow())
        u.set_password("password123")
        db.session.add(u)
        db.session.flush()
        b=Business(user_id=u.id,name=email,subscription_plan="lifetime",subscription_status="active")
        db.session.add(b)
        db.session.commit()
        bid=b.id
    client.post("/auth/login",data={"email":email,"password":"password123"})
    return bid

def add(client,name,qty,cost="250",price="350",minimum="5"):
    response=client.post("/products/new",data={"name":name,"stock_quantity":qty,
        "buying_price":cost,"selling_price":price,"minimum_stock_level":minimum})
    assert response.status_code==302
    with client.application.app_context():
        return Product.query.filter_by(name=name).one().id

def test_end_to_end_stock_and_profit(client):
    bid=owner(client)
    pid=add(client,"Coca-Cola",20)
    with client.application.app_context():
        assert Product.query.get(pid).opening_quantity==20
        assert StockMovement.query.filter_by(product_id=pid,kind="opening").one().quantity_change==20
    client.post("/sales/",data={"product_id":pid,"quantity":"3","unit_price":""})
    client.post("/restocking/receive",data={"product_id":pid,"quantity":"20","unit_cost":"280"})
    client.post("/sales/",data={"product_id":pid,"quantity":"5","unit_price":""})
    client.post("/expenses/",data={"description":"Delivery","category":"Transportation","amount":"1500"})
    with client.application.app_context():
        p=db.session.get(Product,pid)
        assert p.stock_quantity==32
        assert [item.unit_cost for item in SaleItem.query.order_by(SaleItem.id).all()]==[Decimal("250"),Decimal("280")]
        assert Restock.query.one().unit_cost==Decimal("280")
        assert [m.quantity_change for m in StockMovement.query.filter_by(product_id=pid).order_by(StockMovement.id).all()]==[20,-3,20,-5]
        today=datetime.utcnow().date()
        report=figures(db.session.get(Business,bid),datetime.combine(today,datetime.min.time()),datetime.combine(today+timedelta(days=1),datetime.min.time()))
        assert report["revenue"]==Decimal("2800")
        assert report["cogs"]==Decimal("2150")
        assert report["gross_profit"]==Decimal("650")
        assert report["expenses"]==Decimal("1500")
        assert report["net_profit"]==Decimal("-850")
        assert report["inventory_purchased"]==Decimal("5600")
    assert b"32" in client.get(f"/products/{pid}").data
    assert b"650" in client.get("/dashboard").data
    assert b"650" in client.get("/reports").data

def test_multi_product_sale_atomic_and_history(client):
    bid=owner(client)
    ids=[add(client,n,10,cost,price) for n,cost,price in
         (("Coca-Cola","250","350"),("Bread","900","1100"),("Indomie","300","400"))]
    response=client.post("/sales/",data={"product_id":ids,"quantity":["2","1","3"],
        "unit_price":["","",""],"payment_method":"POS"})
    assert response.status_code==302
    with client.application.app_context():
        assert Sale.query.count()==1
        sale=Sale.query.one()
        assert sale.payment_method=="POS"
        assert SaleItem.query.count()==3
        assert sale.total==Decimal("3000")
        assert [db.session.get(Product,pid).stock_quantity for pid in ids]==[8,9,7]
    # Reject whole checkout when one item exceeds stock.
    client.post("/sales/",data={"product_id":ids,"quantity":["1","100","1"],"unit_price":["","",""]})
    with client.application.app_context():
        assert Sale.query.count()==1
        assert [db.session.get(Product,pid).stock_quantity for pid in ids]==[8,9,7]
    client.post(f"/sales/{sale.id}/delete",data={"reason":"Customer returned all goods"})
    with client.application.app_context():
        assert Sale.query.count()==1
        assert Sale.query.one().voided_at
        assert [db.session.get(Product,pid).stock_quantity for pid in ids]==[10,10,10]
        assert figures(db.session.get(Business,bid),datetime.utcnow()-timedelta(days=1),datetime.utcnow()+timedelta(days=1))["revenue"]==0

def test_zero_stock_adjustments_and_isolation(client):
    bid=owner(client)
    pid=add(client,"Empty",0)
    assert b"Out of stock" in client.get("/products/").data
    client.post(f"/products/{pid}/adjust",data={"direction":"increase","quantity":"4","reason":"Returned"})
    with client.application.app_context():
        assert db.session.get(Product,pid).stock_quantity==4
        assert StockMovement.query.filter_by(product_id=pid,kind="adjustment").one().reason=="Returned"
        other=Product(business_id=bid+1,name="Private")
        other_user=User(full_name="Other",email="other@example.com")
        other_user.set_password("password123")
        db.session.add(other_user);db.session.flush()
        db.session.add(Business(id=bid+1,user_id=other_user.id,name="Other",subscription_status="active"))
        db.session.add(other);db.session.commit()
        other_id=other.id
    assert client.get(f"/products/{other_id}").status_code==404
    assert client.post(f"/products/{other_id}/adjust",data={"quantity":"1","direction":"increase","reason":"Other"}).status_code==404
    assert client.post("/restocking/receive",data={"product_id":other_id,"quantity":"1","unit_cost":"1"}).status_code==404
    assert client.post("/sales/",data={"product_id":other_id,"quantity":"1","unit_price":""}).status_code==404
    assert b"Private" not in client.get("/products/").data
    assert b"Private" not in client.get("/reports").data

def test_expense_and_low_stock_filters(client):
    owner(client)
    p=add(client,"Low",2,minimum="5")
    add(client,"Out",0,minimum="5")
    assert b"Low stock" in client.get("/products/?stock=low").data
    response=client.get("/products/?stock=out")
    assert b"Out of stock" in response.data and b"Low</strong>" not in response.data
    client.post("/expenses/",data={"description":"Electricity","category":"Electricity","amount":"120"})
    assert b"120" in client.get("/reports").data
    client.post(f"/products/{p}/edit",data={"name":"Low","stock_quantity":"999","buying_price":"250","selling_price":"350"})
    with client.application.app_context():
        assert db.session.get(Product,p).stock_quantity==2

def test_multi_product_restock_and_validation(client):
    owner(client)
    first=add(client,"Fanta",1,cost="100")
    second=add(client,"Bread",2,cost="200")
    client.post("/restocking/receive",data={"product_id":[first,second],
        "quantity":["4","3"],"unit_cost":["120","230"],"supplier":"Wholesaler"})
    with client.application.app_context():
        assert Restock.query.count()==2
        assert len({row.batch_id for row in Restock.query.all()})==1
        assert [db.session.get(Product,pid).stock_quantity for pid in (first,second)]==[5,5]
        assert [row.unit_cost for row in Restock.query.order_by(Restock.product_id).all()]==[Decimal("120"),Decimal("230")]
    client.post("/restocking/receive",data={"product_id":[first,second],
        "quantity":["1","-5"],"unit_cost":["120","230"]})
    with client.application.app_context():
        assert Restock.query.count()==2
        assert [db.session.get(Product,pid).stock_quantity for pid in (first,second)]==[5,5]

def test_legacy_migration_preserves_sales_and_stock(tmp_path):
    from flask_migrate import upgrade
    from sqlalchemy import text
    app=create_app({"TESTING":True,"SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'legacy.db'}","SECRET_KEY":"test"})
    with app.app_context():
        upgrade(revision="0006_restock")
        db.session.execute(text("""INSERT INTO user (id,full_name,email,password_hash,created_at,email_verified_at)
            VALUES (1,'Owner','owner@example.com','x',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        db.session.execute(text("""INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at)
            VALUES (1,1,'Shop',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        db.session.execute(text("""INSERT INTO product (id,business_id,name,buying_price,selling_price,stock_quantity,minimum_stock_level,supplier_lead_time,safety_stock,created_at,updated_at)
            VALUES (1,1,'Coke',250,350,17,5,2,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        db.session.execute(text("""INSERT INTO sale (id,business_id,product_id,quantity,unit_price,unit_cost,sold_at)
            VALUES (1,1,1,3,350,250,CURRENT_TIMESTAMP)"""))
        db.session.commit()
        upgrade()
        assert Sale.query.count()==1
        assert SaleItem.query.one().unit_cost==250
        assert Sale.query.one().total==1050
        assert Product.query.one().opening_quantity==20
        assert sum(m.quantity_change for m in StockMovement.query.all())==17
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()=="0010_admin_security"

def test_void_expense_keeps_audit_and_updates_report(client):
    bid=owner(client)
    client.post("/expenses/",data={"description":"Delivery","category":"Transportation","amount":"1500"})
    with client.application.app_context():
        eid=Expense.query.one().id
    client.post(f"/expenses/{eid}/delete",data={"reason":"Duplicate entry"})
    with client.application.app_context():
        row=Expense.query.one()
        assert row.void_reason=="Duplicate entry"
        assert row.voided_at
        report=figures(db.session.get(Business,bid),datetime.utcnow()-timedelta(days=1),
                       datetime.utcnow()+timedelta(days=1))
        assert report["expenses"]==0
