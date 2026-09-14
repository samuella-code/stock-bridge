from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import check_password_hash,generate_password_hash
from app import db,login_manager
class User(UserMixin,db.Model):
 id=db.Column(db.Integer,primary_key=True); full_name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(180),unique=True,nullable=False,index=True); password_hash=db.Column(db.String(255),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
 businesses=db.relationship("Business",backref="owner",lazy=True,cascade="all, delete-orphan")
 def set_password(self,p): self.password_hash=generate_password_hash(p)
 def check_password(self,p): return check_password_hash(self.password_hash,p)
class Business(db.Model):
 id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False,index=True); name=db.Column(db.String(140),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
 products=db.relationship("Product",backref="business",cascade="all, delete-orphan"); sales=db.relationship("Sale",backref="business",cascade="all, delete-orphan"); expenses=db.relationship("Expense",backref="business",cascade="all, delete-orphan")
class Product(db.Model):
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); name=db.Column(db.String(140),nullable=False); sku=db.Column(db.String(60)); category=db.Column(db.String(80)); buying_price=db.Column(db.Numeric(12,2),nullable=False,default=0); selling_price=db.Column(db.Numeric(12,2),nullable=False,default=0); stock_quantity=db.Column(db.Integer,nullable=False,default=0); minimum_stock_level=db.Column(db.Integer,nullable=False,default=0); supplier_name=db.Column(db.String(140)); supplier_lead_time=db.Column(db.Integer,nullable=False,default=2); safety_stock=db.Column(db.Integer,nullable=False,default=0); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); updated_at=db.Column(db.DateTime,default=datetime.utcnow,onupdate=datetime.utcnow,nullable=False)
 sales=db.relationship("Sale",backref="product",cascade="all, delete-orphan"); __table_args__=(db.UniqueConstraint("business_id","sku",name="uq_product_business_sku"),)
 @property
 def is_low_stock(self): return self.stock_quantity<=self.minimum_stock_level
class Sale(db.Model):
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); product_id=db.Column(db.Integer,db.ForeignKey("product.id"),nullable=False); quantity=db.Column(db.Integer,nullable=False); unit_price=db.Column(db.Numeric(12,2),nullable=False); unit_cost=db.Column(db.Numeric(12,2),nullable=False); sold_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
 @property
 def total(self): return self.unit_price*self.quantity
 @property
 def profit(self): return (self.unit_price-self.unit_cost)*self.quantity
class Expense(db.Model):
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); description=db.Column(db.String(180),nullable=False); amount=db.Column(db.Numeric(12,2),nullable=False); spent_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
@login_manager.user_loader
def load_user(i): return db.session.get(User,int(i))
