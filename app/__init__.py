import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify
from flask_login import LoginManager
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

    for blueprint in (
        auth_bp, main_bp, products_bp, sales_bp,
        expenses_bp, restocking_bp, profile_bp,
    ):
        app.register_blueprint(blueprint)

    @app.get("/health")
    def health():
        try:
            db.session.execute(db.text("SELECT 1"))
            return jsonify(status="healthy"), 200
        except Exception:
            app.logger.exception("Health check failed")
            return jsonify(status="unhealthy"), 503

    if not app.testing:
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

    return app
