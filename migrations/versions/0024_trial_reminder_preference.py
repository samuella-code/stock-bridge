"""Add per-customer trial reminder opt-out."""
from alembic import op
import sqlalchemy as sa

revision = '0024_trial_reminder_preference'
down_revision = '0023_customer_email_summaries'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column(
            'trial_reminders_enabled', sa.Boolean(), nullable=False,
            server_default=sa.true()
        ))


def downgrade():
    with op.batch_alter_table('user') as batch:
        batch.drop_column('trial_reminders_enabled')
