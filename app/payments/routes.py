import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import csrf, db
from app.models import Payment, User
from app.admin.routes import log
from app.payments.service import PaystackError, initialize_transaction, verify_transaction

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _confirm(payment, data):
    metadata = data.get("metadata") or {}
    live_key = current_app.config["PAYSTACK_SECRET_KEY"].startswith("sk_live_")
    expected_domain = "live" if live_key else "test"
    valid = (
        isinstance(metadata, dict)
        and data.get("status") == "success"
        and data.get("reference") == payment.reference
        and int(data.get("amount", 0)) == payment.amount_kobo
        and data.get("currency") == payment.currency
        and data.get("domain") == expected_domain
        and metadata.get("product") == "stockbridge_lifetime"
        and metadata.get("customer_email", "").lower() == payment.customer_email.lower()
    )
    if not valid:
        current_app.logger.warning("Rejected Paystack confirmation for reference %s", payment.reference)
        return False

    if payment.status != "success":
        payment.status = "success"
        payment.paid_at = payment.paid_at or datetime.utcnow()
        payment.claim_token = payment.claim_token or secrets.token_urlsafe(32)
        log(
            "ACCESS_PAYMENT_SUCCESSFUL",
            f"Verified access payment {payment.reference}.",
            business_id=payment.business_id,
        )
        db.session.commit()
    return True


def _activate_account(payment):
    existing_user = User.query.filter_by(email=payment.customer_email).first()
    if not existing_user or payment.status != "success" or not existing_user.businesses:
        return False

    business = existing_user.businesses[0]
    if payment.business_id and payment.business_id != business.id:
        current_app.logger.warning("Payment %s is already attached to another business.", payment.reference)
        return False

    business.subscription_plan = "lifetime"
    business.subscription_status = "active"
    business.subscription_ends_at = None
    payment.business_id = business.id
    db.session.commit()
    return True


@payments_bp.get("/checkout")
@login_required
def checkout():
    if current_user.businesses[0].has_write_access:
        return redirect(url_for("main.dashboard"))
    configured = bool(
        current_app.config["PAYSTACK_PUBLIC_KEY"]
        and current_app.config["PAYSTACK_SECRET_KEY"]
    )
    return render_template(
        "payments/checkout.html",
        price=current_app.config["LIFETIME_PRICE_NAIRA"],
        configured=configured,
        account_email=current_user.email,
    )


@payments_bp.post("/initialize")
@login_required
def initialize():
    email = current_user.email
    if not email or "@" not in email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("payments.checkout"))

    if current_user.businesses[0].has_write_access:
        flash("This account already has lifetime access.", "success")
        return redirect(url_for("main.dashboard"))

    secret_key = current_app.config["PAYSTACK_SECRET_KEY"]
    if not secret_key:
        flash("Payments are being configured. Please try again later.", "warning")
        return redirect(url_for("payments.checkout"))

    amount_kobo = current_app.config["LIFETIME_PRICE_NAIRA"] * 100
    reference = f"SB-{uuid.uuid4().hex}"
    payment = Payment(
        customer_email=email,
        reference=reference,
        amount_kobo=amount_kobo,
        status="initialized",
    )
    db.session.add(payment)
    db.session.commit()

    try:
        transaction = initialize_transaction(
            secret_key=secret_key,
            email=email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=url_for("payments.callback", _external=True, _scheme="https"),
        )
    except PaystackError:
        payment.status = "failed"
        db.session.commit()
        current_app.logger.exception("Paystack initialization failed for %s", reference)
        flash("Paystack could not start the payment. Please try again.", "error")
        return redirect(url_for("payments.checkout"))

    authorization_url = transaction.get("authorization_url")
    if not authorization_url:
        payment.status = "failed"
        db.session.commit()
        current_app.logger.error("Paystack returned no authorization URL for %s", reference)
        flash("Paystack could not start the payment. Please try again.", "error")
        return redirect(url_for("payments.checkout"))

    return redirect(authorization_url)


@payments_bp.get("/callback")
@login_required
def callback():
    reference = request.args.get("reference", "").strip()
    payment = Payment.query.filter_by(
        reference=reference,
        customer_email=current_user.email,
    ).first()

    if not payment:
        flash("We could not find that payment.", "error")
        return redirect(url_for("payments.checkout"))

    if payment.business_id:
        flash("Your lifetime access is already active.", "success")
        return redirect(url_for("main.dashboard"))

    try:
        data = verify_transaction(current_app.config["PAYSTACK_SECRET_KEY"], reference)
    except PaystackError:
        current_app.logger.exception("Paystack verification failed for %s", reference)
        return render_template("payments/pending.html", reference=reference)

    if _confirm(payment, data) and _activate_account(payment):
        flash("Payment confirmed. Your lifetime access is active.", "success")
        return redirect(url_for("main.dashboard"))

    flash("Payment has not been confirmed yet. If you were charged, please wait a moment and try again.", "warning")
    return render_template("payments/pending.html", reference=reference)


@payments_bp.get("/status/<reference>")
@login_required
def status(reference):
    payment = Payment.query.filter_by(
        reference=reference,
        customer_email=current_user.email,
    ).first_or_404()

    if payment.status != "success":
        try:
            data = verify_transaction(current_app.config["PAYSTACK_SECRET_KEY"], reference)
            _confirm(payment, data)
        except PaystackError:
            current_app.logger.info("Paystack status check still pending for %s", reference)

    if payment.status == "success":
        _activate_account(payment)

    return jsonify(
        status=payment.status,
        redirect=url_for("main.dashboard") if payment.business_id else None,
    )


@payments_bp.post("/webhook")
@csrf.exempt
def webhook():
    secret = current_app.config["PAYSTACK_SECRET_KEY"]
    signature = request.headers.get("x-paystack-signature", "")
    raw_body = request.get_data()
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest() if secret else ""

    if not secret or not hmac.compare_digest(signature, expected):
        return jsonify(status="invalid signature"), 401

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify(status="invalid payload"), 400

    if event.get("event") == "charge.success":
        data = event.get("data") or {}
        payment = Payment.query.filter_by(reference=data.get("reference")).first()
        if payment and _confirm(payment, data):
            _activate_account(payment)

    return jsonify(status="ok"), 200
