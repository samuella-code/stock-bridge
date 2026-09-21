from datetime import datetime
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user, logout_user

from app import db
from app.models import Business, Payment, User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    claim_token = session.get("paid_claim_token")
    payment = Payment.query.filter_by(claim_token=claim_token, status="success", business_id=None).first() if claim_token else None
    if not payment:
        flash("Pay the one-time ₦3,000 access fee before creating an account.", "warning")
        return redirect(url_for("subscriptions.index"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        business_name = request.form.get("business_name", "").strip()
        email = payment.customer_email
        password = request.form.get("password", "")

        if not all([full_name, business_name, email, password]):
            flash("Please fill in every field.", "error")
            return render_template("auth/signup.html")

        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("auth/signup.html")

        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "error")
            return render_template("auth/signup.html")

        user = User(full_name=full_name, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        now = datetime.utcnow()
        business = Business(user_id=user.id, name=business_name, subscription_plan="lifetime", subscription_status="active", trial_started_at=now, trial_ends_at=now)
        db.session.add(business)
        db.session.flush()
        payment.business_id = business.id
        db.session.commit()

        session.pop("paid_claim_token", None)
        login_user(user)
        flash("Welcome to StockBridge. Your lifetime access is active.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/signup.html", paid_email=payment.customer_email)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()

        if not user or not user.check_password(password):
            flash("Email or password is incorrect.", "error")
            return render_template("auth/login.html")

        login_user(user)
        flash("Welcome back.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
