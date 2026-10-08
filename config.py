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
 CUSTOMER_HOST=os.getenv("CUSTOMER_HOST","stock-bridge-one.vercel.app")
 ADMIN_HOST=os.getenv("ADMIN_HOST","stock-bridge-admin.vercel.app")
 SECRET_KEY=os.getenv("SECRET_KEY","dev-only-change-me")
 SQLALCHEMY_DATABASE_URI=database_url()
 SQLALCHEMY_TRACK_MODIFICATIONS=False
 SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True}
 SESSION_COOKIE_HTTPONLY=True
 SESSION_COOKIE_SAMESITE="Lax"
 SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV")=="production" or bool(os.getenv("VERCEL"))
 WTF_CSRF_TIME_LIMIT=3600
 MAX_CONTENT_LENGTH=2*1024*1024
 BUSINESS_LOGO_MAX_BYTES=5*1024*1024
 BUSINESS_LOGO_REQUEST_MAX_BYTES=6*1024*1024
 BUSINESS_IMAGE_STORAGE=os.getenv('BUSINESS_IMAGE_STORAGE', 'disabled')
 BUSINESS_IMAGE_LOCAL_DIR=os.getenv('BUSINESS_IMAGE_LOCAL_DIR', '')
 BUSINESS_IMAGE_S3_BUCKET=os.getenv('BUSINESS_IMAGE_S3_BUCKET', '')
 BUSINESS_IMAGE_S3_REGION=os.getenv('BUSINESS_IMAGE_S3_REGION', 'us-east-1')
 BUSINESS_IMAGE_S3_ENDPOINT=os.getenv('BUSINESS_IMAGE_S3_ENDPOINT', '')
 BUSINESS_IMAGE_S3_ACCESS_KEY_ID=os.getenv('BUSINESS_IMAGE_S3_ACCESS_KEY_ID', '')
 BUSINESS_IMAGE_S3_SECRET_ACCESS_KEY=os.getenv('BUSINESS_IMAGE_S3_SECRET_ACCESS_KEY', '')
 ADMIN_IDLE_TIMEOUT_SECONDS=1800
 PAYSTACK_SECRET_KEY=os.getenv("PAYSTACK_SECRET_KEY","")
 PAYSTACK_PUBLIC_KEY=os.getenv("PAYSTACK_PUBLIC_KEY","")
 LIFETIME_PRICE_NAIRA=int(os.getenv("LIFETIME_PRICE_NAIRA","3000"))
 ADMIN_EMAILS=os.getenv("ADMIN_EMAILS","")
 SMTP_HOST=os.getenv("SMTP_HOST","")
 SMTP_PORT=int(os.getenv("SMTP_PORT","587"))
 SMTP_USERNAME=os.getenv("SMTP_USERNAME","")
 SMTP_PASSWORD=os.getenv("SMTP_PASSWORD","")
 SMTP_FROM_EMAIL=os.getenv("SMTP_FROM_EMAIL") or os.getenv("SMTP_USERNAME","")
 SMTP_FROM_NAME=os.getenv("SMTP_FROM_NAME","StockBridge")
 SMTP_USE_TLS=os.getenv("SMTP_USE_TLS","true").lower()=="true"

 # Off until billing migration/configuration has been reviewed and approved.
 SUBSCRIPTIONS_ENABLED=os.getenv("SUBSCRIPTIONS_ENABLED","false").lower()=="true"
 BILLING_PROVIDER_ENABLED=os.getenv("BILLING_PROVIDER_ENABLED","false").lower()=="true"
 PAYSTACK_BASIC_MONTHLY_PLAN=os.getenv("PAYSTACK_BASIC_MONTHLY_PLAN","")
 PAYSTACK_BASIC_YEARLY_PLAN=os.getenv("PAYSTACK_BASIC_YEARLY_PLAN","")
 PAYSTACK_PLUS_MONTHLY_PLAN=os.getenv("PAYSTACK_PLUS_MONTHLY_PLAN","")
 PAYSTACK_PLUS_YEARLY_PLAN=os.getenv("PAYSTACK_PLUS_YEARLY_PLAN","")

 # Separate credentials and exact HTTPS redirects per environment; disabled when missing.
 GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID", "")
 GOOGLE_CLIENT_SECRET=os.getenv("GOOGLE_CLIENT_SECRET", "")
 GOOGLE_REDIRECT_URI=os.getenv("GOOGLE_REDIRECT_URI", "")
 APPLE_CLIENT_ID=os.getenv("APPLE_CLIENT_ID", "")
 APPLE_TEAM_ID=os.getenv("APPLE_TEAM_ID", "")
 APPLE_KEY_ID=os.getenv("APPLE_KEY_ID", "")
 APPLE_PRIVATE_KEY=os.getenv("APPLE_PRIVATE_KEY", "")
 APPLE_REDIRECT_URI=os.getenv("APPLE_REDIRECT_URI", "")

 # Inventory email dispatch is deliberately off until environment-specific setup.
 INVENTORY_EMAILS_ENABLED=os.getenv('INVENTORY_EMAILS_ENABLED', 'false').lower()=='true'
 INVENTORY_EMAIL_BASE_URL=os.getenv('INVENTORY_EMAIL_BASE_URL', '')
 INVENTORY_EMAIL_DISPATCH_SECRET=os.getenv('INVENTORY_EMAIL_DISPATCH_SECRET', '')
 INVENTORY_EMAIL_MAX_ATTEMPTS=int(os.getenv('INVENTORY_EMAIL_MAX_ATTEMPTS', '5'))
 INVENTORY_EMAIL_RETRY_SECONDS=int(os.getenv('INVENTORY_EMAIL_RETRY_SECONDS', '60'))
 INVENTORY_EMAIL_LEASE_SECONDS=300

 INVENTORY_EMAIL_TRIGGER_URL=os.getenv('INVENTORY_EMAIL_TRIGGER_URL', '')
 INVENTORY_EMAIL_TRIGGER_SECRET=os.getenv('INVENTORY_EMAIL_TRIGGER_SECRET', '')
