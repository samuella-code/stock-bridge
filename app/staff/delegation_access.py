"""Database adapter for the pure policy; reuse authoritative Pro/lifecycle rules."""
from app import db
from app.models import (User,StaffMembership,StaffInvitation,StaffMembershipBusiness,StaffInvitationBusiness)
from app.staff import delegation as policy


def authority(owner, actor, *, now):
    from app.staff import service,roles
    _,choices=service.owner_access(owner,now=now)
    if actor.id==owner.id:
        return policy.Authority(owner.id,actor.id,frozenset(b.id for b in choices),frozenset(roles.CATALOGUE)|policy.OWNER_GRANTED),choices
    member=StaffMembership.query.filter_by(owner_id=owner.id,user_id=actor.id,state='active').first()
    policy.require(member is not None)
    permitted=service.member_businesses(member,actor,now=now)
    policy.require(bool(permitted))
    role=roles.resolve(owner.id,member.role)
    return policy.Authority(owner.id,actor.id,frozenset(b.id for b in permitted),frozenset(role.permissions),role.key),permitted


def assignments(row):
    if isinstance(row,StaffMembership):
        return frozenset(r.business_id for r in StaffMembershipBusiness.query.filter_by(membership_id=row.id))
    return frozenset(r.business_id for r in StaffInvitationBusiness.query.filter_by(invitation_id=row.id))


def target(auth, row):
    from app.staff import roles
    policy.require(row.owner_id==auth.owner_id)
    if auth.owner:return
    role=roles.resolve(row.owner_id,row.role)
    auth.assignment(role.permissions,assignments(row),target_user=getattr(row,'user_id',None))
    if not auth.owner and isinstance(row,StaffInvitation):
        user=db.session.get(User,auth.actor_id)
        policy.require(row.email.lower()!=user.email.lower())


def references(owner_id, key, *, actor_id=None):
    # Do not trim assignments to the current scope; every reference participates.
    members=StaffMembership.query.filter_by(owner_id=owner_id,role=key).all()
    invitations=StaffInvitation.query.filter_by(owner_id=owner_id,role=key).all()
    identities={}
    if actor_id is not None:
        for uid in (owner_id,actor_id):
            user=db.session.get(User,uid)
            policy.require(user is not None)
            identities[user.email.lower()]=uid
    return [(m.user_id,assignments(m)) for m in members]+[(identities.get(i.email.lower()),assignments(i)) for i in invitations]


def shared(auth, key, old, new):
    auth.shared_role(key,old,new,references(auth.owner_id,key,actor_id=auth.actor_id) if key else [])


def mutation(owner, actor, *, now):
    auth,choices=authority(owner,actor,now=now)
    auth.check('staff_admin')
    return auth,choices


def export_plan(owner, actor, dataset, businesses, fields, *, now):
    auth,_=authority(owner,actor,now=now)
    return policy.export_scope(auth,dataset,businesses,fields)


def export_records(*args, **kwargs):
    raise policy.Denied('Staff exports are not enabled.')
