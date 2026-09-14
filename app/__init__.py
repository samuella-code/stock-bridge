from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from config import Config
db=SQLAlchemy(); login_manager=LoginManager(); login_manager.login_view="auth.login"; login_manager.login_message_category="warning"
def create_app(test_config=None):
 app=Flask(__name__,instance_relative_config=True); app.config.from_object(Config)
 if test_config: app.config.update(test_config)
 db.init_app(app); login_manager.init_app(app)
 from app.auth.routes import auth_bp
 from app.main.routes import main_bp
 from app.products.routes import products_bp
 from app.sales.routes import sales_bp
 from app.expenses.routes import expenses_bp
 from app.restocking.routes import restocking_bp
 from app.profile.routes import profile_bp
 for bp in (auth_bp,main_bp,products_bp,sales_bp,expenses_bp,restocking_bp,profile_bp): app.register_blueprint(bp)
 with app.app_context(): db.create_all()
 return app
