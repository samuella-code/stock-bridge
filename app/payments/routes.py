import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user

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


@payments_bp.get("/checkout")
def checkout():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("payments/checkout.html", price=current_app.config["LIFETIME_PRICE_NAIRA"], configured=bool(current_app.config["PAYSTACK_SECRET_KEY"]))


@payments_bp.post("/initialize")
def initialize():
    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("payments.checkout"))
    if User.query.filter_by(email=email).first():
        flash("An account already uses that email. Log in instead.", "warning")
        return redirect(url_for("auth.login"))
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    if not secret:
        flash("Payments are being configured. Please try again later.", "warning")
        return redirect(url_for("payments.checkout"))
    amount_kobo = current_app.config["LIFETIME_PRICE_NAIRA"] * 100
    reference = f"SB-{uuid.uuid4().hex}"
    payment = Payment(customer_email=email, reference=reference, amount_kobo=amount_kobo)
    db.session.add(payment)
    db.session.commit()
    try:
        data = initialize_transaction(secret, email, amount_kobo, reference, url_for("payments.callback", _external=True, _scheme="https"))
    except PaystackError as error:
        payment.status = "failed"
        db.session.commit()
        current_app.logger.warning("Payment initialization failed for %s: %s", reference, error)
        flash(str(error), "error")
        return redirect(url_for("payments.checkout"))
    authorization_url = data.get("authorization_url")
    if not authorization_url:
        payment.status = "failed"
        db.session.commit()
        flash("The payment provider did not return a checkout link.", "error")
        return redirect(url_for("payments.checkout"))
    return redirect(authorization_url)


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
    if payment.status != "success":
        try:
            data = verify_transaction(current_app.config["PAYSTACK_SECRET_KEY"], reference)
        except PaystackError as error:
            current_app.logger.warning("Payment verification failed for %s: %s", reference, error)
            flash("Payment verification is pending. Please try again.", "warning")
            return redirect(url_for("payments.checkout"))
        if not _confirm(payment, data):
            flash("The payment details could not be verified.", "error")
            return redirect(url_for("payments.checkout"))
    session["paid_claim_token"] = payment.claim_token
    return redirect(url_for("auth.signup"))


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
