import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer


def verification_token(email):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).dumps(email, salt="verify-email")


def read_verification_token(token, max_age=86400):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(token, salt="verify-email", max_age=max_age)


def password_reset_token(email):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).dumps(email, salt="reset-password")


def read_password_reset_token(token, max_age=3600):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(token, salt="reset-password", max_age=max_age)


def _send_email(subject, recipient, body):
    host = current_app.config["SMTP_HOST"]
    if not host:
        current_app.logger.warning("SMTP is not configured. Email for %s was not sent.", recipient)
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(
        (current_app.config["SMTP_FROM_NAME"], current_app.config["SMTP_FROM_EMAIL"])
    )
    message["To"] = recipient
    message.set_content(body)
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=15) as smtp:
        if current_app.config["SMTP_USE_TLS"]:
            smtp.starttls()
        username = current_app.config["SMTP_USERNAME"]
        if username:
            smtp.login(username, current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)
    return True


def send_verification_email(user):
    link = url_for("auth.verify_email", token=verification_token(user.email), _external=True, _scheme="https")
    body = f"Hello {user.full_name},\n\nVerify your StockBridge account by opening this link:\n{link}\n\nThis link expires in 24 hours. If you did not create this account, ignore this email."
    return _send_email("Verify your StockBridge email", user.email, body)


def send_password_reset_email(user):
    link = url_for("auth.reset_password", token=password_reset_token(user.email), _external=True, _scheme="https")
    body = f"Hello {user.full_name},\n\nCreate a new StockBridge password by opening this link:\n{link}\n\nThis link expires in 1 hour. If you did not request a password reset, ignore this email."
    return _send_email("Reset your StockBridge password", user.email, body)
