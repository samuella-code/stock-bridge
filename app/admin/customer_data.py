"""Scoped customer reads and explicit serializers for the admin portal."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import aliased
from app import db
from app.models import (User, Business, Payment, RecurringSubscription, StaffMembership,
                        StaffInvitation, StaffMembershipBusiness, StaffInvitationBusiness,
                        StaffRole, AuditLog, AdminCustomerNote)
from app.admin.dashboard import owner_alert_query
from app.admin.subscription_reporting import verified_subscription_payments_query
from app.subscriptions.entitlements import EntitlementSnapshot, current_subscriptions, PLANS

LAGOS = ZoneInfo('Africa/Lagos')
FILTERS = ('all', 'owners', 'employees', 'paid', 'trial-ended', 'renewal-stopped', 'active', 'suspended', 'unpaid')


def local_time(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(LAGOS).strftime('%d/%m/%Y · %H:%M WAT')


def masked_email(email):
    local, separator, domain = email.partition('@')
    if not separator:
        return '•••'
    name, dot, suffix = domain.rpartition('.')
    return local[:1] + '•••@' + (name[:1] + '•••.' + suffix if dot else '•••')


def masked_description(value):
    return re.sub(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
                  lambda match: masked_email(match.group(0)), value)


def customers():
    return User.query.filter(User.role == 'user', User.admin_enabled.is_(False))


def customer(identifier):
    return customers().filter(User.id == identifier).first_or_404()


def paying_condition(now):
    paid = current_subscriptions(now).filter(
        RecurringSubscription.user_id == User.id,
        or_(*[and_(RecurringSubscription.plan_code == plan, RecurringSubscription.billing_interval == interval)
                  for plan, interval in PLANS]),
    ).exists()
    return and_(User.staff_only.is_(False), User.suspended_at.is_(None), User.email_verified_at.isnot(None), paid)


def filtered_customers(q, status, now):
    query = customers()
    if q:
        assigned_business = db.session.query(StaffMembership.id).join(
            StaffMembershipBusiness, StaffMembershipBusiness.membership_id == StaffMembership.id).join(
            Business, Business.id == StaffMembershipBusiness.business_id).filter(
            StaffMembership.user_id == User.id, StaffMembership.state == 'active',
            Business.user_id == StaffMembership.owner_id, Business.name.icontains(q, autoescape=True)).exists()
        query = query.filter(or_(User.full_name.icontains(q, autoescape=True), User.email.icontains(q, autoescape=True),
                                 User.businesses.any(Business.name.icontains(q, autoescape=True)),
                                 and_(User.staff_only.is_(True), assigned_business)))
    if status == 'owners':
        query = query.filter(User.staff_only.is_(False))
    elif status == 'employees':
        query = query.filter(User.staff_only.is_(True))
    elif status == 'paid':
        query = query.filter(paying_condition(now))
    elif status == 'unpaid':
        query = query.filter(User.staff_only.is_(False), ~paying_condition(now))
    elif status in ('trial-ended', 'renewal-stopped'):
        query = query.filter(User.id.in_(owner_alert_query(status, now).with_entities(User.id)))
    elif status == 'active':
        query = query.filter(User.suspended_at.is_(None))
    elif status == 'suspended':
        query = query.filter(User.suspended_at.isnot(None))
    return query.order_by(User.created_at.desc(), User.id.desc())


def summary(now):
    owners, employees, paying = db.session.query(
        func.count(User.id).filter(User.staff_only.is_(False)),
        func.count(User.id).filter(User.staff_only.is_(True)),
        func.count(User.id).filter(paying_condition(now)),
    ).filter(User.role == 'user', User.admin_enabled.is_(False)).one()
    return dict(owners=owners, employees=employees, paying=paying,
                trials_ended=owner_alert_query('trial-ended', now).count())


class CustomerRows:
    def __init__(self, users, now):
        self.users = list(users)
        self.now = now
        self.snapshot = EntitlementSnapshot(self.users, now=now)
        ids = [u.id for u in self.users]
        self.businesses = {uid: [] for uid in ids}
        for shop in Business.query.filter(Business.user_id.in_(ids)).order_by(Business.id):
            self.businesses[shop.user_id].append(dict(id=shop.id, name=shop.name, suspended=bool(shop.suspended_at)))
        owner_alias = aliased(User)
        seen = set()
        for member, shop in db.session.query(StaffMembership, Business).join(
                StaffMembershipBusiness, StaffMembershipBusiness.membership_id == StaffMembership.id).join(
                Business, Business.id == StaffMembershipBusiness.business_id).join(
                owner_alias, owner_alias.id == StaffMembership.owner_id).filter(
                StaffMembership.user_id.in_([u.id for u in self.users if u.staff_only]), StaffMembership.state == 'active',
                owner_alias.role == 'user', owner_alias.admin_enabled.is_(False),
                Business.user_id == StaffMembership.owner_id).order_by(Business.id):
            if (member.user_id, shop.id) not in seen:
                self.businesses[member.user_id].append(dict(id=shop.id, name=shop.name, suspended=bool(shop.suspended_at)))
                seen.add((member.user_id, shop.id))
        self.employee_counts = dict(db.session.query(StaffMembership.owner_id, func.count(StaffMembership.id)).filter(
            StaffMembership.owner_id.in_(ids), StaffMembership.state == 'active',
        ).group_by(StaffMembership.owner_id))
        latest = db.session.query(RecurringSubscription.user_id.label('uid'), func.max(RecurringSubscription.id).label('sid')).filter(
            RecurringSubscription.user_id.in_(ids)).group_by(RecurringSubscription.user_id).subquery()
        self.latest = {s.user_id: s for s in RecurringSubscription.query.join(latest, latest.c.sid == RecurringSubscription.id)}

    def serialize(self, user):
        access = self.snapshot.access(user)
        account = self.snapshot.accounts.get(user.id)
        sub = access.subscription
        latest = self.latest.get(user.id)
        plan = 'Choose a plan'
        if user.staff_only:
            plan = 'Covered by owner’s plan'
        elif sub:
            plan = sub.plan_code.title()
        elif access.kind == 'trial':
            plan = 'Free trial'
        elif access.legacy:
            plan = 'Existing account access'
        elif latest and latest.current_period_end and latest.current_period_end <= self.now:
            plan = 'Subscription expired'
        elif account and account.trial_ends_at and account.trial_ends_at <= self.now:
            plan = 'Trial expired'
        return dict(id=user.id, name=user.full_name, email=masked_email(user.email),
                    employee=user.staff_only, employee_count=self.employee_counts.get(user.id, 0),
                    businesses=self.businesses[user.id], plan=plan, verified=bool(user.email_verified_at),
                    suspended=bool(user.suspended_at), suspended_at=user.suspended_at.isoformat() if user.suspended_at else '', joined=local_time(user.created_at),
                    last_active=local_time(user.last_activity_at),
                    next_renewal=local_time(sub.current_period_end) if sub and not user.staff_only else None,
                    renewal_stopped=bool(sub and (sub.cancel_at_period_end or sub.status in ('cancel_at_period_end', 'cancelled'))),
                    interval=sub.billing_interval if sub and not user.staff_only else None,
                    trial_end=account.trial_ends_at.isoformat() if account and account.trial_ends_at and not user.staff_only else None)


def invitation_status(row, now, removed_memberships=()):
    if row.state == 'revoked' or (row.state == 'accepted' and row.membership_id in removed_memberships):
        return 'Removed'
    if row.state == 'accepted':
        return 'Accepted'
    return 'Expired' if row.expires_at <= now else 'Invited'


def invitation_query(q='', status='all', now=None):
    now = now or datetime.utcnow()
    query = StaffInvitation.query.join(User, User.id == StaffInvitation.owner_id).filter(
        User.role == 'user', User.admin_enabled.is_(False), User.staff_only.is_(False))
    if q:
        business_match = db.session.query(StaffInvitationBusiness.invitation_id).join(
            Business, Business.id == StaffInvitationBusiness.business_id).filter(
                StaffInvitationBusiness.invitation_id == StaffInvitation.id,
                Business.user_id == StaffInvitation.owner_id, Business.name.icontains(q, autoescape=True)).exists()
        role_match = StaffRole.query.filter(StaffRole.owner_id == StaffInvitation.owner_id,
            StaffRole.key == StaffInvitation.role, StaffRole.name.icontains(q, autoescape=True)).exists()
        query = query.filter(or_(StaffInvitation.email.icontains(q, autoescape=True),
            User.full_name.icontains(q, autoescape=True), business_match, role_match))
    removed_member = StaffMembership.query.filter(StaffMembership.id == StaffInvitation.membership_id, StaffMembership.state == 'revoked').exists()
    if status == 'Invited':
        query = query.filter(StaffInvitation.state == 'pending', StaffInvitation.expires_at > now)
    elif status == 'Expired':
        query = query.filter(StaffInvitation.state == 'pending', StaffInvitation.expires_at <= now)
    elif status == 'Accepted':
        query = query.filter(StaffInvitation.state == 'accepted', ~removed_member)
    elif status == 'Removed':
        query = query.filter(or_(StaffInvitation.state == 'revoked', and_(StaffInvitation.state == 'accepted', removed_member)))
    return query.order_by(StaffInvitation.created_at.desc(), StaffInvitation.id.desc())


def invitations(rows, now):
    rows = list(rows)
    ids = [r.id for r in rows]
    owner_ids = {r.owner_id for r in rows}
    owners = {u.id: u for u in User.query.filter(User.id.in_(owner_ids))}
    removed_memberships = {mid for mid, in db.session.query(StaffMembership.id).filter(
        StaffMembership.id.in_([r.membership_id for r in rows if r.membership_id]), StaffMembership.state == 'revoked')}
    roles = {(r.owner_id, r.key): r.name for r in StaffRole.query.filter(StaffRole.owner_id.in_(owner_ids))}
    shops = {identifier: [] for identifier in ids}
    for assignment, shop in db.session.query(StaffInvitationBusiness, Business).join(
            Business, Business.id == StaffInvitationBusiness.business_id).join(
            StaffInvitation, StaffInvitation.id == StaffInvitationBusiness.invitation_id).filter(
            StaffInvitation.id.in_(ids), Business.user_id == StaffInvitation.owner_id):
        shops[assignment.invitation_id].append(dict(id=shop.id, name=shop.name))
    actors = {}
    for event, actor in db.session.query(AuditLog, User).join(User, User.id == AuditLog.actor_id).filter(
            AuditLog.action == 'STAFF_INVITED', AuditLog.target_type == 'staff', AuditLog.target_id.in_(ids)).order_by(AuditLog.id):
        actors.setdefault(event.target_id, dict(id=actor.id, name=actor.full_name))
    return [dict(id=r.id, owner_id=r.owner_id, owner=owners[r.owner_id].full_name, email=masked_email(r.email),
                 invited_by=actors.get(r.id, dict(id=r.owner_id, name=owners[r.owner_id].full_name)),
                 role=roles.get((r.owner_id, r.role), r.role), businesses=shops[r.id],
                 sent=local_time(r.created_at), accepted=local_time(r.accepted_at), status=invitation_status(r, now, removed_memberships),
                 email_delivery=r.delivery_status, membership_id=r.membership_id) for r in rows]


def memberships_for(user, now):
    from app.staff.service import member_businesses
    rows = StaffMembership.query.filter_by(user_id=user.id).order_by(StaffMembership.id).all()
    result = []
    for member in rows:
        owner = customers().filter(User.id == member.owner_id, User.staff_only.is_(False)).first()
        if not owner:
            continue
        role = db.session.get(StaffRole, (member.owner_id, member.role))
        assigned = db.session.query(Business).join(StaffMembershipBusiness, StaffMembershipBusiness.business_id == Business.id).filter(
            StaffMembershipBusiness.membership_id == member.id, Business.user_id == owner.id).order_by(Business.id).all()
        result.append(dict(id=member.id, owner_id=owner.id, owner=owner.full_name,
            role=role.name if role else member.role, status='Removed' if member.state == 'revoked' else 'Accepted',
            access_active=bool(member_businesses(member, user, now=now)),
            businesses=[dict(id=b.id, name=b.name) for b in assigned]))
    return result


def details(user, now):
    result = CustomerRows([user], now).serialize(user)
    if user.staff_only:
        team = invitation_query(now=now).filter(func.lower(StaffInvitation.email) == user.email.lower()).all()
        result.update(works_for=memberships_for(user, now), invitations=invitations(team, now), paid_so_far_kobo=None,
                      staff_seats=None)
    else:
        from app.staff.service import seat_count
        team = invitation_query(now=now).filter(StaffInvitation.owner_id == user.id).all()
        paid = verified_subscription_payments_query().filter(Payment.user_id == user.id).with_entities(
            func.coalesce(func.sum(Payment.amount_kobo), 0)).scalar()
        from app.admin.routes import verified_payments_query
        legacy = verified_payments_query().filter(Business.user_id == user.id).with_entities(
            func.coalesce(func.sum(Payment.amount_kobo), 0)).scalar()
        result.update(team=invitations(team, now), paid_so_far_kobo=int(paid + legacy),
                      staff_seats=dict(used=seat_count(user.id, now=now), limit=5))
    note = db.session.get(AdminCustomerNote, user.id)
    result['note'] = dict(body=note.body, version=note.version, updated_at=local_time(note.updated_at)) if note else dict(body='', version=0, updated_at=None)
    return result
