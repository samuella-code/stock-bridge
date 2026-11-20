"""Disabled-by-default admin alert preferences and durable reminder claims."""
from alembic import op
import sqlalchemy as sa

revision = '0022_admin_reminders'
down_revision = '0021_admin_weekly_reports'
branch_labels = None
depends_on = None


def owner_id(name='user_id', primary=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE' if name=='user_id' else None), primary_key=primary, nullable=False)


def upgrade():
    op.create_table('admin_alert_preference', owner_id(primary=True),
        *[sa.Column(k, sa.Boolean(), nullable=False) for k in ('failed_payments','security_alerts','daily_summary')],
        sa.Column('version', sa.Integer(), nullable=False), sa.Column('auth_version', sa.Integer(), nullable=False),
        sa.Column('mfa_verified', sa.Boolean(), nullable=False))
    op.create_table('admin_reminder_schedule', sa.Column('key', sa.String(40), primary_key=True),
        sa.Column('enabled', sa.Boolean(), nullable=False), owner_id('actor_id'),
        sa.Column('auth_version', sa.Integer(), nullable=False), sa.Column('mfa_verified', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False))
    op.create_table('customer_reminder_preference', owner_id(primary=True),
        sa.Column('stopped_at', sa.DateTime()), sa.Column('last_contact_at', sa.DateTime()))
    op.create_table('admin_reminder_batch', sa.Column('id', sa.Integer(), primary_key=True), owner_id('actor_id'),
        sa.Column('request_key', sa.String(100), nullable=False), sa.Column('content_digest', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('actor_id','request_key',name='uq_reminder_batch_request'))
    op.create_table('admin_reminder_delivery', sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('admin_reminder_batch.id'), nullable=False), owner_id(),
        sa.Column('kind', sa.String(30), nullable=False), sa.Column('is_test', sa.Boolean(), nullable=False),
        sa.Column('is_automatic', sa.Boolean(), nullable=False), sa.Column('schedule_key',sa.String(40)),
        sa.Column('recipient_digest', sa.String(64), nullable=False), sa.Column('body', sa.Text(), nullable=False),
        sa.Column('email_status', sa.String(16), nullable=False),
        sa.Column('override_reason', sa.String(300), nullable=False), sa.Column('include_feedback', sa.Boolean(), nullable=False),
        sa.Column('period_end', sa.DateTime()), sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('completed_at', sa.DateTime()),
        sa.UniqueConstraint('batch_id','user_id',name='uq_reminder_batch_recipient'),
        sa.CheckConstraint("email_status IN ('unused','sending','sent','failed','unknown','suppressed')",name='ck_reminder_email_status'))
    op.create_index('ix_admin_reminder_delivery_user_id','admin_reminder_delivery',['user_id'])
    op.create_table('admin_reminder_feedback',
        sa.Column('delivery_id', sa.Integer(), sa.ForeignKey('admin_reminder_delivery.id',ondelete='CASCADE'), primary_key=True),
        sa.Column('reason', sa.String(30), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False))


def downgrade():
    for table in ('admin_reminder_feedback','admin_reminder_delivery','admin_reminder_batch',
                  'customer_reminder_preference','admin_reminder_schedule','admin_alert_preference'):
        op.drop_table(table)
