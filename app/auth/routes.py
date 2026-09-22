from datetime import datetime
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired

from app import db
from app.email_service import (
    read_password_reset_token,
    read_verification_token,
    send_password_reset_email,
    send_verification_email,
)
from app.models import Business, Payment, User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        business_name = request.form.get("business_name", "").strip()
        email = request.form.get("email", "").strip().lower()
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
        business = Business(user_id=user.id, name=business_name, subscription_plan="starter", subscription_status="inactive", trial_started_at=now, trial_ends_at=now)
        db.session.add(business)
        db.session.commit()

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

    return render_template("auth/signup.html")


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


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first() if email else None
        if user:
            try:
                send_password_reset_email(user)
            except Exception:
                current_app.logger.exception("Could not send password reset email to %s", user.email)
        flash("If that email belongs to a StockBridge account, a password-reset link has been sent.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        logout_user()
    try:
        email = read_password_reset_token(token)
    except SignatureExpired:
        flash("That password-reset link has expired. Request a new one.", "warning")
        return redirect(url_for("auth.forgot_password"))
    except BadSignature:
        flash("That password-reset link is invalid.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(email=email.lower()).first()
    if not user:
        flash("That password-reset link is invalid.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirm_password:
            flash("The passwords do not match.", "error")
        else:
            user.set_password(password)
            db.session.commit()
            flash("Your password has been updated. You can now log in.", "success")
            return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", token=token)


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
    if sent:
        current_user.verification_sent_at = datetime.utcnow()
        db.session.commit()
    flash("A new verification email was sent." if sent else "Email is not configured yet. Please contact the StockBridge owner.", "success" if sent else "warning")
    return redirect(url_for("auth.verification_pending"))


@auth_bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
