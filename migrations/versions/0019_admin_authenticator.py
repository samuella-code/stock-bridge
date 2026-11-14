"""Store encrypted admin authenticator enrollment and challenge state."""
from alembic import op
import sqlalchemy as sa

revision = "0019_admin_authenticator"
down_revision = "0018_staff_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_authenticator",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("encrypted_secret", sa.LargeBinary(), nullable=False),
        sa.Column("enabled_at", sa.DateTime()),
        sa.Column("last_step", sa.BigInteger(), nullable=False),
        sa.Column("recovery_digests", sa.JSON(), nullable=False),
        sa.Column("challenge_digest", sa.String(64)),
        sa.Column("challenge_expires_at", sa.DateTime()),
    )


def downgrade():
    raise RuntimeError("Admin authentication history must be reviewed before removing enrollment data.")
