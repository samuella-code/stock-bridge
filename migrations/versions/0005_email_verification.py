"""Add user email verification state."""
from alembic import op
import sqlalchemy as sa

revision = "0005_email_verification"
down_revision = "0004_payment_first"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("email_verified_at", sa.DateTime(), nullable=True))
    op.add_column("user", sa.Column("verification_sent_at", sa.DateTime(), nullable=True))
    # Accounts created before email verification existed keep their current access.
    op.execute('UPDATE "user" SET email_verified_at = CURRENT_TIMESTAMP')


def downgrade():
    op.drop_column("user", "verification_sent_at")
    op.drop_column("user", "email_verified_at")
