"""Business-scoped notifications and nullable logo references; no customer rewrite."""
from alembic import op
import sqlalchemy as sa

revision = '0015_notifications_business_logo'
down_revision = '0014_social_identity'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('business', sa.Column('logo_key', sa.String(200), nullable=True))
    op.create_table('notification',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(40), nullable=False),
        sa.Column('title', sa.String(140), nullable=False),
        sa.Column('body', sa.String(700), nullable=False),
        sa.Column('resource_id', sa.Integer()),
        sa.Column('event_key', sa.String(180), nullable=False),
        sa.Column('read_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('event_key', name='uq_notification_event_key'))
    op.create_index('ix_notification_owner_business_read', 'notification', ['user_id', 'business_id', 'read_at'])
    op.create_index('ix_notification_business_created', 'notification', ['business_id', 'created_at', 'id'])
    op.create_table('notification_preference',
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('low_stock_email', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('out_of_stock_email', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_table('notification_preference')
    op.drop_table('notification')
    op.drop_column('business', 'logo_key')
