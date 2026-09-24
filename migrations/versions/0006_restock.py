"""Record received stock."""
from alembic import op
import sqlalchemy as sa

revision = "0006_restock"
down_revision = "0005_email_verification"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("restock",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("business_id",sa.Integer(),sa.ForeignKey("business.id"),nullable=False),
        sa.Column("product_id",sa.Integer(),sa.ForeignKey("product.id"),nullable=False),
        sa.Column("quantity",sa.Integer(),nullable=False),
        sa.Column("unit_cost",sa.Numeric(12,2),nullable=False),
        sa.Column("supplier",sa.String(140)),
        sa.Column("received_at",sa.DateTime(),nullable=False))
    op.create_index("ix_restock_business_id","restock",["business_id"])

def downgrade():
    op.drop_index("ix_restock_business_id",table_name="restock")
    op.drop_table("restock")
