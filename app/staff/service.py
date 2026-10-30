"""Transactional owner controls. Callers commit or roll back the whole operation.

Lock the owner's User row BEFORE seat/token reads. This shares billing's owner
lock, serializes PostgreSQL writers and obtains SQLite's write lock. No retries.
"""
from datetime import datetime, timedelta
import hashlib
import json
import re
import secrets
from sqlalchemy import func, update
from flask import current_app
from app import db
from app.models import (User, Business, AccountBilling, AuditLog, RecurringSubscription,
    StaffMembership, StaffInvitation, StaffMembershipBusiness, StaffInvitationBusiness, StaffAuditEvent)
from app.staff.lifecycle import coverage, event_watermark
from app.staff import roles as role_service
from app.subscriptions.entitlements import EntitlementSnapshot

SEATS = 5
ROLES = {key: name for key, (name, _) in role_service.TEMPLATES.items()}


def lock_owner(owner_id):
    with db.session.no_autoflush:
        db.session.execute(update(User).where(User.id == owner_id).values(last_activity_at=User.last_activity_at))
    # Discard earlier request reads after waiting for a concurrent owner write.
    db.session.expire_all()


def owner_access(owner, *, now):
    if owner.staff_only or not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        raise ValueError('Current paid Pro owner access is required.')
    snapshot = EntitlementSnapshot([owner], now=now)
    access = snapshot.access(owner)
    if not (access.can_write and access.subscription and access.subscription.plan_code == 'pro'):
        raise ValueError('Current paid Pro owner access is required.')
    choices = Business.query.filter_by(user_id=owner.id).order_by(Business.id).all()
    return access, [b for b in choices if snapshot.access(owner, business=b).can_write]


def seat_count(owner_id, *, now, except_invitation=None):
    active = StaffMembership.query.filter_by(owner_id=owner_id, state='active').count()
    pending = StaffInvitation.query.filter(StaffInvitation.owner_id == owner_id,
        StaffInvitation.state == 'pending', StaffInvitation.expires_at > now)
    if except_invitation is not None: pending = pending.filter(StaffInvitation.id != except_invitation)
    return active + pending.count()


def fields(role, label, finance, ids, choices):
    owner_id = choices[0].user_id if choices else None
    role_service.resolve(owner_id, role)
    if not isinstance(label, str) or len(label.strip()) > 100:
        raise ValueError('Choose a staff role and a duty label of up to 100 characters.')
    if finance is not False:
        raise ValueError('Configure permissions on the owned role, not an individual label.')
    try: ids = set(int(value) for value in ids)
    except (ValueError, TypeError): raise ValueError('Choose permitted business assignments.') from None
    if not ids or not ids <= {b.id for b in choices}:
        raise ValueError('Choose only owned, permitted, non-suspended businesses.')
    return role, label.strip(), ids


def audit(owner_id, actor_id, action, *, invitation=None, membership=None):
    # Fixed metadata only: no token, URL, email, name, duty text or passwords.
    facts = dict(owner_id=owner_id, invitation_id=invitation.id if invitation else None,
                 membership_id=membership.id if membership else None)
    row = membership or invitation
    facts.update(role=row.role, state=row.state,
        subscription_id=row.grant_subscription_id, grant_event_id=row.grant_event_id,
        grant_started_at=row.grant_started_at.isoformat(), grant_pro_start=row.grant_pro_start.isoformat())
    if membership:
        facts.update(access_until=row.access_until.isoformat(),
            businesses=[r.business_id for r in StaffMembershipBusiness.query.filter_by(membership_id=membership.id)])
    else:
        facts.update(expires_at=row.expires_at.isoformat(),
            businesses=[r.business_id for r in StaffInvitationBusiness.query.filter_by(invitation_id=invitation.id)])
    log = AuditLog(actor_id=actor_id, action=action, target_type='staff',
        target_id=membership.id if membership else invitation.id, description='Staff event recorded; structured details retained.')
    db.session.add(log); db.session.flush()
    db.session.add(StaffAuditEvent(audit_log_id=log.id, owner_id=owner_id, details=facts))


def grant_matches(owner, row, *, now):
    try: _, choices = owner_access(owner, now=now)
    except ValueError: return False, []
    end = coverage(owner.id, row, now=now)
    return (True, choices) if end is not None and now < end else (False, [])


def invite(owner, email, role, label, finance, ids, *, now):
    lock_owner(owner.id)
    now = max(now, datetime.utcnow())
    return _invite_locked(owner, email, role, label, finance, ids, now=now)


def _invite_locked(owner, email, role, label, finance, ids, *, now):
    access, choices = owner_access(owner, now=now)
    role_service._initialize_locked(owner.id)
    role, label, ids = fields(role, label, finance, ids, choices)
    email = email.strip().lower()
    if len(email) > 180 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email) or email == owner.email.lower():
        raise ValueError('Enter a staff email different from the owner email.')
    existing = User.query.filter(func.lower(User.email) == email).first()
    if existing and (existing.role != 'user' or existing.admin_enabled or existing.suspended_at):
        raise ValueError('That account cannot receive staff access.')
    duplicate = StaffInvitation.query.filter(StaffInvitation.owner_id == owner.id,
        StaffInvitation.email == email, StaffInvitation.state == 'pending', StaffInvitation.expires_at > now).first()
    member = StaffMembership.query.join(User, User.id == StaffMembership.user_id).filter(
        StaffMembership.owner_id == owner.id, StaffMembership.state == 'active', func.lower(User.email) == email).first()
    if duplicate or member: raise ValueError('This person already occupies a seat or has a pending invitation.')
    if seat_count(owner.id, now=now) >= SEATS: raise ValueError('All five staff seats are occupied.')
    raw = secrets.token_urlsafe(32)
    invitation = StaffInvitation(owner_id=owner.id, email=email, role=role, duty_label=label,
        token_digest=hashlib.sha256(raw.encode()).hexdigest(),
        grant_subscription_id=access.subscription.id, grant_event_id=event_watermark(owner.id), grant_started_at=now, grant_pro_start=access.subscription.current_period_start,
        expires_at=min(now+timedelta(days=2), access.subscription.current_period_end), created_at=now)
    db.session.add(invitation); db.session.flush()
    for bid in sorted(ids): db.session.add(StaffInvitationBusiness(invitation_id=invitation.id, business_id=bid))
    audit(owner.id, owner.id, 'STAFF_INVITED', invitation=invitation)
    return invitation, raw


def invitation_for(raw, *, now):
    if not isinstance(raw, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', raw):
        raise ValueError('Invitation is invalid or no longer available.')
    row = StaffInvitation.query.filter_by(token_digest=hashlib.sha256(raw.encode()).hexdigest(), state='pending').first()
    if not row or now >= row.expires_at: raise ValueError('Invitation is invalid or no longer available.')
    owner = db.session.get(User, row.owner_id)
    # Invitations are never extended to a later paid period automatically.
    access, choices = owner_access(owner, now=now)
    # Invitation expiry is never extended. Reuse evidence-based continuity for
    # downgrade detection within its original, already capped lifetime.
    from types import SimpleNamespace
    anchor = SimpleNamespace(access_until=row.expires_at, grant_started_at=row.grant_started_at, grant_event_id=row.grant_event_id, grant_pro_start=row.grant_pro_start, grant_subscription_id=row.grant_subscription_id)
    if coverage(owner.id, anchor, now=now) is None:
        raise ValueError('Invitation is invalid or no longer available.')
    ids = {r.business_id for r in StaffInvitationBusiness.query.filter_by(invitation_id=row.id)}
    fields(row.role, row.duty_label, False, ids, choices)
    return row


def accept(raw, actor, full_name, password, confirm, *, now):
    # The first token read discovers only the lock identity; re-read after locking.
    row = invitation_for(raw, now=now)
    lock_owner(row.owner_id)
    now = max(now, datetime.utcnow())
    row = invitation_for(raw, now=now)
    owner = db.session.get(User, row.owner_id)
    access, choices = owner_access(owner, now=now)
    existing = User.query.filter(func.lower(User.email) == row.email).first()
    if existing:
        if not actor or actor.id != existing.id or actor.email.lower() != row.email or not actor.email_verified_at:
            raise ValueError('Sign in as the verified invited account before accepting.')
        user = existing
    else:
        if actor: raise ValueError('Sign out before creating the invited account.')
        if not full_name.strip() or len(full_name.strip()) > 120 or not 12 <= len(password) <= 128 or password != confirm:
            raise ValueError('Enter your name and matching passwords of 12–128 characters.')
        user = User(full_name=full_name.strip(), email=row.email, staff_only=True)
        user.set_password(password); db.session.add(user); db.session.flush()
    if user.role != 'user' or user.admin_enabled or user.suspended_at or user.id == owner.id:
        raise ValueError('This identity cannot accept staff access.')
    # An existing owner retains their independent owner permissions, never staff workflows.
    if not Business.query.filter_by(user_id=user.id).first() and not db.session.get(AccountBilling, user.id):
        user.staff_only = True
    if seat_count(owner.id, now=now, except_invitation=row.id) >= SEATS:
        raise ValueError('All five staff seats are occupied.')
    watermark = event_watermark(owner.id)
    membership = StaffMembership.query.filter_by(owner_id=owner.id, user_id=user.id).first()
    if membership and membership.state == 'active': raise ValueError('This membership is already active.')
    if not membership:
        membership = StaffMembership(owner_id=owner.id, user_id=user.id, created_at=now)
        db.session.add(membership)
    membership.role, membership.duty_label = row.role, row.duty_label
    membership.state, membership.revoked_at = 'active', None
    membership.grant_event_id, membership.grant_started_at = watermark, now
    membership.grant_pro_start = access.subscription.current_period_start
    membership.grant_subscription_id, membership.access_until = access.subscription.id, access.subscription.current_period_end
    db.session.flush()
    StaffMembershipBusiness.query.filter_by(membership_id=membership.id).delete(synchronize_session=False)
    for assignment in StaffInvitationBusiness.query.filter_by(invitation_id=row.id):
        db.session.add(StaffMembershipBusiness(membership_id=membership.id, business_id=assignment.business_id))
    row.state, row.accepted_at, row.membership_id = 'accepted', now, membership.id
    db.session.flush(); audit(owner.id, user.id, 'STAFF_ACCEPTED', invitation=row, membership=membership)
    return user, membership


def change(owner, membership_id, role, label, finance, ids, *, now):
    lock_owner(owner.id)
    now = max(now, datetime.utcnow())
    _, choices = owner_access(owner, now=now)
    row = StaffMembership.query.filter_by(id=membership_id, owner_id=owner.id, state='active').first()
    if not row: raise ValueError('Active membership is unavailable.')
    role_service._initialize_locked(owner.id)
    values = fields(role, label, finance, ids, choices)
    audit(owner.id, owner.id, 'STAFF_CHANGE_BEFORE', membership=row)
    row.role, row.duty_label, assigned = values
    StaffMembershipBusiness.query.filter_by(membership_id=row.id).delete(synchronize_session=False)
    for bid in sorted(assigned): db.session.add(StaffMembershipBusiness(membership_id=row.id, business_id=bid))
    db.session.flush(); audit(owner.id, owner.id, 'STAFF_CHANGED', membership=row)


def revoke(owner, kind, identifier, *, now):
    lock_owner(owner.id)
    now = max(now, datetime.utcnow())
    owner_access(owner, now=now)
    model = StaffInvitation if kind == 'invitation' else StaffMembership if kind == 'membership' else None
    if model is None: raise ValueError('Choose an invitation or membership.')
    row = model.query.filter_by(id=identifier, owner_id=owner.id).first()
    if not row: raise ValueError('Staff record is unavailable.')
    if row.state == 'revoked': return
    if kind == 'invitation' and row.state != 'pending': raise ValueError('Invitation is no longer pending.')
    row.state, row.revoked_at = 'revoked', now
    audit(owner.id, owner.id, 'STAFF_REVOKED', **{kind:row})


def reconfirm(owner, identifier, *, now):
    lock_owner(owner.id)
    now = max(now, datetime.utcnow())
    access, choices = owner_access(owner, now=now)
    row = StaffMembership.query.filter_by(id=identifier, owner_id=owner.id, state='active').first()
    if not row: raise ValueError('Only an active membership can be reconfirmed; revoked staff require a new invitation.')
    ids = {r.business_id for r in StaffMembershipBusiness.query.filter_by(membership_id=row.id)}
    fields(row.role, row.duty_label, False, ids, choices)
    row.grant_event_id, row.grant_started_at = event_watermark(owner.id), now
    row.grant_pro_start = access.subscription.current_period_start
    row.grant_subscription_id, row.access_until = access.subscription.id, access.subscription.current_period_end
    audit(owner.id, owner.id, 'STAFF_RECONFIRMED', membership=row)


def member_businesses(membership, user, *, now):
    if membership.user_id != user.id or membership.state != 'active' or user.suspended_at or not user.email_verified_at or user.role != 'user' or user.admin_enabled:
        return []
    try: role_service.resolve(membership.owner_id, membership.role)
    except ValueError: return []
    valid, choices = grant_matches(db.session.get(User, membership.owner_id), membership, now=now)
    if not valid: return []
    ids = {r.business_id for r in StaffMembershipBusiness.query.filter_by(membership_id=membership.id)}
    return [b for b in choices if b.id in ids]


def can_operate(*args, **kwargs):
    # Fail closed until the later role-aware transaction/financial workflow patch.
    return False


def resend(owner, identifier, *, now):
    """Explicit replacement, not an automatic retry; caller commits before SMTP."""
    lock_owner(owner.id); now = max(now, datetime.utcnow()); owner_access(owner, now=now)
    row = StaffInvitation.query.filter_by(id=identifier, owner_id=owner.id, state='pending').first()
    if not row: raise ValueError('Pending invitation is unavailable.')
    if row.delivery_status == 'sending' and row.delivery_attempted_at and now < row.delivery_attempted_at + timedelta(minutes=2):
        raise ValueError('Delivery is still in progress; review later.')
    ids = [r.business_id for r in StaffInvitationBusiness.query.filter_by(invitation_id=row.id)]
    row.state, row.revoked_at = 'revoked', now
    audit(owner.id, owner.id, 'STAFF_INVITE_REPLACED', invitation=row)
    db.session.flush()
    return _invite_locked(owner, row.email, row.role, row.duty_label, False, ids, now=now)
