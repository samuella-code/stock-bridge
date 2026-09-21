from datetime import datetime
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired

from app import db
from app.email_service import read_verification_token, send_verification_email
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
        user.verification_sent_at = datetime.utcnow()
        db.session.commit()
        try:
            sent = send_verification_email(user)
        except Exception:
            sent = False
            current_app.logger.exception("Could not send verification email to %s", user.email)
        flash("Account created. Check your email to verify your account." if sent else "Account created, but the verification email could not be sent. Please use resend after email is configured.", "success" if sent else "warning")
        return redirect(url_for("auth.verification_pending"))

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
        if not user.email_verified_at:
            return redirect(url_for("auth.verification_pending"))
        flash("Welcome back.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@auth_bp.get("/verify-pending")
@login_required
def verification_pending():
    if current_user.email_verified_at:
        return redirect(url_for("main.dashboard"))
    return render_template("auth/verify_pending.html")


@auth_bp.get("/verify/<token>")
def verify_email(token):
    try:
        email = read_verification_token(token)
    except SignatureExpired:
        flash("That verification link has expired. Log in to request another.", "warning")
        return redirect(url_for("auth.login"))
    except BadSignature:
        flash("That verification link is invalid.", "error")
        return redirect(url_for("auth.login"))
    user = User.query.filter_by(email=email.lower()).first_or_404()
    user.email_verified_at = user.email_verified_at or datetime.utcnow()
    db.session.commit()
    if not current_user.is_authenticated:
        login_user(user)
    flash("Email verified. Welcome to StockBridge.", "success")
    return redirect(url_for("main.dashboard"))


@auth_bp.post("/resend-verification")
@login_required
def resend_verification():
    if current_user.email_verified_at:
        return redirect(url_for("main.dashboard"))
    try:
        sent = send_verification_email(current_user)
    except Exception:
        sent = False
        current_app.logger.exception("Could not resend verification email to %s", current_user.email)
    current_user.verification_sent_at = datetime.utcnow()
    db.session.commit()
    flash("A new verification email was sent." if sent else "Email is not configured yet. Please contact the StockBridge owner.", "success" if sent else "warning")
    return redirect(url_for("auth.verification_pending"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
