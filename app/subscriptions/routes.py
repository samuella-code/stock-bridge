from flask import Blueprint, current_app, render_template
from flask_login import current_user

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/plans")

@subscriptions_bp.get("/")
def index():
    business = current_user.businesses[0] if current_user.is_authenticated else None
    return render_template("subscriptions/index.html", business=business, lifetime_price=current_app.config["LIFETIME_PRICE_NAIRA"])
