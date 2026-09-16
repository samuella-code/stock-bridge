import os
class Config:
 SECRET_KEY=os.getenv("SECRET_KEY","dev-only-change-me")
 SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL","sqlite:///stockbridge.db").replace("postgres://","postgresql+psycopg://",1)
 SQLALCHEMY_TRACK_MODIFICATIONS=False
 SESSION_COOKIE_HTTPONLY=True
 SESSION_COOKIE_SAMESITE="Lax"
 SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV")=="production"
 WTF_CSRF_TIME_LIMIT=3600
 MAX_CONTENT_LENGTH=2*1024*1024
