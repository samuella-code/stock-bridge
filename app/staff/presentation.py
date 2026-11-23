"""Read-only team presentation; authority and lifecycle stay in their existing services."""
from flask import current_app
from app import db
from app.models import User, StaffMembership
from app.staff import service, roles, delegation_access
from app.staff.lifecycle import coverage
from app.admin.customer_data import masked_email, local_time


def team_choices(user, now):
    candidates={user.id}
    candidates.update(row.owner_id for row in StaffMembership.query.filter_by(user_id=user.id,state='active'))
    result=[]
    for identifier in sorted(candidates):
        owner=db.session.get(User,identifier)
        if not owner:continue
        try:authority,businesses=delegation_access.mutation(owner,user,now=now)
        except ValueError:continue
        result.append((owner,authority,businesses))
    return result


def team_card(owner, authority, businesses, now):
    return dict(owner=owner,is_owner=authority.owner,businesses=businesses,
        seats=service.seat_count(owner.id,now=now),roles=len(roles.options(owner.id)),plan='Pro active')


def member_status(owner, member, person, choices, now):
    result=dict(label='Access unavailable',tone='muted',paused_due_pro=False,can_restore=False)
    if member.state!='active':return dict(result,label='Access removed',tone='danger')
    if not person or person.suspended_at or person.role!='user' or person.admin_enabled:return result
    if not person.email_verified_at:return dict(result,label='Email verification needed')
    try:roles.resolve(owner.id,member.role)
    except ValueError:return result
    end=coverage(owner.id,member,now=now)
    if end is None or now>=end:
        ids=delegation_access.assignments(member)
        return dict(result,label='Access paused',tone='warning',paused_due_pro=True,
            can_restore=bool(ids) and ids<={business.id for business in choices})
    if service.member_businesses(member,person,now=now):return dict(result,label='Active',tone='healthy')
    return dict(result,label='No available business assigned')


def team_details(context, now):
    owner=context['team_owner'];people={};statuses={};usage={key:0 for key in context['roles']}
    for member in context['memberships']:
        person=db.session.get(User,member.user_id)
        people[member.user_id]=masked_email(person.email) if person else 'Unavailable'
        statuses[member.id]=member_status(owner,member,person,context['choices'],now)
        if member.state=='active' and member.role in usage:usage[member.role]+=1
    invitations={}
    for row in context['invitations']:
        state='Expired' if row.state=='pending' and row.expires_at<=now else {'pending':'Invited','accepted':'Accepted','revoked':'Removed'}.get(row.state,'Unavailable')
        invitations[row.id]=dict(email=masked_email(row.email),status=state,
            accepted=local_time(row.accepted_at),expires=local_time(row.expires_at))
    from app.staff.delivery import gate
    import os
    return dict(member_emails=people,member_statuses=statuses,role_usage=usage,invitation_details=invitations,
        invitation_email_off=gate(owner.email, current_app.config, os.environ)!='ready')
