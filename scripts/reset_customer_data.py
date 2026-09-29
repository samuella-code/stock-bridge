"""One-time, opt-in removal of ordinary customer accounts and their business data.

Run from a trusted shell with DATABASE_URL (or NEON_DATABASE_URL) set. The
default is a dry run; --execute also requires the confirmation phrase. Take a
database backup and stop signups/payments before using this on production.
"""

import argparse
import os

from sqlalchemy import and_, delete, func, inspect, or_, select

from app import create_app, db
from app.models import (AdminPasswordReset, AuditLog, Business, Expense, Payment,
                        Product, Restock, Sale, SaleItem, StockMovement, User)


def deletion_plan():
    """Build SQL predicates from the actual user/business relationship graph."""
    customers = select(User.id).where(User.role == "user", User.admin_enabled.is_(False))
    businesses = select(Business.id).where(Business.user_id.in_(customers))
    products = select(Product.id).where(Product.business_id.in_(businesses))
    sales = select(Sale.id).where(Sale.business_id.in_(businesses))
    customer_emails = select(func.lower(User.email)).where(User.id.in_(customers))
    return [
        ("audit_log", AuditLog, or_(AuditLog.actor_id.in_(customers),
            AuditLog.business_id.in_(businesses),
            and_(AuditLog.target_type == "user", AuditLog.target_id.in_(customers)),
            and_(AuditLog.target_type == "business", AuditLog.target_id.in_(businesses)))),
        ("admin_password_reset", AdminPasswordReset, AdminPasswordReset.user_id.in_(customers)),
        ("sale_item", SaleItem, SaleItem.sale_id.in_(sales)),
        ("stock_movement", StockMovement, StockMovement.business_id.in_(businesses)),
        ("restock", Restock, Restock.business_id.in_(businesses)),
        ("sale", Sale, Sale.business_id.in_(businesses)),
        ("expense", Expense, Expense.business_id.in_(businesses)),
        ("payment", Payment, or_(Payment.business_id.in_(businesses),
            func.lower(Payment.customer_email).in_(customer_emails))),
        ("product", Product, Product.business_id.in_(businesses)),
        ("business", Business, Business.user_id.in_(customers)),
        ("user", User, User.id.in_(customers)),
    ]


def check_schema():
    """Refuse an unknown relation that could be orphaned by a bulk delete."""
    inspector = inspect(db.engine)
    known = set(db.metadata.tables)
    found = set(inspector.get_table_names())
    if not known.issubset(found):
        raise RuntimeError("Database schema is incomplete; apply migrations first.")
    deleted = {model.__tablename__ for _, model, _ in deletion_plan()}
    for table in found - known:
        for fk in inspector.get_foreign_keys(table):
            if fk["referred_table"] in deleted:
                raise RuntimeError(f"Unknown table {table} references customer data; review its deletion order first.")
    # Cross-business references would either leave dangling foreign keys or
    # delete data belonging to an administrator. Stop for a manual review.
    customers = select(User.id).where(User.role == "user", User.admin_enabled.is_(False))
    businesses = select(Business.id).where(Business.user_id.in_(customers))
    products = select(Product.id).where(Product.business_id.in_(businesses))
    sales = select(Sale.id).where(Sale.business_id.in_(businesses))
    restocks = select(Restock.id).where(Restock.business_id.in_(businesses))
    checks = [
        select(Sale.id).where(Sale.product_id.in_(products), Sale.business_id.not_in(businesses)),
        select(SaleItem.id).where(SaleItem.product_id.in_(products), SaleItem.sale_id.not_in(sales)),
        select(Restock.id).where(Restock.product_id.in_(products), Restock.business_id.not_in(businesses)),
        select(StockMovement.id).where(StockMovement.product_id.in_(products), StockMovement.business_id.not_in(businesses)),
        select(StockMovement.id).where(StockMovement.sale_id.in_(sales), StockMovement.business_id.not_in(businesses)),
        select(StockMovement.id).where(StockMovement.restock_id.in_(restocks), StockMovement.business_id.not_in(businesses)),
    ]
    if any(db.session.execute(stmt.limit(1)).first() for stmt in checks):
        raise RuntimeError("Cross-business transaction references found; stop and review them before resetting.")
    if db.session.scalar(select(func.count()).select_from(User).where(User.role.not_in(("user", "admin")))):
        raise RuntimeError("Unknown user roles found; review admin identities before resetting.")


def reset_customer_data(*, execute=False):
    """Return planned or deleted counts; all writes commit together."""
    try:
        check_schema()
        plan = deletion_plan()
        counts = {name: db.session.scalar(select(func.count()).select_from(model).where(condition))
                  for name, model, condition in plan}
        admins = db.session.scalar(select(func.count()).select_from(User).where(
            or_(User.role == "admin", User.admin_enabled.is_(True))))
        if execute:
            for _, model, condition in plan:
                db.session.execute(delete(model).where(condition))
            db.session.commit()
        else:
            db.session.rollback()
        return counts, admins
    except Exception:
        db.session.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Delete after typed confirmation; default is dry-run")
    args = parser.parse_args()
    if not any(os.environ.get(key) for key in ("NEON_DATABASE_URL", "NEON_POSTGRES_URL", "DATABASE_URL")):
        raise SystemExit("Set the intended database URL in the environment first; refusing the default SQLite database.")
    app = create_app()
    with app.app_context():
        check_schema()
        counts, admins = reset_customer_data()
        print(f"Database: {db.engine.url.render_as_string(hide_password=True)}")
        print("Preserved: admin accounts, including dual-role administrators, their businesses, and schema/migrations.")
        print("Customer records to remove:")
        for name, count in counts.items():
            print(f"  {name}: {count}")
        print(f"Admin accounts preserved: {admins}")
        if not args.execute:
            print("DRY RUN: no records changed. Back up the database before executing.")
            return
        print("This permanently removes the customer records listed above. Stop new signups and payments first.")
        if input("Type RESET CUSTOMER DATA to confirm: ").strip() != "RESET CUSTOMER DATA":
            raise SystemExit("Cancelled; no records changed.")
        removed, preserved = reset_customer_data(execute=True)
        print("Customer records removed:")
        for name, count in removed.items():
            print(f"  {name}: {count}")
        print(f"Admin accounts preserved: {preserved}")


if __name__ == "__main__":
    main()
