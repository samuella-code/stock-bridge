from flask import Blueprint, redirect, render_template, url_for
from flask_login import current_user, login_required


main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    business = current_user.businesses[0] if current_user.businesses else None

    metrics = {
        "sales_today": 0,
        "expenses_today": 0,
        "gross_profit": 0,
        "product_count": 0,
        "low_stock_count": 0,
    }

    recent_transactions = []
    low_stock_products = []

    return render_template(
        "dashboard.html",
        business=business,
        metrics=metrics,
        recent_transactions=recent_transactions,
        low_stock_products=low_stock_products,
    )
