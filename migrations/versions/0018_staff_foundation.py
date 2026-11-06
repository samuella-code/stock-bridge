"""Add staff identities, invitation digests, assignments and bounded grants."""
from alembic import op
import sqlalchemy as sa
revision = '0018_staff_foundation'
down_revision = '0017_transaction_submission'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user', sa.Column('staff_only', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table('staff_role',
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), primary_key=True),
        sa.Column('key', sa.String(32), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False))
    op.create_table('staff_membership',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('role', sa.String(32), nullable=False),
        sa.Column('duty_label', sa.String(100), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('grant_event_id', sa.Integer(), nullable=False),
        sa.Column('grant_started_at', sa.DateTime(), nullable=False),
        sa.Column('grant_pro_start', sa.DateTime(), nullable=False),
        sa.Column('grant_subscription_id', sa.Integer(), sa.ForeignKey('recurring_subscription.id'), nullable=False),
        sa.Column('access_until', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('revoked_at', sa.DateTime()),
        sa.UniqueConstraint('owner_id','user_id',name='uq_staff_owner_user'),
        sa.ForeignKeyConstraint(['owner_id','role'], ['staff_role.owner_id','staff_role.key'], name='fk_staff_member_owned_role'),
        sa.CheckConstraint("state IN ('active','revoked')",name='ck_staff_state'),
        sa.CheckConstraint('owner_id <> user_id',name='ck_staff_not_owner'))
    op.create_index('ix_staff_membership_owner_id','staff_membership',['owner_id'])
    op.create_index('ix_staff_membership_user_id','staff_membership',['user_id'])
    op.create_table('staff_invitation',
        sa.Column('delivery_status', sa.String(24), nullable=False, server_default='pending'),
        sa.Column('delivery_attempted_at', sa.DateTime()),
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('email', sa.String(180), nullable=False), sa.Column('role', sa.String(32), nullable=False),
        sa.Column('duty_label', sa.String(100), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False, unique=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('grant_event_id', sa.Integer(), nullable=False),
        sa.Column('grant_started_at', sa.DateTime(), nullable=False),
        sa.Column('grant_pro_start', sa.DateTime(), nullable=False),
        sa.Column('grant_subscription_id', sa.Integer(), sa.ForeignKey('recurring_subscription.id'), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False), sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime()), sa.Column('revoked_at', sa.DateTime()),
        sa.Column('membership_id', sa.Integer(), sa.ForeignKey('staff_membership.id')),
        sa.CheckConstraint("delivery_status IN ('pending','sending','sent','disabled','configuration_required','failed','unknown','suppressed')", name='ck_staff_delivery_status'),
        sa.ForeignKeyConstraint(['owner_id','role'], ['staff_role.owner_id','staff_role.key'], name='fk_staff_invite_owned_role'),
        sa.CheckConstraint("state IN ('pending','accepted','revoked')",name='ck_invite_state'))
    op.create_index('ix_staff_invitation_owner_id','staff_invitation',['owner_id'])
    op.create_index('ix_staff_invite_seats','staff_invitation',['owner_id','state','expires_at'])
    op.create_table('staff_membership_business',
        sa.Column('membership_id',sa.Integer(),sa.ForeignKey('staff_membership.id'),primary_key=True),
        sa.Column('business_id',sa.Integer(),sa.ForeignKey('business.id'),primary_key=True))
    op.create_table('staff_invitation_business',
        sa.Column('invitation_id',sa.Integer(),sa.ForeignKey('staff_invitation.id'),primary_key=True),
        sa.Column('business_id',sa.Integer(),sa.ForeignKey('business.id'),primary_key=True))

    op.create_table('staff_audit_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('audit_log_id', sa.Integer(), sa.ForeignKey('audit_log.id'), nullable=False, unique=True),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False))
    op.create_index('ix_staff_audit_event_owner_id', 'staff_audit_event', ['owner_id'])


def downgrade():
    raise RuntimeError('Staff history must be reviewed and archived explicitly; automatic destructive downgrade is disabled.')
