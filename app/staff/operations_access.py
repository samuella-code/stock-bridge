"""Explicit scoped authority for product, expense and financial workflows."""
from datetime import datetime
from flask import current_app
from app import db
from app.models import StaffMembership
from app.staff import roles,service

PERMISSIONS=frozenset(('products_create','products_edit','expenses_view','expenses_create','financial_view'))


def enabled():return current_app.config.get('STAFF_OPERATIONS_ENABLED')=='true'


def authorize(user,membership_id,business_id,permission,*,write=False):
    if not enabled() or permission not in PERMISSIONS:raise ValueError('Staff workflow unavailable.')
    member=StaffMembership.query.filter_by(id=membership_id,user_id=user.id).first()
    if member is None:raise ValueError('Staff workflow unavailable.')
    if write:
        service.lock_owner(member.owner_id)
        member=StaffMembership.query.filter_by(id=membership_id,user_id=user.id).first()
        if member is None:raise ValueError('Staff workflow unavailable.')
    choices=service.member_businesses(member,user,now=datetime.utcnow())
    business=next((b for b in choices if b.id==business_id),None)
    if business is None or permission not in roles.resolve(member.owner_id,member.role).permissions:
        raise ValueError('Staff workflow unavailable.')
    return member,business
