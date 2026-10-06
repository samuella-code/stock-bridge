import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, has_request_context, abort, flash, jsonify, redirect, render_template, request, session, url_for
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
    from app.auth.social import init_social
    init_social(app)
    from app.main.routes import main_bp
    from app.products.routes import products_bp
    from app.sales.routes import sales_bp
    from app.expenses.routes import expenses_bp
    from app.restocking.routes import restocking_bp
    from app.profile.routes import profile_bp
    from app.businesses.routes import businesses_bp
    from app.subscriptions.routes import subscriptions_bp
    from app.payments.routes import payments_bp
    from app.admin.routes import admin_bp, admin_api_bp

    for blueprint in (
        auth_bp, main_bp, products_bp, sales_bp,
        expenses_bp, restocking_bp, profile_bp, businesses_bp, subscriptions_bp, payments_bp, admin_bp, admin_api_bp,
    ):
        app.register_blueprint(blueprint)

    from runtime_diagnostics import register_request_diagnostics
    register_request_diagnostics(app)

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
        verified_areas = {"main", "products", "sales", "expenses", "restocking", "profile", "businesses"}
        if app.config.get("SUBSCRIPTIONS_ENABLED"):
            verified_areas.add("subscriptions")
        paid_areas = {"products", "sales", "expenses", "restocking"}
        if current_user.is_authenticated and (request.blueprint in verified_areas or request.endpoint in {"payments.checkout", "payments.initialize"}):
            if not current_user.email_verified_at:
                flash("Verify your email to access StockBridge.", "warning")
                return redirect(url_for("auth.verification_pending"))
        if app.config.get("SUBSCRIPTIONS_ENABLED") and current_user.is_authenticated and current_user.email_verified_at:
            from app.subscriptions.entitlements import start_trial
            if start_trial(current_user):
                db.session.commit()
        business_areas = {'products', 'sales', 'expenses', 'restocking', 'profile'}
        if current_user.is_authenticated and (request.blueprint in business_areas or request.endpoint in {'main.dashboard', 'main.reports'}):
            from app.subscriptions.entitlements import selected_business
            from app.businesses.service import selection_required, accessible_business
            business = selected_business(current_user)
            if not business or selection_required(current_user) or not accessible_business(current_user, business):
                return redirect(url_for('businesses.index'))
        if current_user.is_authenticated and request.blueprint in paid_areas:
            from app.subscriptions.entitlements import selected_business
            business = selected_business(current_user)
            db.session.refresh(business)
            if not business.has_write_access and (not app.config.get("SUBSCRIPTIONS_ENABLED") or request.method not in {"GET", "HEAD", "OPTIONS"}):
                flash("Your records remain available. Choose a plan to record new business activity." if app.config.get("SUBSCRIPTIONS_ENABLED") else "Unlock Products, Sales, Expenses and Restocking with the one-time ₦3,000 payment.", "warning")
                return redirect(url_for("subscriptions.index"))

    @app.context_processor
    def subscription_context():
        if not has_request_context() or not current_user.is_authenticated or not current_user.businesses:
            return {}
        from app.subscriptions.entitlements import selected_business
        business=selected_business(current_user)
        from app.businesses.service import owned_businesses, accessible_business, selection_required
        choices = owned_businesses(current_user)
        context = {"subscription_business": business, "business_choices": choices,
            "business_switch_ids": {b.id for b in choices if accessible_business(current_user, b)},
            "business_selection_required": selection_required(current_user)}
        if app.config.get("SUBSCRIPTIONS_ENABLED"):
            from app.subscriptions.entitlements import effective_access, access_label
            context.update(billing_access=effective_access(current_user, business=business), billing_label=access_label(current_user))
            if context['billing_access'].kind == 'trial':
                from app.subscriptions.entitlements import account_for
                context['billing_trial_end'] = account_for(current_user).trial_ends_at
            context["business_can_add"] = bool(effective_access(current_user).can_write and len(choices) < effective_access(current_user).business_limit)
        return context

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

    from app.subscriptions.notifications import register_commands
    register_commands(app)
    if app.config.get("SUBSCRIPTIONS_ENABLED"):
        from app.subscriptions.entitlements import access_label
        app.jinja_env.globals["billing_access_label"] = access_label
    return app
