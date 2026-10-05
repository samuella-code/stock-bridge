"""Transactional email rendering and SMTP transport."""
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, render_template, url_for
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import or_


def verification_token(email):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).dumps(email, salt="verify-email")


def read_verification_token(token, max_age=86400):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(token, salt="verify-email", max_age=max_age)


def password_reset_token(email):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).dumps(email, salt="reset-password")


def read_password_reset_token(token, max_age=3600):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(token, salt="reset-password", max_age=max_age)


def customer_url(endpoint, **values):
    path = url_for(endpoint, **values)
    from flask import request
    if (os.getenv("VERCEL_ENV") == "production"
            or request.host.split(":", 1)[0].lower() == current_app.config["ADMIN_HOST"]):
        return f"https://{current_app.config['CUSTOMER_HOST']}{path}"
    return url_for(endpoint, _external=True, **values)


def _send_email(subject, recipient, body, html=None):
    host = current_app.config["SMTP_HOST"]
    if not host or not current_app.config["SMTP_FROM_EMAIL"]:
        current_app.logger.warning("SMTP sender is not configured; transactional email was not sent.")
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((
        current_app.config["SMTP_FROM_NAME"],
        current_app.config["SMTP_FROM_EMAIL"],
    ))
    message["To"] = recipient
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=15) as smtp:
        if current_app.config["SMTP_USE_TLS"]:
            smtp.starttls()
        username = current_app.config["SMTP_USERNAME"]
        if username:
            smtp.login(username, current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)
    return True


def _transactional(template, subject, recipient, name, body, **context):
    html = render_template(f"email/{template}.html", name=name, **context)
    return _send_email(subject, recipient, body, html=html)


def send_verification_email(user):
    link = customer_url("auth.verify_email", token=verification_token(user.email))
    body = (f"Hi {user.full_name.split()[0]},\n\nWelcome to StockBridge. Verify your email to continue setting up your business:\n"
            f"{link}\n\nThis link expires in 24 hours. If you didn't create a StockBridge account, ignore this email.")
    return _transactional("verify", "Verify your StockBridge email", user.email, user.full_name, body, link=link)


def send_password_reset_email(user):
    link = customer_url("auth.reset_password", token=password_reset_token(user.email))
    body = (f"Hi {user.full_name.split()[0]},\n\nWe received a request to reset your StockBridge password.\n"
            f"{link}\n\nThis link expires in 1 hour. If you didn't request this, ignore this email.")
    return _transactional("password_reset", "Reset your StockBridge password", user.email, user.full_name, body, link=link)


def send_welcome_email(user):
    link = customer_url("main.dashboard")
    if current_app.config.get("SUBSCRIPTIONS_ENABLED"):
        body = f"Hi {user.full_name.split()[0]},\n\nWelcome to StockBridge. Your verified account can start its 7-day trial without a card. Record business activity once and keep your numbers up to date. Choose Basic or Plus when ready.\n{link}"
        return _transactional("billing", "Welcome to StockBridge", user.email, user.full_name, body, link=link, message=body)
    body = (f"Hi {user.full_name.split()[0]},\n\nWelcome to StockBridge. Track stock, sales, expenses and profit, and know when to restock.\n\n"
            "Create Account → Verify Email → Explore StockBridge → Pay ₦3,000 once → Lifetime Access.\n"
            f"Explore StockBridge: {link}\n\nBusiness tools unlock after the one-time payment.")
    return _transactional("welcome", "Welcome to StockBridge", user.email, user.full_name, body, link=link)


def send_payment_success_email(user, business, payment):
    link = customer_url("main.dashboard")
    paid_at = payment.paid_at.strftime("%d %b %Y %H:%M UTC")
    amount = f"₦{payment.amount_kobo / 100:,.2f}"
    body = (f"Hi {user.full_name.split()[0]},\n\nPayment successful. Your StockBridge Lifetime Access is active.\n"
            f"Business: {business.name}\nAmount: {amount} ({payment.currency})\nReference: {payment.reference}\n"
            f"Paid: {paid_at}\nAccess: Lifetime Access\nStatus: Active\n\n"
            f"Open StockBridge: {link}\n\nOne-time payment. No subscription. No recurring charges.")
    return _transactional("payment_success", "Your StockBridge Lifetime Access is active", user.email,
                          user.full_name, body, link=link, business=business, payment=payment,
                          amount=amount, paid_at=paid_at)


def send_account_status_email(user, *, active, business=None):
    kind = "business" if business else "account"
    if active:
        link = customer_url("auth.login")
        subject = f"Your StockBridge {kind} has been reactivated"
        body = f"Hi {user.full_name.split()[0]},\n\nYour StockBridge {kind} has been reactivated. You can sign in and continue using StockBridge.\n{link}"
    else:
        link = None
        subject = f"Your StockBridge {kind} has been suspended"
        body = f"Hi {user.full_name.split()[0]},\n\nAccess to your StockBridge {kind} is temporarily restricted. Contact StockBridge support if you need help."
    return _transactional("account_status", subject, user.email, user.full_name, body,
                          link=link, active=active, kind=kind)


def send_password_changed_email(user):
    link = customer_url("auth.forgot_password")
    body = (f"Hi {user.full_name.split()[0]},\n\nYour StockBridge password was changed. "
            f"If this wasn't you, request a new password immediately: {link}")
    return _transactional("password_changed", "Your StockBridge password was changed", user.email,
                          user.full_name, body, link=link)


def safely_send(sender, *args, **kwargs):
    """Notification failure cannot roll back a committed account or payment."""
    try:
        return sender(*args, **kwargs)
    except Exception:
        current_app.logger.exception("Transactional email delivery failed: %s", sender.__name__)
        return False


def send_access_receipt_once(payment):
    """Claim a verified activated payment before SMTP to guard callback/webhook races.

    Failed sends release the claim; a crashed claim expires after five minutes.
    """
    from app import db
    from app.models import Business, Payment, User

    if (payment.status != "success" or not payment.paid_at or payment.currency != "NGN"
            or payment.amount_kobo != current_app.config["LIFETIME_PRICE_NAIRA"] * 100
            or not payment.business_id):
        return False
    business = db.session.get(Business, payment.business_id)
    user = db.session.get(User, business.user_id) if business else None
    if not business or not business.has_write_access or not user or user.role != "user" or user.admin_enabled:
        return False
    now = datetime.utcnow()
    claimed = (db.session.query(Payment).filter(
        Payment.id == payment.id, Payment.receipt_email_sent_at.is_(None),
        or_(Payment.receipt_email_claimed_at.is_(None),
            Payment.receipt_email_claimed_at < now - timedelta(minutes=5)))
        .update({"receipt_email_claimed_at": now}, synchronize_session=False))
    db.session.commit()
    if not claimed:
        return False
    sent = safely_send(send_payment_success_email, user, business, payment)
    if sent:
        (db.session.query(Payment).filter(Payment.id == payment.id,
            Payment.receipt_email_claimed_at == now, Payment.receipt_email_sent_at.is_(None))
            .update({"receipt_email_sent_at": datetime.utcnow()}, synchronize_session=False))
    else:
        (db.session.query(Payment).filter(Payment.id == payment.id,
            Payment.receipt_email_claimed_at == now)
            .update({"receipt_email_claimed_at": None}, synchronize_session=False))
    db.session.commit()
    return bool(sent)
