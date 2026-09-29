from datetime import datetime

import pytest
from sqlalchemy import inspect

from app import create_app, db
from app.models import (AdminLoginAttempt, AdminPasswordReset, AuditLog, Business,
                        Expense, Payment, Product, Restock, Sale, SaleItem,
                        StockMovement, User)
from scripts.reset_customer_data import main, reset_customer_data


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path/'reset.db'}",
                      "SECRET_KEY": "test"})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def seed():
    customer = User(full_name="Customer", email="customer@example.com")
    customer.set_password("customer-password")
    admin = User(full_name="Admin", email="admin@example.com", role="admin")
    admin.set_password("admin-password")
    dual = User(full_name="Dual", email="dual@example.com", admin_enabled=True)
    dual.set_password("dual-password")
    db.session.add_all([customer, admin, dual]); db.session.flush()
    shop = Business(user_id=customer.id, name="Customer shop")
    admin_shop = Business(user_id=dual.id, name="Admin shop")
    db.session.add_all([shop, admin_shop]); db.session.flush()
    product = Product(business_id=shop.id, name="Coke", stock_quantity=5)
    db.session.add(product); db.session.flush()
    sale = Sale(business_id=shop.id, product_id=product.id, quantity=1,
                unit_price=350, unit_cost=250)
    restock = Restock(business_id=shop.id, product_id=product.id, quantity=1, unit_cost=250)
    db.session.add_all([sale, restock]); db.session.flush()
    db.session.add_all([
        SaleItem(sale_id=sale.id, product_id=product.id, quantity=1, unit_price=350, unit_cost=250),
        StockMovement(business_id=shop.id, product_id=product.id, kind="sale",
                      quantity_change=-1, sale_id=sale.id),
        Expense(business_id=shop.id, description="Transport", amount=100),
        Payment(business_id=shop.id, customer_email=customer.email, reference="customer-ref", amount_kobo=300000, status="success"),
        Payment(customer_email="CUSTOMER@example.com", reference="unclaimed-ref", amount_kobo=300000, status="pending"),
        Payment(customer_email="legacy@example.com", reference="orphan-success", amount_kobo=300000, status="success"),
        Payment(customer_email="other@example.com", reference="orphan-pending", amount_kobo=300000, status="pending"),
        Payment(business_id=admin_shop.id, customer_email=dual.email, reference="admin-ref", amount_kobo=300000, status="success"),
        Payment(business_id=admin_shop.id, customer_email="CUSTOMER@example.com", reference="admin-email-collision", amount_kobo=300000, status="success"),
        Payment(customer_email="DUAL@example.com", reference="admin-unlinked", amount_kobo=300000, status="pending"),
        AuditLog(actor_id=admin.id, business_id=shop.id, action="USER_SUSPENDED",
                 description="Customer action", target_type="user", target_id=customer.id),
        AuditLog(actor_id=admin.id, action="ADMIN_LOGIN_SUCCESS", description="Admin signed in"),
        AdminPasswordReset(user_id=customer.id, token_digest="customer-token", expires_at=datetime.utcnow()),
        AdminPasswordReset(user_id=admin.id, token_digest="admin-token", expires_at=datetime.utcnow()),
        AdminLoginAttempt(identifier="admin-test"),
    ])
    db.session.commit()


def test_dry_run_and_actual_reset_preserve_admins_and_schema(app):
    with app.app_context():
        seed()
        tables_before = set(inspect(db.engine).get_table_names())
        counts, admins, payments = reset_customer_data()
        assert admins == 2
        assert counts["user"] == 1 and counts["business"] == 1
        assert counts["payment"] == 4 and counts["sale_item"] == 1
        assert payments == {"customer_business": 1, "customer_email": 1, "orphan": 2,
                            "admin_business": 2, "admin_email": 1}
        assert User.query.count() == 3 and SaleItem.query.count() == 1
        removed, preserved, _ = reset_customer_data(execute=True)
        assert removed == counts and preserved == 2
        assert [u.email for u in User.query.order_by(User.id)] == ["admin@example.com", "dual@example.com"]
        assert Business.query.one().name == "Admin shop"
        assert {p.reference for p in Payment.query} == {"admin-ref", "admin-email-collision", "admin-unlinked"}
        assert {p.status for p in Payment.query} == {"success", "pending"}
        assert AuditLog.query.one().action == "ADMIN_LOGIN_SUCCESS"
        assert AdminPasswordReset.query.one().token_digest == "admin-token"
        assert AdminLoginAttempt.query.count() == 1
        assert all(model.query.count() == 0 for model in (Sale, SaleItem, StockMovement,
                   Restock, Expense, Product))
        assert set(inspect(db.engine).get_table_names()) == tables_before


def test_failed_reset_rolls_back_every_delete(app, monkeypatch):
    with app.app_context():
        seed()
        original = db.session.execute
        calls = 0

        def interrupted(statement, *args, **kwargs):
            nonlocal calls
            if statement.__class__.__name__ == "Delete":
                calls += 1
                if calls == 4:
                    raise RuntimeError("interrupted")
            return original(statement, *args, **kwargs)

        monkeypatch.setattr(db.session, "execute", interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            reset_customer_data(execute=True)
        assert User.query.count() == 3
        assert SaleItem.query.count() == 1
        assert AuditLog.query.count() == 2


def test_ambiguous_customer_admin_email_stops_before_any_deletion(app):
    with app.app_context():
        seed()
        db.session.add(Payment(business_id=Business.query.filter_by(name="Customer shop").one().id,
                               customer_email="ADMIN@example.com", reference="ambiguous", amount_kobo=300000))
        db.session.commit()
        with pytest.raises(RuntimeError, match="admin-email payment"):
            reset_customer_data(execute=True)
        assert User.query.count() == 3 and Payment.query.count() == 8


def test_casefolded_identity_collision_stops_before_any_deletion(app):
    with app.app_context():
        seed()
        db.session.add(User(full_name="Other admin", email="CUSTOMER@example.com", role="admin",
                            password_hash="unused"))
        db.session.commit()
        with pytest.raises(RuntimeError, match="share an email"):
            reset_customer_data(execute=True)
        assert User.query.count() == 4 and Payment.query.count() == 7


def test_cli_dry_run_reports_payment_groups_without_deleting(app, monkeypatch, capsys):
    with app.app_context():
        seed()
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test-only.db")
    monkeypatch.setattr("scripts.reset_customer_data.create_app", lambda: app)
    monkeypatch.setattr("sys.argv", ["reset_customer_data"])
    main()
    output = capsys.readouterr().out
    assert "Linked to deleted customer businesses: 1" in output
    assert "Unlinked, matched by deleted customer email: 1" in output
    assert "Unlinked/orphaned, no admin email match: 2" in output
    assert "Protected: admin-owned business: 2" in output
    assert "Protected: unlinked admin email: 1" in output
    assert "Total unlinked/orphaned: 4" in output
    assert "DRY RUN: no records changed" in output
    with app.app_context():
        assert Payment.query.count() == 7 and User.query.count() == 3
