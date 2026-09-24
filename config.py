import os
from dotenv import load_dotenv

load_dotenv()


def database_url():
    """Return a SQLAlchemy URL that uses the installed psycopg v3 driver."""
    # Vercel's Neon integration uses prefixed connection variables, allowing
    # it to coexist with older hosting settings that define DATABASE_URL.
    url = (
        os.getenv("NEON_DATABASE_URL")
        or os.getenv("NEON_POSTGRES_URL")
        or os.getenv("DATABASE_URL")
        or "sqlite:///stockbridge.db"
    )
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class Config:
 SECRET_KEY=os.getenv("SECRET_KEY","dev-only-change-me")
 SQLALCHEMY_DATABASE_URI=database_url()
 SQLALCHEMY_TRACK_MODIFICATIONS=False
 SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True}
 SESSION_COOKIE_HTTPONLY=True
 SESSION_COOKIE_SAMESITE="Lax"
 SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV")=="production" or bool(os.getenv("VERCEL"))
 WTF_CSRF_TIME_LIMIT=3600
 MAX_CONTENT_LENGTH=2*1024*1024
 ADMIN_IDLE_TIMEOUT_SECONDS=1800
 PAYSTACK_SECRET_KEY=os.getenv("PAYSTACK_SECRET_KEY","")
 PAYSTACK_PUBLIC_KEY=os.getenv("PAYSTACK_PUBLIC_KEY","")
 LIFETIME_PRICE_NAIRA=int(os.getenv("LIFETIME_PRICE_NAIRA","3000"))
 ADMIN_EMAILS=os.getenv("ADMIN_EMAILS","")
 SMTP_HOST=os.getenv("SMTP_HOST","")
 SMTP_PORT=int(os.getenv("SMTP_PORT","587"))
 SMTP_USERNAME=os.getenv("SMTP_USERNAME","")
 SMTP_PASSWORD=os.getenv("SMTP_PASSWORD","")
 SMTP_FROM_EMAIL=os.getenv("SMTP_FROM_EMAIL","noreply@stockbridge.ng")
 SMTP_USE_TLS=os.getenv("SMTP_USE_TLS","true").lower()=="true"
