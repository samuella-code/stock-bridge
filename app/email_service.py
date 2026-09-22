import smtplib
from email.message import EmailMessage

from flask import current_app, url_for
from itsdangerous import URLSafeTimedSerializer


def verification_token(email):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).dumps(email, salt="verify-email")


def read_verification_token(token, max_age=86400):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"]).loads(token, salt="verify-email", max_age=max_age)


def send_verification_email(user):
    link = url_for("auth.verify_email", token=verification_token(user.email), _external=True, _scheme="https")
    host = current_app.config["SMTP_HOST"]
    if not host:
        current_app.logger.warning("SMTP is not configured. Verification link for %s: %s", user.email, link)
        return False
    message = EmailMessage()
    message["Subject"] = "Verify your StockBridge email"
    message["From"] = current_app.config["SMTP_FROM_EMAIL"]
    message["To"] = user.email
    message.set_content(f"Hello {user.full_name},\n\nVerify your StockBridge account by opening this link:\n{link}\n\nThis link expires in 24 hours. If you did not create this account, ignore this email.")
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=15) as smtp:
        if current_app.config["SMTP_USE_TLS"]:
            smtp.starttls()
        username = current_app.config["SMTP_USERNAME"]
        if username:
            smtp.login(username, current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)
    return True
