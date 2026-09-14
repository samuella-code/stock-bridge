from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required
from app.models import Product

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def index():
    return redirect(url_for("main.dashboard")) if current_user.is_authenticated else redirect(url_for("auth.login"))

@main_bp.route("/dashboard")
@login_required
def dashboard():
    business = current_user.businesses[0] if current_user.businesses else None
    products = Product.query.filter_by(business_id=business.id).all() if business else []
    low_stock_products = sorted((p for p in products if p.is_low_stock), key=lambda p: p.stock_quantity - p.minimum_stock_level)[:5]
    metrics = {"sales_today": 0, "expenses_today": 0, "gross_profit": 0,
               "product_count": len(products), "low_stock_count": sum(p.is_low_stock for p in products)}
    return render_template("dashboard.html", business=business, metrics=metrics,
                           recent_transactions=[], low_stock_products=low_stock_products)
