"""Public draft policies: presentation only, no account or provider operations."""
from flask import Blueprint, render_template

legal_bp = Blueprint("legal", __name__)


def page(template):
    return render_template("legal/" + template + ".html")


@legal_bp.get("/privacy")
def privacy():
    return page("privacy")


@legal_bp.get("/terms")
def terms():
    return page("terms")


@legal_bp.get("/refund-policy")
def refunds():
    return page("refunds")


@legal_bp.get("/subscription-terms")
def subscriptions():
    from app.main.routes import public_plan_prices
    return render_template("legal/subscriptions.html", plan_prices=public_plan_prices())


@legal_bp.get("/contact")
def contact():
    return page("contact")
