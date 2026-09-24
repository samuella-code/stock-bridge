"""Allow an existing business owner to be granted administrator access."""
from alembic import op
import sqlalchemy as sa

revision = "0009_dual_role_admins"
down_revision = "0008_admin_portal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user", sa.Column("admin_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column("user", "admin_enabled")
