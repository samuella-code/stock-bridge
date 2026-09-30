"""Optional business-scoped barcodes and catalogue query indexes."""
from alembic import op
import sqlalchemy as sa

revision = "0012_product_catalogue"
down_revision = "0011_payment_receipt_email"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("product") as batch:
        batch.add_column(sa.Column("barcode", sa.String(80), nullable=True))
        batch.create_unique_constraint("uq_product_business_barcode", ["business_id", "barcode"])
        batch.create_index("ix_product_business_active_name", ["business_id", "active", "name"])
        batch.create_index("ix_product_business_active_category", ["business_id", "active", "category"])

def downgrade():
    with op.batch_alter_table("product") as batch:
        batch.drop_index("ix_product_business_active_category")
        batch.drop_index("ix_product_business_active_name")
        batch.drop_constraint("uq_product_business_barcode", type_="unique")
        batch.drop_column("barcode")
