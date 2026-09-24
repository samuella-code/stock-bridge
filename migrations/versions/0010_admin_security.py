"""Single-use admin password reset and safe audit metadata."""
from alembic import op
import sqlalchemy as sa

revision = "0010_admin_security"
down_revision = "0009_dual_role_admins"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user",sa.Column("admin_auth_version",sa.Integer(),nullable=False,server_default="0"))
    op.add_column("audit_log",sa.Column("target_type",sa.String(40)))
    op.add_column("audit_log",sa.Column("target_id",sa.Integer()))
    op.add_column("audit_log",sa.Column("ip_address",sa.String(45)))
    op.create_table("admin_password_reset",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("user_id",sa.Integer(),sa.ForeignKey("user.id"),nullable=False),
        sa.Column("token_digest",sa.String(64),nullable=False,unique=True),
        sa.Column("expires_at",sa.DateTime(),nullable=False),
        sa.Column("used_at",sa.DateTime()),
        sa.Column("created_at",sa.DateTime(),nullable=False))
    op.create_index("ix_admin_password_reset_user_id","admin_password_reset",["user_id"])

def downgrade():
    op.drop_table("admin_password_reset")
    op.drop_column("audit_log","ip_address")
    op.drop_column("audit_log","target_id")
    op.drop_column("audit_log","target_type")
    op.drop_column("user","admin_auth_version")
