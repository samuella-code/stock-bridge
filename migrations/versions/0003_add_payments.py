"""Store one-time Paystack payments."""
from alembic import op
import sqlalchemy as sa

revision = "0003_payments"
down_revision = "0002_subscriptions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("payment", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("business_id", sa.Integer(), sa.ForeignKey("business.id"), nullable=False), sa.Column("reference", sa.String(100), nullable=False), sa.Column("provider", sa.String(30), nullable=False), sa.Column("product", sa.String(40), nullable=False), sa.Column("amount_kobo", sa.Integer(), nullable=False), sa.Column("currency", sa.String(3), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("paid_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("reference"))
    op.create_index("ix_payment_business_id", "payment", ["business_id"])
    op.create_index("ix_payment_reference", "payment", ["reference"], unique=True)
    op.create_index("ix_payment_status", "payment", ["status"])


def downgrade():
    op.drop_table("payment")
