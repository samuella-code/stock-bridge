from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import check_password_hash,generate_password_hash
from app import db,login_manager
class User(UserMixin,db.Model):
 id=db.Column(db.Integer,primary_key=True); full_name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(180),unique=True,nullable=False,index=True); password_hash=db.Column(db.String(255),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); email_verified_at=db.Column(db.DateTime); verification_sent_at=db.Column(db.DateTime); role=db.Column(db.String(20),nullable=False,default="user"); suspended_at=db.Column(db.DateTime); last_activity_at=db.Column(db.DateTime)
 businesses=db.relationship("Business",backref="owner",lazy=True,cascade="all, delete-orphan")
 def set_password(self,p): self.password_hash=generate_password_hash(p)
 def check_password(self,p): return check_password_hash(self.password_hash,p)
class Business(db.Model):
 id=db.Column(db.Integer,primary_key=True); user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False,index=True); name=db.Column(db.String(140),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
 subscription_plan=db.Column(db.String(20),nullable=False,default="starter")
 subscription_status=db.Column(db.String(20),nullable=False,default="inactive")
 trial_started_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)
 trial_ends_at=db.Column(db.DateTime,nullable=False,default=lambda: datetime.utcnow()+timedelta(days=14))
 subscription_ends_at=db.Column(db.DateTime); suspended_at=db.Column(db.DateTime)
 products=db.relationship("Product",backref="business",cascade="all, delete-orphan"); sales=db.relationship("Sale",backref="business",cascade="all, delete-orphan"); expenses=db.relationship("Expense",backref="business",cascade="all, delete-orphan"); payments=db.relationship("Payment",backref="business",cascade="all, delete-orphan")
 @property
 def trial_days_remaining(self):
  if self.subscription_status != "trialing" or not self.trial_ends_at: return 0
  remaining=(self.trial_ends_at-datetime.utcnow()).total_seconds()
  return max(0,int((remaining+86399)//86400))
 @property
 def has_write_access(self):
  if self.subscription_status == "active":
   return not self.subscription_ends_at or self.subscription_ends_at > datetime.utcnow()
  return False
class Product(db.Model):
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); name=db.Column(db.String(140),nullable=False); sku=db.Column(db.String(60)); category=db.Column(db.String(80)); buying_price=db.Column(db.Numeric(12,2),nullable=False,default=0); selling_price=db.Column(db.Numeric(12,2),nullable=False,default=0); stock_quantity=db.Column(db.Integer,nullable=False,default=0); minimum_stock_level=db.Column(db.Integer,nullable=False,default=0); supplier_name=db.Column(db.String(140)); unit=db.Column(db.String(30),nullable=False,default="unit"); description=db.Column(db.String(500)); active=db.Column(db.Boolean,nullable=False,default=True); opening_quantity=db.Column(db.Integer,nullable=False,default=0); supplier_lead_time=db.Column(db.Integer,nullable=False,default=2); safety_stock=db.Column(db.Integer,nullable=False,default=0); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); updated_at=db.Column(db.DateTime,default=datetime.utcnow,onupdate=datetime.utcnow,nullable=False)
 __table_args__=(db.UniqueConstraint("business_id","sku",name="uq_product_business_sku"),)
 @property
 def is_low_stock(self): return self.stock_quantity<=self.minimum_stock_level
 @property
 def stock_status(self):
  return "Out of stock" if self.stock_quantity==0 else "Low stock" if self.is_low_stock else "In stock"
class Sale(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True)
 # Legacy line columns remain nullable so existing records and migrations remain valid.
 product_id=db.Column(db.Integer,db.ForeignKey("product.id"))
 quantity=db.Column(db.Integer)
 unit_price=db.Column(db.Numeric(12,2))
 unit_cost=db.Column(db.Numeric(12,2))
 sold_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
 payment_method=db.Column(db.String(20),nullable=False,default="Other")
 note=db.Column(db.String(500))
 voided_at=db.Column(db.DateTime)
 void_reason=db.Column(db.String(300))
 items=db.relationship("SaleItem",backref="sale",cascade="all, delete-orphan",lazy="select")
 @property
 def total(self): return sum((item.subtotal for item in self.items),0)
 @property
 def cost(self): return sum((item.cost for item in self.items),0)
 @property
 def profit(self): return self.total-self.cost
 @property
 def units(self): return sum(item.quantity for item in self.items)
 @property
 def product(self):
  return self.items[0].product if self.items else None

class SaleItem(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 sale_id=db.Column(db.Integer,db.ForeignKey("sale.id"),nullable=False,index=True)
 product_id=db.Column(db.Integer,db.ForeignKey("product.id"),nullable=False,index=True)
 quantity=db.Column(db.Integer,nullable=False)
 unit_price=db.Column(db.Numeric(12,2),nullable=False)
 unit_cost=db.Column(db.Numeric(12,2),nullable=False)
 product=db.relationship("Product")
 @property
 def subtotal(self): return self.quantity*self.unit_price
 @property
 def cost(self): return self.quantity*self.unit_cost
 @property
 def profit(self): return self.subtotal-self.cost

class StockMovement(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True)
 product_id=db.Column(db.Integer,db.ForeignKey("product.id"),nullable=False,index=True)
 kind=db.Column(db.String(30),nullable=False)
 quantity_change=db.Column(db.Integer,nullable=False)
 reason=db.Column(db.String(100))
 note=db.Column(db.String(500))
 sale_id=db.Column(db.Integer,db.ForeignKey("sale.id"))
 restock_id=db.Column(db.Integer,db.ForeignKey("restock.id"))
 occurred_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
 product=db.relationship("Product")
class Restock(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True)
 product_id=db.Column(db.Integer,db.ForeignKey("product.id"),nullable=False)
 quantity=db.Column(db.Integer,nullable=False)
 unit_cost=db.Column(db.Numeric(12,2),nullable=False)
 supplier=db.Column(db.String(140))
 note=db.Column(db.String(500))
 batch_id=db.Column(db.String(36),index=True)
 received_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
 product=db.relationship("Product")
 @property
 def total(self): return self.quantity*self.unit_cost

class Expense(db.Model):
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); description=db.Column(db.String(180),nullable=False); amount=db.Column(db.Numeric(12,2),nullable=False); spent_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True); category=db.Column(db.String(80),nullable=False,default="Miscellaneous"); note=db.Column(db.String(500)); voided_at=db.Column(db.DateTime); void_reason=db.Column(db.String(300))
class AuditLog(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 actor_id=db.Column(db.Integer,db.ForeignKey("user.id"),index=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),index=True)
 action=db.Column(db.String(50),nullable=False,index=True)
 description=db.Column(db.String(300),nullable=False)
 created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
 actor=db.relationship("User")
 business=db.relationship("Business")

class AdminLoginAttempt(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 identifier=db.Column(db.String(64),nullable=False,index=True)
 attempted_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)

class Payment(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=True,index=True)
 customer_email=db.Column(db.String(180),nullable=False,index=True)
 claim_token=db.Column(db.String(120),unique=True,index=True)
 reference=db.Column(db.String(100),unique=True,nullable=False,index=True)
 provider=db.Column(db.String(30),nullable=False,default="paystack")
 product=db.Column(db.String(40),nullable=False,default="lifetime")
 amount_kobo=db.Column(db.Integer,nullable=False)
 currency=db.Column(db.String(3),nullable=False,default="NGN")
 status=db.Column(db.String(20),nullable=False,default="initialized",index=True)
 paid_at=db.Column(db.DateTime)
 created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
@login_manager.user_loader
def load_user(i): return db.session.get(User,int(i))
