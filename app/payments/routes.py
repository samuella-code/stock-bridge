import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import csrf, db
from app.models import Payment, User
from app.payments.service import PaystackError, initialize_transaction, verify_transaction

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _confirm(payment, data):
    metadata = data.get("metadata") or {}
    valid = isinstance(metadata, dict) and data.get("status") == "success" and data.get("reference") == payment.reference and int(data.get("amount", 0)) == payment.amount_kobo and data.get("currency") == payment.currency and metadata.get("product") == "stockbridge_lifetime" and metadata.get("customer_email", "").lower() == payment.customer_email.lower()
    if not valid:
        return False
    payment.status = "success"
    payment.paid_at = payment.paid_at or datetime.utcnow()
    payment.claim_token = payment.claim_token or secrets.token_urlsafe(32)
    db.session.commit()
    return True


def _activate_account(payment):
    existing_user = User.query.filter_by(email=payment.customer_email).first()
    if not existing_user or payment.status != "success":
        return False
    business = existing_user.businesses[0]
    business.subscription_plan = "lifetime"
    business.subscription_status = "active"
    business.subscription_ends_at = None
    payment.business_id = business.id
    db.session.commit()
    return True


@payments_bp.get("/checkout")
@login_required
def checkout():
    if current_user.is_authenticated and current_user.businesses[0].has_write_access:
        return redirect(url_for("main.dashboard"))
    configured = bool(current_app.config["PAYSTACK_PUBLIC_KEY"] and current_app.config["PAYSTACK_SECRET_KEY"])
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
    public_key = current_app.config["PAYSTACK_PUBLIC_KEY"]
    if not public_key or not current_app.config["PAYSTACK_SECRET_KEY"]:
        flash("Payments are being configured. Please try again later.", "warning")
        return redirect(url_for("payments.checkout"))
    amount_kobo = current_app.config["LIFETIME_PRICE_NAIRA"] * 100
    reference = f"SB-{uuid.uuid4().hex}"
    payment = Payment(customer_email=email, reference=reference, amount_kobo=amount_kobo)
    db.session.add(payment)
    db.session.commit()
    return render_template("payments/launch.html", payment=payment, public_key=public_key)


@payments_bp.get("/callback")
def callback():
    reference = request.args.get("reference", "")
    payment = Payment.query.filter_by(reference=reference).first()
    if not payment:
        flash("We could not find that payment.", "error")
        return redirect(url_for("payments.checkout"))
    if payment.business_id:
        flash("This payment has already been used.", "warning")
        return redirect(url_for("auth.login"))
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
        if payment and payment.status != "success":
            _confirm(payment, data)
    return jsonify(status="ok"), 200
