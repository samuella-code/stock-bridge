"""Durable inventory alerts; preserve historical preferences, notifications and logos."""
from alembic import op
import sqlalchemy as sa

revision = '0016_inventory_email_outbox'
down_revision = '0015_notifications_business_logo'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notification_preference') as batch:
        # Change defaults only. Existing explicit False values remain False.
        for field in ('low_stock_email', 'out_of_stock_email'):
            batch.alter_column(field, existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.true())
        for field in ('product_added', 'sales', 'restocking'):
            batch.add_column(sa.Column(field, sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table('email_outbox',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recipient_user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('recipient_email', sa.String(180), nullable=False),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('event_key', sa.String(180), nullable=False),
        sa.Column('subject', sa.String(240), nullable=False),
        sa.Column('template_name', sa.String(40), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime()),
        sa.Column('claim_token', sa.String(32)),
        sa.Column('claim_expires_at', sa.DateTime()),
        sa.Column('sent_at', sa.DateTime()),
        sa.Column('last_error', sa.String(80)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('event_key', name='uq_email_outbox_event_key'),
        sa.CheckConstraint("status IN ('pending','processing','sent','retry','failed','suppressed')", name='ck_email_outbox_status'),
        sa.CheckConstraint('attempt_count >= 0 AND max_attempts > 0', name='ck_email_outbox_attempts'))
    op.create_index('ix_email_outbox_due', 'email_outbox', ['status', 'next_attempt_at', 'claim_expires_at'])


def downgrade():
    op.drop_table('email_outbox')
    with op.batch_alter_table('notification_preference') as batch:
        for field in ('product_added', 'sales', 'restocking'):
            batch.drop_column(field)
        for field in ('low_stock_email', 'out_of_stock_email'):
            batch.alter_column(field, existing_type=sa.Boolean(), existing_nullable=False, server_default=sa.false())
