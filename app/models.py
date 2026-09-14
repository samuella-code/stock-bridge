from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash
from app import db, login_manager

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    businesses = db.relationship("Business", backref="owner", lazy=True, cascade="all, delete-orphan")
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Business(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    products = db.relationship("Product", backref="business", lazy=True, cascade="all, delete-orphan")

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("business.id"), nullable=False, index=True)
    name = db.Column(db.String(140), nullable=False)
    sku = db.Column(db.String(60))
    category = db.Column(db.String(80))
    buying_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    selling_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    stock_quantity = db.Column(db.Integer, nullable=False, default=0)
    minimum_stock_level = db.Column(db.Integer, nullable=False, default=0)
    supplier_name = db.Column(db.String(140))
    supplier_lead_time = db.Column(db.Integer, nullable=False, default=2)
    safety_stock = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("business_id", "sku", name="uq_product_business_sku"),)
    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.minimum_stock_level

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))
