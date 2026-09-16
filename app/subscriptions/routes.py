from flask import Blueprint, render_template
from flask_login import current_user, login_required

subscriptions_bp = Blueprint("subscriptions", __name__, url_prefix="/plans")

PLANS = (
    {"slug":"starter","name":"Starter","price":3000,"description":"For one shop getting control of daily stock.","features":("Products and inventory","Sales and expenses","Low-stock alerts")},
    {"slug":"business","name":"Business","price":7500,"description":"For growing retailers that need stronger insights.","features":("Everything in Starter","Restocking recommendations","Advanced reports","Multiple team members soon")},
    {"slug":"pro","name":"Pro","price":15000,"description":"For established retailers and multiple locations.","features":("Everything in Business","Multiple branches soon","Priority support","Data exports and backups soon")},
)

@subscriptions_bp.get("/")
@login_required
def index():
    return render_template("subscriptions/index.html", business=current_user.businesses[0], plans=PLANS)
