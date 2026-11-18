"""Store private admin notes and durable customer-message claims."""
from alembic import op
import sqlalchemy as sa

revision = '0020_admin_customer_tools'
down_revision = '0019_admin_authenticator'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admin_customer_note',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'admin_customer_message',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_key', sa.String(64), nullable=False),
        sa.Column('content_digest', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime()),
        sa.UniqueConstraint('actor_id', 'request_key', name='uq_admin_message_request'),
        sa.CheckConstraint("status IN ('sending','sent','failed','unknown')", name='ck_admin_message_status'),
    )


def downgrade():
    op.drop_table('admin_customer_message')
    op.drop_table('admin_customer_note')
