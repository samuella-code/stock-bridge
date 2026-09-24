"""Separate platform administrators from business customers."""
from alembic import op
import sqlalchemy as sa

revision="0008_admin_portal"
down_revision="0007_business_activity"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("user",sa.Column("role",sa.String(20),nullable=False,server_default="user"))
    op.add_column("user",sa.Column("suspended_at",sa.DateTime()))
    op.add_column("user",sa.Column("last_activity_at",sa.DateTime()))
    op.add_column("business",sa.Column("suspended_at",sa.DateTime()))
    op.create_table("audit_log",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("actor_id",sa.Integer(),sa.ForeignKey("user.id")),
        sa.Column("business_id",sa.Integer(),sa.ForeignKey("business.id")),
        sa.Column("action",sa.String(50),nullable=False),
        sa.Column("description",sa.String(300),nullable=False),
        sa.Column("created_at",sa.DateTime(),nullable=False))
    for column in ("actor_id","business_id","action","created_at"):
        op.create_index(f"ix_audit_log_{column}","audit_log",[column])
    op.create_table("admin_login_attempt",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("identifier",sa.String(64),nullable=False),
        sa.Column("attempted_at",sa.DateTime(),nullable=False))
    op.create_index("ix_admin_login_attempt_identifier","admin_login_attempt",["identifier"])
    op.create_index("ix_admin_login_attempt_attempted_at","admin_login_attempt",["attempted_at"])

def downgrade():
    op.drop_table("admin_login_attempt")
    op.drop_table("audit_log")
    op.drop_column("business","suspended_at")
    op.drop_column("user","last_activity_at")
    op.drop_column("user","suspended_at")
    op.drop_column("user","role")
