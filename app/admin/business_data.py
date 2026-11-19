"""Read-only business inventory, using the existing entitlement evaluator."""
from datetime import timedelta

from flask import current_app
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import joinedload

from app import db
from app.admin.customer_data import CustomerRows, local_time, masked_email
from app.models import (AuditLog, Business, Expense, Product, Restock, Sale,
                        StaffMembership, StaffMembershipBusiness, StaffRole,
                        StockMovement, User)
from app.subscriptions.entitlements import EntitlementSnapshot

FILTERS = ('all', 'used', 'never-used')
# An admin opening/inspecting a business is deliberately not business use.
WORK_ACTIONS = ('PRODUCT_CREATED', 'PRODUCT_IMPORT', 'PRODUCT_UPDATED',
                'SALE_CREATED', 'SALE_VOIDED', 'RESTOCK_CREATED',
                'EXPENSE_CREATED', 'EXPENSE_VOIDED', 'STOCK_ADJUSTED')


def query(q='', status='all', *, now):
    rows = Business.query.join(User, User.id == Business.user_id).filter(
        User.role == 'user', User.admin_enabled.is_(False), User.staff_only.is_(False))
    if q:
        rows = rows.filter(or_(Business.name.icontains(q, autoescape=True),
            User.email.icontains(q, autoescape=True), User.full_name.icontains(q, autoescape=True)))
    if status != 'all':
        events = []
        for model, stamp in ((Product, Product.updated_at), (Sale, Sale.sold_at),
                (Restock, Restock.received_at), (Expense, Expense.spent_at),
                (StockMovement, StockMovement.occurred_at), (AuditLog, AuditLog.created_at)):
            event = db.session.query(model.id).filter(model.business_id == Business.id)
            if model is AuditLog:
                event = event.filter(AuditLog.action.in_(WORK_ACTIONS))
            if status == 'used':
                event = event.filter(stamp >= now - timedelta(days=7), stamp <= now)
            events.append(event.exists())
        used = or_(*events)
        rows = rows.filter(used if status == 'used' else ~used)
    return rows.options(joinedload(Business.owner)).order_by(Business.created_at.desc(), Business.id.desc())


def marked_active(shop, owner, now):
    return bool(not (shop.suspended_at or owner.suspended_at)
                and shop.subscription_status == 'active'
                and (shop.subscription_ends_at is None or shop.subscription_ends_at > now))


def writable(shop, snapshot):
    owner = snapshot.users[shop.user_id]
    if current_app.config['SUBSCRIPTIONS_ENABLED']:
        return snapshot.access(owner, business=shop).can_write
    return marked_active(shop, owner, snapshot.now)


def summary(now):
    """Same business access definition as the dashboard; bounded batches."""
    result = dict(total=0, active=0, marked_active=0, disagreements=0)
    last_id = 0
    while True:
        batch = query(now=now).order_by(None).filter(Business.id > last_id).order_by(Business.id).limit(100).all()
        if not batch:
            return result
        snapshot = EntitlementSnapshot({b.user_id: b.owner for b in batch}.values(), now=now)
        for shop in batch:
            active = writable(shop, snapshot)
            marker = marked_active(shop, shop.owner, now)
            result['total'] += 1
            result['active'] += int(active)
            result['marked_active'] += int(marker)
            result['disagreements'] += int(active != marker)
        last_id = batch[-1].id


def serialize(shops, now):
    shops = list(shops)
    ids = [b.id for b in shops]
    owners = {b.user_id: b.owner for b in shops}
    customers = CustomerRows(owners.values(), now)
    plans = {uid: customers.serialize(owner)['plan'] for uid, owner in owners.items()}
    ranked = db.session.query(Sale.id.label('sale_id'), Sale.business_id,
        Sale.sold_at, func.row_number().over(partition_by=Sale.business_id,
            order_by=(Sale.sold_at.desc(), Sale.id.desc())).label('position')).filter(
        Sale.business_id.in_(ids), Sale.voided_at.is_(None), Sale.sold_at <= now).subquery()
    latest = {r.business_id: r for r in db.session.query(ranked).filter(ranked.c.position == 1)}
    team = {bid: [] for bid in ids}
    members = db.session.query(StaffMembership, StaffMembershipBusiness, User, StaffRole).join(
        StaffMembershipBusiness, StaffMembershipBusiness.membership_id == StaffMembership.id).join(
        Business, Business.id == StaffMembershipBusiness.business_id).join(
        User, User.id == StaffMembership.user_id).join(StaffRole, and_(
            StaffRole.owner_id == StaffMembership.owner_id, StaffRole.key == StaffMembership.role)).filter(
        Business.id.in_(ids), Business.user_id == StaffMembership.owner_id,
        StaffMembership.state == 'active', User.role == 'user', User.admin_enabled.is_(False)).order_by(
        User.full_name, User.id)
    for member, assignment, employee, role in members:
        team[assignment.business_id].append(dict(id=employee.id, email=masked_email(employee.email), role=role.name))
    result = []
    for shop in shops:
        sale = latest.get(shop.id)
        active = writable(shop, customers.snapshot)
        result.append(dict(id=shop.id, name=shop.name, owner_id=shop.user_id,
            owner_email=masked_email(shop.owner.email), plan=plans[shop.user_id],
            status='Suspended' if shop.suspended_at or shop.owner.suspended_at else 'Active' if active else 'Read-only',
            created=local_time(shop.created_at), last_sale_id=sale.sale_id if sale else None,
            last_sale=local_time(sale.sold_at) if sale else None, team=team[shop.id]))
    return result
