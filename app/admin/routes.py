from functools import wraps

from flask import Blueprint, abort, current_app, render_template
from flask_login import current_user, login_required

from app.models import Business, User

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def owner_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        owners = {email.strip().lower() for email in current_app.config["ADMIN_EMAILS"].split(",") if email.strip()}
        if current_user.email.lower() not in owners:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@admin_bp.get("/")
@owner_required
def index():
    users = User.query.order_by(User.created_at.desc()).all()
    metrics = {
        "users": len(users),
        "verified": sum(user.email_verified_at is not None for user in users),
        "lifetime": Business.query.filter_by(subscription_plan="lifetime", subscription_status="active").count(),
    }
    return render_template("admin/index.html", users=users, metrics=metrics)
