"""Create an admin through a trusted terminal with the target DATABASE_URL set."""
import getpass
from datetime import datetime
from app import create_app, db
from app.models import User

def main():
    app=create_app()
    with app.app_context():
        email=input("New admin email: ").strip().lower()
        name=input("Admin full name: ").strip()
        if not email or "@" not in email or not name:
            raise SystemExit("A name and valid email are required.")
        if User.query.filter_by(email=email).first():
            raise SystemExit("That email already belongs to an account. Choose a separate admin email; existing business accounts are not promoted.")
        password=getpass.getpass("Admin password (at least 12 characters): ")
        confirmation=getpass.getpass("Confirm password: ")
        if len(password)<12 or password!=confirmation:
            raise SystemExit("Passwords must match and contain at least 12 characters.")
        admin=User(full_name=name,email=email,role="admin",email_verified_at=datetime.utcnow())
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        print("Admin created. Sign in at /admin/login.")

if __name__=="__main__":
    main()
