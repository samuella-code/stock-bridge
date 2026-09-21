"""Support payment before account creation and remove trial access."""
from alembic import op
import sqlalchemy as sa

revision = "0004_payment_first"
down_revision = "0003_payments"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("payment") as batch:
        batch.alter_column("business_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("customer_email", sa.String(180), nullable=True))
        batch.add_column(sa.Column("claim_token", sa.String(120), nullable=True))
        batch.create_index("ix_payment_customer_email", ["customer_email"])
        batch.create_index("ix_payment_claim_token", ["claim_token"], unique=True)
    op.execute("UPDATE payment SET customer_email = 'legacy-' || id || '@stockbridge.local' WHERE customer_email IS NULL")
    with op.batch_alter_table("payment") as batch:
        batch.alter_column("customer_email", existing_type=sa.String(180), nullable=False)
    op.execute("UPDATE business SET subscription_status = 'inactive' WHERE subscription_status = 'trialing'")


def downgrade():
    with op.batch_alter_table("payment") as batch:
        batch.drop_index("ix_payment_claim_token")
        batch.drop_index("ix_payment_customer_email")
        batch.drop_column("claim_token")
        batch.drop_column("customer_email")
        batch.alter_column("business_id", existing_type=sa.Integer(), nullable=False)
