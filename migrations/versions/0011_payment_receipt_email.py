"""Track sent Lifetime Access receipts without altering historical payments."""
from alembic import op
import sqlalchemy as sa

revision = "0011_payment_receipt_email"
down_revision = "0010_admin_security"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payment", sa.Column("receipt_email_claimed_at", sa.DateTime()))
    op.add_column("payment", sa.Column("receipt_email_sent_at", sa.DateTime()))


def downgrade():
    op.drop_column("payment", "receipt_email_sent_at")
    op.drop_column("payment", "receipt_email_claimed_at")
