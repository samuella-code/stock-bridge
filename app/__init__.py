import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import generate_csrf

from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"

def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)
    if app.testing:
        app.config["WTF_CSRF_ENABLED"] = False

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    app.jinja_env.globals["csrf_token"] = generate_csrf

    from app.auth.routes import auth_bp
    from app.main.routes import main_bp
    from app.products.routes import products_bp
    from app.sales.routes import sales_bp
    from app.expenses.routes import expenses_bp
    from app.restocking.routes import restocking_bp
    from app.profile.routes import profile_bp
    from app.subscriptions.routes import subscriptions_bp
    from app.payments.routes import payments_bp
    from app.admin.routes import admin_bp, admin_api_bp

    for blueprint in (
        auth_bp, main_bp, products_bp, sales_bp,
        expenses_bp, restocking_bp, profile_bp, subscriptions_bp, payments_bp, admin_bp, admin_api_bp,
    ):
        app.register_blueprint(blueprint)

    @app.before_request
    def require_lifetime_access():
        host = request.host.split(":", 1)[0].lower()
        customer_host = app.config["CUSTOMER_HOST"]
        admin_host = app.config["ADMIN_HOST"]
        if host == customer_host and request.blueprint in {"admin", "admin_api"}:
            if request.method == "GET" and request.blueprint == "admin":
                return redirect(f"https://{admin_host}{request.full_path.rstrip('?')}")
            abort(404)
        if host == admin_host and request.endpoint not in {"static", "health"} and request.blueprint not in {"admin", "admin_api"}:
            if request.method == "GET":
                if request.endpoint == "main.index":
                    return redirect(url_for("admin.index"))
                return redirect(f"https://{customer_host}{request.full_path.rstrip('?')}")
            abort(404)
        # Old admin cookies on the customer host should not hide customer login.
        if host == customer_host and current_user.is_authenticated and session.get("admin_session"):
            logout_user()
            session.clear()
            return redirect(url_for("auth.login"))
        if current_user.is_authenticated:
            if current_user.role == "admin" or (current_user.admin_enabled and session.get("admin_session")):
                if request.endpoint in {"main.index", "auth.login"}:
                    return redirect(url_for("admin.index" if session.get("admin_session") else "admin.login"))
                if request.blueprint not in {"admin", "admin_api"} and request.endpoint not in {"health", "static", "auth.logout"}:
                    abort(403)
            elif current_user.suspended_at or any(b.suspended_at for b in current_user.businesses):
                if request.endpoint not in {"auth.logout", "static"} and not (request.endpoint in {"admin.login", "admin.forgot_password", "admin.reset_password"} and current_user.admin_enabled and not current_user.suspended_at):
                    return render_template("admin/suspended.html"), 403
        verified_areas = {"main", "products", "sales", "expenses", "restocking", "profile"}
        paid_areas = {"products", "sales", "expenses", "restocking"}
        if current_user.is_authenticated and request.blueprint in verified_areas:
            if not current_user.email_verified_at:
                flash("Verify your email to access StockBridge.", "warning")
                return redirect(url_for("auth.verification_pending"))
        if current_user.is_authenticated and request.blueprint in paid_areas:
            business = current_user.businesses[0]
            db.session.refresh(business)
            if not business.has_write_access:
                flash("Unlock Products, Sales, Expenses and Restocking with the one-time ₦3,000 payment.", "warning")
                return redirect(url_for("subscriptions.index"))

    @app.context_processor
    def subscription_context():
        if not current_user.is_authenticated or not current_user.businesses:
            return {}
        return {"subscription_business": current_user.businesses[0]}

    @app.after_request
    def protect_admin_responses(response):
        if request.path.startswith("/admin/") or request.path.startswith("/api/admin/"):
            response.headers["Cache-Control"]="no-store, private"
            response.headers["X-Content-Type-Options"]="nosniff"
            # Flask-WTF checks the same-origin Referer on HTTPS form submissions.
            response.headers["Referrer-Policy"]="same-origin"
            response.headers["X-Frame-Options"]="DENY"
            response.headers["Content-Security-Policy"]=(
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
                "frame-ancestors 'none'; form-action 'self'")
        return response

    @app.get("/health")
    def health():
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify(status="healthy"), 200
        except Exception:
            app.logger.exception("Health check failed")
            return jsonify(status="unhealthy"), 503

    if not app.testing and not os.getenv("VERCEL"):
        os.makedirs(app.instance_path, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(app.instance_path, "stockbridge.log"),
            maxBytes=1_000_000,
            backupCount=5,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s"
        ))
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)
    elif not app.testing:
        # Vercel captures stdout/stderr in its runtime logs. Its function
        # filesystem must not be used for persistent application logs.
        app.logger.setLevel(logging.INFO)

    return app
