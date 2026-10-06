"""Add stable social identities without modifying users or billing history."""
from alembic import op
import sqlalchemy as sa
revision = '0014_social_identity'
down_revision = '0013_account_billing'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('social_identity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('provider', sa.String(16), nullable=False),
        sa.Column('provider_subject', sa.String(255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('provider', 'provider_subject', name='uq_social_provider_subject'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_social_user_provider'),
        sa.CheckConstraint("provider IN ('google', 'apple')", name='ck_social_provider'))
    op.create_index('ix_social_identity_user_id', 'social_identity', ['user_id'])


def downgrade():
    # Only identities introduced here are removed; user/password/business/billing stay intact.
    op.drop_index('ix_social_identity_user_id', table_name='social_identity')
    op.drop_table('social_identity')
