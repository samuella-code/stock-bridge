import hashlib
import hmac
import json
import os
import secrets
import uuid
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import csrf, db
from app.models import Payment, User
from app.admin.routes import log
from app.payments.service import PaystackError, initialize_transaction, verify_transaction

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _confirm(payment, data, *, commit=True):
    metadata = data.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            return False
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    mode = "live" if secret.startswith("sk_live_") else "test" if secret.startswith("sk_test_") else None
    valid = (mode is not None and isinstance(metadata, dict) and type(data.get("amount")) is int
        and data.get("status") == "success" and data.get("reference") == payment.reference
        and data["amount"] == payment.amount_kobo and data.get("currency") == payment.currency
        and data.get("domain") == mode and metadata.get("product") == "stockbridge_lifetime"
        and isinstance(metadata.get("customer_email"), str)
        and metadata["customer_email"].lower() == payment.customer_email.lower())
    if not valid:
        return False
    first_confirmation = payment.status != "success" or payment.paid_at is None
    payment.status = "success"
    payment.paid_at = payment.paid_at or datetime.utcnow()
    payment.claim_token = payment.claim_token or secrets.token_urlsafe(32)
    if first_confirmation:
        log("ACCESS_PAYMENT_SUCCESSFUL", f"Verified access payment {payment.reference}.", business_id=payment.business_id)
    if commit:
        db.session.commit()
    return True


def _activate_account(payment, *, commit=True):
    users = User.query.filter(func.lower(User.email) == payment.customer_email.lower()).limit(2).all()
    if len(users) != 1 or users[0].role != "user" or users[0].admin_enabled or payment.status != "success":
        return False
    existing_user = users[0]
    if len(existing_user.businesses) != 1:
        return False
    business = existing_user.businesses[0]
    if payment.business_id is not None and payment.business_id != business.id:
        return False
    business.subscription_plan = "lifetime"
    business.subscription_status = "active"
    business.subscription_ends_at = None
    payment.business_id = business.id
    if commit:
        db.session.commit()
    return True


@payments_bp.get("/checkout")
@login_required
def checkout():
    if current_user.is_authenticated and current_user.businesses[0].has_write_access:
        return redirect(url_for("main.dashboard"))
    configured = _payments_configured()
    return render_template("payments/checkout.html", price=current_app.config["LIFETIME_PRICE_NAIRA"], configured=configured, account_email=current_user.email)


@payments_bp.post("/initialize")
@login_required
def initialize():
    email = current_user.email
    if not email or "@" not in email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("payments.checkout"))
    existing_user = current_user
    if existing_user and existing_user.businesses[0].has_write_access:
        flash("That account already has lifetime access. Log in instead.", "success")
        return redirect(url_for("auth.login"))
    if not _payments_configured():
        flash("Payments are being configured. Please try again later.", "warning")
        return redirect(url_for("payments.checkout"))
    amount_kobo = current_app.config["LIFETIME_PRICE_NAIRA"] * 100
    reference = f"SB-{uuid.uuid4().hex}"
    payment = Payment(customer_email=email, reference=reference, amount_kobo=amount_kobo)
    db.session.add(payment)
    callback_url = (f"https://{current_app.config['CUSTOMER_HOST']}{url_for('payments.callback')}"
        if os.getenv("VERCEL_ENV") == "production" else url_for("payments.callback", _external=True))
    try:
        details = initialize_transaction(current_app.config["PAYSTACK_SECRET_KEY"], email,
            amount_kobo, reference, callback_url)
        checkout_url = details.get("authorization_url", "")
        parsed = urlparse(checkout_url)
        if (details.get("reference") != reference or parsed.scheme != "https"
                or parsed.hostname != "checkout.paystack.com"):
            raise PaystackError("Invalid checkout response from Paystack.")
        db.session.commit()
    except PaystackError:
        db.session.rollback()
        current_app.logger.exception("Could not initialize Paystack transaction")
        flash("Secure checkout could not open. Please try again.", "error")
        return redirect(url_for("payments.checkout"))
    return redirect(checkout_url, code=303)


def _payments_configured():
    public = current_app.config["PAYSTACK_PUBLIC_KEY"]
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    if os.getenv("VERCEL_ENV") and os.getenv("VERCEL_ENV") != "production":
        return False
    return ((public.startswith("pk_live_") and secret.startswith("sk_live_"))
        or (public.startswith("pk_test_") and secret.startswith("sk_test_")))


@payments_bp.get("/callback")
def callback():
    reference = request.args.get("reference", "")
    payment = Payment.query.filter_by(reference=reference,
        customer_email=current_user.email).first() if current_user.is_authenticated else None
    if not payment:
        flash("We could not find that payment.", "error")
        return redirect(url_for("payments.checkout"))
    if payment.business_id:
        flash("This payment has already been used.", "warning")
        return redirect(url_for("auth.login"))
    if payment.status != "success" and _payments_configured():
        try:
            verified = verify_transaction(current_app.config["PAYSTACK_SECRET_KEY"], reference)
            _confirm(payment, verified)
        except PaystackError:
            current_app.logger.warning("Paystack verification pending for %s", reference)
    if _activate_account(payment):
        flash("Payment confirmed. Your lifetime access is active.", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("payments/pending.html", reference=reference)


@payments_bp.get("/status/<reference>")
@login_required
def status(reference):
    payment = Payment.query.filter_by(reference=reference, customer_email=current_user.email).first_or_404()
    if payment.status == "success":
        _activate_account(payment)
    return jsonify(status=payment.status, redirect=url_for("main.dashboard") if payment.business_id else None)


@payments_bp.post("/webhook")
@csrf.exempt
def webhook():
    if os.getenv("VERCEL_ENV") and os.getenv("VERCEL_ENV") != "production":
        return jsonify(status="not available"), 404
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    signature = request.headers.get("x-paystack-signature", "")
    expected = hmac.new(secret.encode(), request.get_data(), hashlib.sha512).hexdigest() if secret else ""
    if not secret or not hmac.compare_digest(signature, expected):
        return jsonify(status="invalid signature"), 401
    try:
        event = json.loads(request.get_data(as_text=True))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify(status="invalid payload"), 400
    if event.get("event") == "charge.success":
        data = event.get("data") or {}
        payment = Payment.query.filter_by(reference=data.get("reference")).first()
        if payment:
            if payment.status != "success":
                _confirm(payment, data)
            if payment.status == "success":
                _activate_account(payment)
    return jsonify(status="ok"), 200
