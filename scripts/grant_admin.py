"""Grant an existing business account admin access without changing its data."""
from app import create_app, db
from app.models import User
from app.admin.routes import log


def main():
    app = create_app()
    with app.app_context():
        email = input("Existing account email: ").strip().lower()
        user = User.query.filter_by(email=email, role="user").first()
        if not user:
            raise SystemExit("No existing business account has that email.")
        if user.admin_enabled:
            raise SystemExit("This account already has admin access.")
        print(f"Grant admin access to {user.full_name} ({user.email})?")
        if input("Type GRANT ADMIN to confirm: ").strip() != "GRANT ADMIN":
            raise SystemExit("No changes made.")
        user.admin_enabled = True
        log("ADMIN_ACCESS_GRANTED", f"Administrator access granted to account {user.id}.", actor=user)
        db.session.commit()
        print("Admin access granted. Sign in at /admin/login with the existing password.")


if __name__ == "__main__":
    main()
