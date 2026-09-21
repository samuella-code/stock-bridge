import hashlib
import hmac
import json
import uuid
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import csrf, db
from app.models import Payment
from app.payments.service import PaystackError, initialize_transaction, verify_transaction

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _activate(payment, data):
    expected_metadata = data.get("metadata") or {}
    if not isinstance(expected_metadata, dict):
        return False
    if data.get("status") != "success" or data.get("reference") != payment.reference:
        return False
    if int(data.get("amount", 0)) != payment.amount_kobo or data.get("currency") != payment.currency:
        return False
    if str(expected_metadata.get("business_id")) != str(payment.business_id) or expected_metadata.get("product") != "stockbridge_lifetime":
        return False
    payment.status = "success"
    payment.paid_at = datetime.utcnow()
    payment.business.subscription_plan = "lifetime"
    payment.business.subscription_status = "active"
    payment.business.subscription_ends_at = None
    db.session.commit()
    return True


@payments_bp.get("/checkout")
@login_required
def checkout():
    return render_template("payments/checkout.html", business=current_user.businesses[0], price=current_app.config["LIFETIME_PRICE_NAIRA"], configured=bool(current_app.config["PAYSTACK_SECRET_KEY"]))


@payments_bp.post("/initialize")
@login_required
def initialize():
    business = current_user.businesses[0]
    if business.subscription_status == "active" and business.subscription_plan == "lifetime":
        flash("Your business already has lifetime access.", "success")
        return redirect(url_for("subscriptions.index"))
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    if not secret:
        flash("Payments are being configured. Please try again later.", "warning")
        return redirect(url_for("payments.checkout"))
    amount_kobo = current_app.config["LIFETIME_PRICE_NAIRA"] * 100
    reference = f"SB-{business.id}-{uuid.uuid4().hex}"
    payment = Payment(business_id=business.id, reference=reference, amount_kobo=amount_kobo)
    db.session.add(payment)
    db.session.commit()
    try:
        data = initialize_transaction(secret, current_user.email, amount_kobo, reference, url_for("payments.callback", _external=True, _scheme="https"), business.id)
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
        flash("The payment provider did not return a checkout link. Please try again.", "error")
        return redirect(url_for("payments.checkout"))
    return redirect(authorization_url)


@payments_bp.get("/callback")
@login_required
def callback():
    reference = request.args.get("reference", "")
    payment = Payment.query.filter_by(reference=reference, business_id=current_user.businesses[0].id).first()
    if not payment:
        flash("We could not find that payment.", "error")
        return redirect(url_for("payments.checkout"))
    if payment.status == "success":
        return render_template("payments/success.html", business=payment.business)
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    if not secret:
        flash("Payment verification is not configured.", "error")
        return redirect(url_for("payments.checkout"))
    try:
        data = verify_transaction(secret, reference)
    except PaystackError as error:
        current_app.logger.warning("Payment verification failed for %s: %s", reference, error)
        flash("Payment verification is still pending. Please contact support if you were charged.", "warning")
        return redirect(url_for("payments.checkout"))
    if not _activate(payment, data):
        flash("The payment details could not be verified.", "error")
        return redirect(url_for("payments.checkout"))
    return render_template("payments/success.html", business=payment.business)


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
            _activate(payment, data)
    return jsonify(status="ok"), 200
