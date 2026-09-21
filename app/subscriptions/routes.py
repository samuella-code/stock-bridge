from flask import Blueprint, current_app, render_template
from flask_login import current_user, login_required

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/plans")

@subscriptions_bp.get("/")
@login_required
def index():
    return render_template("subscriptions/index.html", business=current_user.businesses[0], lifetime_price=current_app.config["LIFETIME_PRICE_NAIRA"])
