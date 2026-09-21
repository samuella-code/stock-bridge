import os
from dotenv import load_dotenv

load_dotenv()
class Config:
 SECRET_KEY=os.getenv("SECRET_KEY","dev-only-change-me")
 SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL","sqlite:///stockbridge.db").replace("postgres://","postgresql+psycopg://",1)
 SQLALCHEMY_TRACK_MODIFICATIONS=False
 SESSION_COOKIE_HTTPONLY=True
 SESSION_COOKIE_SAMESITE="Lax"
 SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV")=="production"
 WTF_CSRF_TIME_LIMIT=3600
 MAX_CONTENT_LENGTH=2*1024*1024
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
