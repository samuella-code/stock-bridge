from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import check_password_hash,generate_password_hash
from app import db,login_manager
class User(UserMixin,db.Model):
 id=db.Column(db.Integer,primary_key=True); full_name=db.Column(db.String(120),nullable=False); email=db.Column(db.String(180),unique=True,nullable=False,index=True); password_hash=db.Column(db.String(255),nullable=False); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); email_verified_at=db.Column(db.DateTime); verification_sent_at=db.Column(db.DateTime); role=db.Column(db.String(20),nullable=False,default="user"); admin_enabled=db.Column(db.Boolean,nullable=False,default=False,server_default="false"); admin_auth_version=db.Column(db.Integer,nullable=False,default=0,server_default="0"); suspended_at=db.Column(db.DateTime); last_activity_at=db.Column(db.DateTime)
 businesses=db.relationship("Business",backref="owner",lazy=True,cascade="all, delete-orphan")
 def set_password(self,p): self.password_hash=generate_password_hash(p)
 def check_password(self,p): return check_password_hash(self.password_hash,p)
class Business(db.Model):
 logo_key=db.Column(db.String(200))
 notifications=db.relationship("Notification",cascade="all, delete-orphan",passive_deletes=True)
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
  from flask import current_app
  if current_app.config.get("SUBSCRIPTIONS_ENABLED"):
   from app.subscriptions.entitlements import effective_access
   return effective_access(self.owner, business=self).can_write
  if self.subscription_status == "active":
   return not self.subscription_ends_at or self.subscription_ends_at > datetime.utcnow()
  return False
class Product(db.Model):
 # Retained legacy storage: no business workflow reads or writes this column.
 barcode=db.Column(db.String(80))
 id=db.Column(db.Integer,primary_key=True); business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False,index=True); name=db.Column(db.String(140),nullable=False); sku=db.Column(db.String(60)); category=db.Column(db.String(80)); buying_price=db.Column(db.Numeric(12,2),nullable=False,default=0); selling_price=db.Column(db.Numeric(12,2),nullable=False,default=0); stock_quantity=db.Column(db.Integer,nullable=False,default=0); minimum_stock_level=db.Column(db.Integer,nullable=False,default=0); supplier_name=db.Column(db.String(140)); unit=db.Column(db.String(30),nullable=False,default="unit"); description=db.Column(db.String(500)); active=db.Column(db.Boolean,nullable=False,default=True); opening_quantity=db.Column(db.Integer,nullable=False,default=0); supplier_lead_time=db.Column(db.Integer,nullable=False,default=2); safety_stock=db.Column(db.Integer,nullable=False,default=0); created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False); updated_at=db.Column(db.DateTime,default=datetime.utcnow,onupdate=datetime.utcnow,nullable=False)
 __table_args__=(db.UniqueConstraint("business_id","sku",name="uq_product_business_sku"), db.UniqueConstraint("business_id","barcode",name="uq_product_business_barcode"), db.Index("ix_product_business_active_name", "business_id", "active", "name"), db.Index("ix_product_business_active_category", "business_id", "active", "category"))
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
 target_type=db.Column(db.String(40))
 target_id=db.Column(db.Integer)
 ip_address=db.Column(db.String(45))
 created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)
 actor=db.relationship("User")
 business=db.relationship("Business")

class AdminLoginAttempt(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 identifier=db.Column(db.String(64),nullable=False,index=True)
 attempted_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False,index=True)

class AdminPasswordReset(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False,index=True)
 token_digest=db.Column(db.String(64),nullable=False,unique=True)
 expires_at=db.Column(db.DateTime,nullable=False)
 used_at=db.Column(db.DateTime)
 created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
 user=db.relationship("User")

class Payment(db.Model):
 user_id=db.Column(db.Integer,db.ForeignKey("user.id"),index=True)
 subscription_id=db.Column(db.Integer,db.ForeignKey("recurring_subscription.id"),index=True)
 plan_code=db.Column(db.String(20))
 billing_interval=db.Column(db.String(10))
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
 receipt_email_claimed_at=db.Column(db.DateTime)
 receipt_email_sent_at=db.Column(db.DateTime)
 created_at=db.Column(db.DateTime,default=datetime.utcnow,nullable=False)
class AccountBilling(db.Model):
 user_id=db.Column(db.Integer,db.ForeignKey("user.id"),primary_key=True)
 trial_eligible=db.Column(db.Boolean,nullable=False,default=False,server_default="false")
 trial_started_at=db.Column(db.DateTime)
 trial_ends_at=db.Column(db.DateTime)
 legacy_payment_id=db.Column(db.Integer,db.ForeignKey("payment.id"),unique=True)
 legacy_business_id=db.Column(db.Integer,db.ForeignKey("business.id"))
 legacy_granted_at=db.Column(db.DateTime)
 primary_business_id=db.Column(db.Integer,db.ForeignKey("business.id"))
 created_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)

class RecurringSubscription(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False,index=True)
 business_id=db.Column(db.Integer,db.ForeignKey("business.id"),nullable=False)
 plan_code=db.Column(db.String(20),nullable=False)
 billing_interval=db.Column(db.String(10),nullable=False)
 amount_kobo=db.Column(db.Integer,nullable=False)
 provider_plan_code=db.Column(db.String(80),nullable=False)
 provider_customer_code=db.Column(db.String(80))
 provider_subscription_code=db.Column(db.String(80),unique=True)
 status=db.Column(db.String(30),nullable=False,default="initializing",index=True)
 checkout_reference=db.Column(db.String(100),unique=True)
 checkout_url=db.Column(db.String(500))
 subscription_started_at=db.Column(db.DateTime)
 current_period_start=db.Column(db.DateTime)
 current_period_end=db.Column(db.DateTime)
 next_renewal_at=db.Column(db.DateTime)
 cancel_at_period_end=db.Column(db.Boolean,nullable=False,default=False,server_default="false")
 cancelled_at=db.Column(db.DateTime)
 replacement_of_id=db.Column(db.Integer,db.ForeignKey("recurring_subscription.id"),unique=True)
 starts_at=db.Column(db.DateTime)
 last_event_at=db.Column(db.DateTime)
 created_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)

class BillingEvent(db.Model):
 id=db.Column(db.Integer,primary_key=True)
 event_key=db.Column(db.String(180),nullable=False,unique=True)
 user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False,index=True)
 subscription_id=db.Column(db.Integer,db.ForeignKey("recurring_subscription.id"))
 kind=db.Column(db.String(40),nullable=False)
 message=db.Column(db.String(500),nullable=False)
 email_claimed_at=db.Column(db.DateTime)
 email_sent_at=db.Column(db.DateTime)
 created_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)

@login_manager.user_loader
def load_user(i): return db.session.get(User,int(i))


class SocialIdentity(db.Model):
    """Stable provider subject; no OAuth tokens are retained."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    provider = db.Column(db.String(16), nullable=False)
    provider_subject = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    user = db.relationship("User", backref=db.backref("social_identities", cascade="all, delete-orphan"))
    __table_args__ = (
        db.UniqueConstraint("provider", "provider_subject", name="uq_social_provider_subject"),
        db.UniqueConstraint("user_id", "provider", name="uq_social_user_provider"),
        db.CheckConstraint("provider IN ('google', 'apple')", name="ck_social_provider"),
    )


class Notification(db.Model):
    """Business activity history, committed alongside the originating operation."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    business_id = db.Column(db.Integer, db.ForeignKey('business.id', ondelete='CASCADE'), nullable=False)
    kind = db.Column(db.String(40), nullable=False)
    title = db.Column(db.String(140), nullable=False)
    body = db.Column(db.String(700), nullable=False)
    resource_id = db.Column(db.Integer)
    event_key = db.Column(db.String(180), nullable=False, unique=True)
    read_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.Index('ix_notification_owner_business_read', 'user_id', 'business_id', 'read_at'),
        db.Index('ix_notification_business_created', 'business_id', 'created_at', 'id'),
    )


class NotificationPreference(db.Model):
    """Future transactional email opt-ins; in-app activity remains available."""
    business_id = db.Column(db.Integer, db.ForeignKey('business.id', ondelete='CASCADE'), primary_key=True)
    low_stock_email = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
    out_of_stock_email = db.Column(db.Boolean, nullable=False, default=False, server_default='false')
