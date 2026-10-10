"""Add transaction replay receipts without changing historical business records."""
from alembic import op
import sqlalchemy as sa
revision = '0017_transaction_submission'
down_revision = '0016_inventory_email_outbox'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('transaction_submission',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('business_id', sa.Integer(), sa.ForeignKey('business.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('operation', sa.String(32), nullable=False),
        sa.Column('key', sa.String(64), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('result_reference', sa.String(120)),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('completed_at', sa.DateTime()),
        sa.UniqueConstraint('business_id', 'user_id', 'operation', 'key', name='uq_transaction_submission_scope'))


def downgrade():
    raise RuntimeError('Submission receipts must be retained to prevent replay; destructive downgrade unavailable.')
