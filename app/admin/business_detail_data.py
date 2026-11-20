"""Read-only, business-scoped projections for the admin detail page."""
from datetime import timedelta, timezone
from sqlalchemy import and_, or_, func
from app import db
from app.models import AuditLog
from app.admin import activity_data, business_data
from app.admin.customer_data import CustomerRows, local_time, LAGOS
from app.admin.payment_data import money
from app.models import (Payment, Product, Sale, Restock, Expense, StaffMembership,
                        StaffMembershipBusiness, StaffRole, User)

SUSPEND_REASONS = ('Violated rules', 'Fraud concern', 'Non-payment', 'Owner asked', 'Other')
REACTIVATE_REASONS = ('Review complete', 'Owner asked', 'Other')


def activity(shop, now):
    # Keep every immutable audit row. Only the presentation groups repeated views.
    events = activity_data.filtered(business=shop).filter(
        AuditLog.created_at <= now).limit(120).all()
    rendered = activity_data.serialize(events, now)
    result, views = [], {}
    for event, item in zip(events, rendered):
        if event.action == 'ADMIN_READ_ONLY_OPENED':
            key = (event.actor_id, event.business_id, event.target_type, event.target_id)
            previous = views.get(key)
            if previous and previous['at'] - event.created_at <= timedelta(minutes=5):
                previous['item']['count'] += 1
                continue
            item['sentence'] = item['actor'] + ' opened a read-only view of ' + shop.name
            item['count'] = 1
            views[key] = dict(at=event.created_at, item=item)
        else:
            item['count'] = 1
            # Show the business owner by name, never a numeric internal account.
            if event.target_type == 'user' and event.target_id == shop.user_id:
                item['sentence'] += ' for ' + shop.owner.full_name
        result.append(item)
    return result[:8]


def details(shop, now):
    owner_rows = CustomerRows([shop.owner], now)
    owner = owner_rows.serialize(shop.owner)
    account_access = owner_rows.snapshot.access(shop.owner)
    sub = account_access.subscription
    status = ('Suspended' if shop.suspended_at or shop.owner.suspended_at else
              'Active' if business_data.writable(shop, owner_rows.snapshot) else 'Read-only')
    team = db.session.query(User, StaffRole).join(StaffMembership,
        StaffMembership.user_id == User.id).join(StaffMembershipBusiness,
        StaffMembershipBusiness.membership_id == StaffMembership.id).join(StaffRole, and_(
            StaffRole.owner_id == StaffMembership.owner_id, StaffRole.key == StaffMembership.role)).filter(
        StaffMembership.owner_id == shop.user_id, StaffMembership.state == 'active',
        StaffMembershipBusiness.business_id == shop.id, User.role == 'user',
        User.admin_enabled.is_(False)).order_by(User.full_name, User.id).all()
    team = [dict(id=u.id, name=u.full_name, role=role.name,
        status='Account suspended' if u.suspended_at else 'Assigned') for u, role in team]
    tiles = []
    for label, model, stamp, noun in (
            ('Products', Product, Product.created_at, 'Product'), ('Sales', Sale, Sale.sold_at, 'Sale'),
            ('Restocks', Restock, Restock.received_at, 'Restock'), ('Expenses', Expense, Expense.spent_at, 'Expense')):
        query = model.query.filter_by(business_id=shop.id)
        if model in (Sale, Expense):
            query = query.filter(model.voided_at.is_(None))
        last = query.filter(stamp <= now).order_by(stamp.desc(), model.id.desc()).first()
        tiles.append(dict(label=label, count=query.count(), last=(
            f'{noun} #{last.id} on {local_time(getattr(last, stamp.key))[:10]}' if last else 'No records yet')))
    # Include only payments linked to this business and its owner. A foreign
    # subscription or incorrectly linked customer must never be represented here.
    payments = Payment.query.filter(Payment.business_id == shop.id, or_(
        and_(Payment.product == 'subscription', Payment.user_id == shop.user_id),
        and_(Payment.product == 'lifetime', or_(Payment.user_id == shop.user_id, Payment.user_id.is_(None)),
            func.lower(Payment.customer_email) == shop.owner.email.lower()))).order_by(
                Payment.created_at.desc(), Payment.id.desc()).limit(5).all()
    payments = [dict(id=p.id, date=local_time(p.paid_at or p.created_at),
        plan=(p.plan_code.title() if p.plan_code else 'Plan not recorded') if p.product == 'subscription' else 'Existing access',
        interval=p.billing_interval.title() if p.billing_interval else '', amount=money(p.amount_kobo,p.currency),
        status='Pending' if p.status=='initialized' else p.status.title(), reference=p.reference,
        short_reference=p.reference if len(p.reference)<=20 else p.reference[:10]+'…'+p.reference[-6:],
        can_view=p.product=='subscription') for p in payments]
    days = max(0, (now.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()-
        shop.created_at.replace(tzinfo=timezone.utc).astimezone(LAGOS).date()).days)
    return dict(owner=owner, status=status, team=team, tiles=tiles, payments=payments,
        plan=owner['plan'], interval=owner['interval'].title() if owner['interval'] else None,
        paid_until=local_time(sub.current_period_end)[:10] if sub and sub.current_period_end else None,
        renewal_stopped=owner['renewal_stopped'], created=local_time(shop.created_at)[:10],
        age='Today' if not days else f'{days} day'+('' if days==1 else 's')+' ago',
        activity=activity(shop, now), reasons=REACTIVATE_REASONS if shop.suspended_at else SUSPEND_REASONS)
