"""Add trials and subscription state to businesses."""
from datetime import datetime, timedelta
from alembic import op
import sqlalchemy as sa

revision="0002_subscriptions"; down_revision="0001_initial"; branch_labels=None; depends_on=None

def upgrade():
 op.add_column("business",sa.Column("subscription_plan",sa.String(20),nullable=False,server_default="starter")); op.add_column("business",sa.Column("subscription_status",sa.String(20),nullable=False,server_default="trialing")); op.add_column("business",sa.Column("trial_started_at",sa.DateTime(),nullable=True)); op.add_column("business",sa.Column("trial_ends_at",sa.DateTime(),nullable=True)); op.add_column("business",sa.Column("subscription_ends_at",sa.DateTime(),nullable=True))
 business=sa.table("business",sa.column("trial_started_at",sa.DateTime()),sa.column("trial_ends_at",sa.DateTime())); started=datetime.utcnow(); op.execute(business.update().values(trial_started_at=started,trial_ends_at=started+timedelta(days=14)))
 with op.batch_alter_table("business") as batch: batch.alter_column("trial_started_at",nullable=False); batch.alter_column("trial_ends_at",nullable=False)

def downgrade():
 with op.batch_alter_table("business") as batch: batch.drop_column("subscription_ends_at"); batch.drop_column("trial_ends_at"); batch.drop_column("trial_started_at"); batch.drop_column("subscription_status"); batch.drop_column("subscription_plan")
