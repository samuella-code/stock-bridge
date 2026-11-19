"""Store per-admin weekly report consent and one-time delivery claims."""
from alembic import op
import sqlalchemy as sa

revision = '0021_admin_weekly_reports'
down_revision = '0020_admin_customer_tools'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('admin_report_preference',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('weekly_enabled', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('auth_version', sa.Integer(), nullable=False),
        sa.Column('mfa_verified', sa.Boolean(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False))
    op.create_table('admin_report_delivery',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('week_start', sa.Date(), nullable=False),
        sa.Column('recipient_digest', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime()),
        sa.UniqueConstraint('user_id', 'week_start', name='uq_admin_report_week'),
        sa.CheckConstraint("status IN ('sending','sent','failed','unknown','suppressed')", name='ck_admin_report_delivery_status'))


def downgrade():
    op.drop_table('admin_report_delivery')
    op.drop_table('admin_report_preference')
