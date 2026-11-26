"""Add opt-in customer summary preferences."""
from alembic import op
import sqlalchemy as sa

revision = '0023_customer_email_summaries'
down_revision = '0022_admin_reminders'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notification_preference') as batch:
        batch.add_column(sa.Column(
            'daily_summary_email', sa.Boolean(), nullable=False,
            server_default=sa.false()
        ))
        batch.add_column(sa.Column(
            'weekly_summary_email', sa.Boolean(), nullable=False,
            server_default=sa.false()
        ))


def downgrade():
    with op.batch_alter_table('notification_preference') as batch:
        batch.drop_column('weekly_summary_email')
        batch.drop_column('daily_summary_email')
