"""Preserve existing sales as one-item transactions and record stock history."""
from alembic import op
import sqlalchemy as sa

revision = "0007_business_activity"
down_revision = "0006_restock"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("product", sa.Column("unit", sa.String(30), nullable=False, server_default="unit"))
    op.add_column("product", sa.Column("description", sa.String(500)))
    op.add_column("product", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("product", sa.Column("opening_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("expense", sa.Column("category", sa.String(80), nullable=False, server_default="Miscellaneous"))
    op.add_column("expense", sa.Column("note", sa.String(500)))
    op.add_column("expense", sa.Column("voided_at", sa.DateTime()))
    op.add_column("expense", sa.Column("void_reason", sa.String(300)))
    op.add_column("restock", sa.Column("note", sa.String(500)))
    op.add_column("restock", sa.Column("batch_id", sa.String(36)))
    op.create_index("ix_restock_batch_id", "restock", ["batch_id"])
    with op.batch_alter_table("sale") as batch:
        batch.alter_column("product_id", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("quantity", existing_type=sa.Integer(), nullable=True)
        batch.alter_column("unit_price", existing_type=sa.Numeric(12, 2), nullable=True)
        batch.alter_column("unit_cost", existing_type=sa.Numeric(12, 2), nullable=True)
        batch.add_column(sa.Column("payment_method", sa.String(20), nullable=False, server_default="Other"))
        batch.add_column(sa.Column("note", sa.String(500)))
        batch.add_column(sa.Column("voided_at", sa.DateTime()))
        batch.add_column(sa.Column("void_reason", sa.String(300)))
    op.create_table("sale_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sale.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12,2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12,2), nullable=False))
    op.create_index("ix_sale_item_sale_id", "sale_item", ["sale_id"])
    op.create_index("ix_sale_item_product_id", "sale_item", ["product_id"])
    op.create_table("stock_movement",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("business.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("quantity_change", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column("note", sa.String(500)),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sale.id")),
        sa.Column("restock_id", sa.Integer(), sa.ForeignKey("restock.id")),
        sa.Column("occurred_at", sa.DateTime(), nullable=False))
    op.create_index("ix_stock_movement_business_id", "stock_movement", ["business_id"])
    op.create_index("ix_stock_movement_product_id", "stock_movement", ["product_id"])
    op.create_index("ix_stock_movement_occurred_at", "stock_movement", ["occurred_at"])
    connection = op.get_bind()
    # Legacy rows represent one sale line each. Preserve their IDs, prices and dates.
    connection.execute(sa.text("""
        INSERT INTO sale_item (sale_id, product_id, quantity, unit_price, unit_cost)
        SELECT id, product_id, quantity, unit_price, unit_cost FROM sale
    """))
    # Infer opening balance from the transactions retained in this database. If
    # historic stock was edited manually, a reconciliation movement explains it.
    products = connection.execute(sa.text("SELECT id, business_id, stock_quantity, created_at FROM product")).mappings().all()
    for p in products:
        sold = connection.execute(sa.text("SELECT COALESCE(SUM(quantity),0) FROM sale WHERE product_id=:pid"), {"pid": p["id"]}).scalar()
        received = connection.execute(sa.text("SELECT COALESCE(SUM(quantity),0) FROM restock WHERE product_id=:pid"), {"pid": p["id"]}).scalar()
        inferred = p["stock_quantity"] + sold - received
        opening = max(0, inferred)
        connection.execute(sa.text("UPDATE product SET opening_quantity=:qty WHERE id=:pid"), {"qty": opening, "pid": p["id"]})
        connection.execute(sa.text("""
            INSERT INTO stock_movement (business_id,product_id,kind,quantity_change,reason,occurred_at)
            VALUES (:bid,:pid,'opening',:qty,'Opening balance inferred from existing records',:at)
        """), {"bid": p["business_id"], "pid": p["id"], "qty": opening, "at": p["created_at"]})
        if inferred < 0:
            connection.execute(sa.text("""
                INSERT INTO stock_movement (business_id,product_id,kind,quantity_change,reason,occurred_at)
                VALUES (:bid,:pid,'adjustment',:qty,'Historical balance reconciliation',:at)
            """), {"bid": p["business_id"], "pid": p["id"], "qty": inferred, "at": p["created_at"]})
    connection.execute(sa.text("""
        INSERT INTO stock_movement (business_id,product_id,kind,quantity_change,sale_id,occurred_at)
        SELECT business_id,product_id,'sale',-quantity,id,sold_at FROM sale
    """))
    connection.execute(sa.text("""
        INSERT INTO stock_movement (business_id,product_id,kind,quantity_change,restock_id,occurred_at)
        SELECT business_id,product_id,'restock',quantity,id,received_at FROM restock
    """))

def downgrade():
    # Multi-item transactions and adjustments cannot be represented in the old
    # schema without losing information. Keep rollback explicitly unavailable.
    raise RuntimeError("Business activity migration cannot be downgraded without losing transaction history.")
