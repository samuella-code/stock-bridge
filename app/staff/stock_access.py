"""Stock-only runtime authority. No session-selected owner business is trusted."""
from datetime import datetime
from flask import current_app
from app import db
from app.models import StaffMembership
from app.staff import roles, service


def enabled():
    return current_app.config.get('STAFF_STOCK_ENABLED') == 'true'


def authorize(user, membership_id, business_id, permission, *, write=False, now=None):
    if not enabled() or permission not in ('inventory_view', 'restocking_view', 'restocking_create'):
        raise ValueError('Staff stock unavailable.')
    member = StaffMembership.query.filter_by(id=membership_id, user_id=user.id).first()
    if member is None:
        raise ValueError('Staff stock unavailable.')
    if write:
        # Same owner lock used by sales, grants, revocation and assignments.
        service.lock_owner(member.owner_id)
        member = StaffMembership.query.filter_by(id=membership_id, user_id=user.id).first()
        if member is None:
            raise ValueError('Staff stock unavailable.')
    choices = service.member_businesses(member, user, now=now or datetime.utcnow())
    business = next((b for b in choices if b.id == business_id), None)
    if business is None or permission not in roles.resolve(member.owner_id, member.role).permissions:
        raise ValueError('Staff stock unavailable.')
    return member, business
