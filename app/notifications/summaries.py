"""Business-scoped daily restock and weekly performance email summaries."""
from datetime import datetime, timedelta
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Business, EmailOutbox, Expense, NotificationPreference, Product,
    Sale, SaleItem, User,
)
from app.notifications.staging_email_policy import policy, stamp_test_job


def _wat_window(now, days):
    """Return the previous WAT calendar window as naive UTC datetimes."""
    wat_now = now + timedelta(hours=1)
    end_local = wat_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if days == 7:
        end_local -= timedelta(days=end_local.weekday())
        start_local = end_local - timedelta(days=7)
    else:
        start_local = end_local - timedelta(days=1)
    return start_local - timedelta(hours=1), end_local - timedelta(hours=1), start_local.date()


def _money(value):
    return float(value or 0)


def _summary_payload(business, start, end, period_label, daily=False):
    sales_total, cost_total, units = (
        db.session.query(
            func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
            func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0),
            func.coalesce(func.sum(SaleItem.quantity), 0),
        )
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(
            Sale.business_id == business.id,
            Sale.voided_at.is_(None),
            Sale.sold_at >= start,
            Sale.sold_at < end,
        ).one()
    )
    expense_total = db.session.query(
        func.coalesce(func.sum(Expense.amount), 0)
    ).filter(
        Expense.business_id == business.id,
        Expense.voided_at.is_(None),
        Expense.spent_at >= start,
        Expense.spent_at < end,
    ).scalar()
    best = (
        db.session.query(
            Product.name, func.sum(SaleItem.quantity).label('units')
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .filter(
            Sale.business_id == business.id,
            Product.business_id == business.id,
            Sale.voided_at.is_(None),
            Sale.sold_at >= start,
            Sale.sold_at < end,
        )
        .group_by(Product.id, Product.name)
        .order_by(func.sum(SaleItem.quantity).desc(), Product.name.asc())
        .first()
    )
    low_query = Product.query.filter(
        Product.business_id == business.id,
        Product.active.is_(True),
        Product.stock_quantity <= Product.minimum_stock_level,
    )
    low_count = low_query.count()
    low_products = low_query.order_by(
        Product.stock_quantity.asc(), Product.name.asc()
    ).limit(10).all()
    revenue = _money(sales_total)
    cost_of_goods = _money(cost_total)
    expenses = _money(expense_total)
    return {
        'business_name': business.name,
        'period_label': period_label,
        'sales': revenue,
        'cost_of_goods': cost_of_goods,
        'expenses': expenses,
        'kept': revenue - cost_of_goods - expenses,
        'units_sold': int(units or 0),
        'best_seller': {'name': best[0], 'quantity': int(best[1])} if best else None,
        'low_stock_count': low_count,
        'low_stock_products': [
            {'name': p.name, 'quantity': p.stock_quantity, 'unit': p.unit}
            for p in low_products
        ],
        'daily': daily,
    }


def queue_due_summaries(now=None):
    """Queue opt-in summaries idempotently; no SMTP or external calls occur here."""
    now = now or datetime.utcnow()
    selected_policy = policy(now)
    if selected_policy.mode == 'blocked':
        return 0
    queued = 0
    businesses = (
        Business.query.join(User, User.id == Business.user_id)
        .filter(
            Business.suspended_at.is_(None),
            User.email_verified_at.isnot(None),
            User.suspended_at.is_(None),
            User.role == 'user',
            User.admin_enabled.is_(False),
            User.staff_only.is_(False),
        ).order_by(Business.id).all()
    )
    for business in businesses:
        preference = db.session.get(NotificationPreference, business.id)
        if preference is None:
            continue
        owner = db.session.get(User, business.user_id)
        if not owner or not owner.email or '@' not in owner.email:
            continue
        if selected_policy.mode == 'staging' and not selected_policy.allows(owner.email):
            continue
        for daily, field, days in (
            (True, 'daily_summary_email', 1),
            (False, 'weekly_summary_email', 7),
        ):
            if not getattr(preference, field, False):
                continue
            start, end, period_date = _wat_window(now, days)
            payload = _summary_payload(
                business, start, end,
                period_date.strftime('%d/%m/%Y'),
                daily=daily,
            )
            # Daily mail is an attention-only restock digest. Weekly mail is
            # sent only when the business had activity or stock needing attention.
            has_activity = bool(
                payload['sales'] or payload['expenses'] or payload['units_sold']
            )
            if daily and not payload['low_stock_count']:
                continue
            if not daily and not (has_activity or payload['low_stock_count']):
                continue
            kind = 'daily_summary' if daily else 'weekly_summary'
            event_key = f'{kind}:{business.id}:{period_date.isoformat()}'
            row = EmailOutbox(
                business_id=business.id,
                recipient_user_id=owner.id,
                recipient_email=owner.email,
                event_type=kind,
                event_key=event_key,
                subject=(
                    f"{payload['low_stock_count']} products need restocking today | StockBridge"
                    if daily else f"Your weekly StockBridge summary | {business.name}"
                )[:240],
                template_name='customer_summary',
                payload=payload,
                max_attempts=5,
            )
            stamp_test_job(row)
            try:
                with db.session.begin_nested():
                    db.session.add(row)
                    db.session.flush()
                queued += 1
            except IntegrityError:
                # A concurrent scheduler may have inserted this period key.
                continue
    db.session.commit()
    return queued


def register_commands(app):
    @app.cli.command('customer-email-summaries')
    def customer_email_summaries_command():
        if not app.config.get('CUSTOMER_SUMMARY_EMAILS_ENABLED'):
            raise __import__('click').ClickException(
                'Customer summary email delivery is disabled.'
            )
        count = queue_due_summaries()
        from app.notifications.email_outbox import dispatch_one
        sent = 0
        # The existing outbox lease, recipient guard and retry policy own delivery.
        for _ in range(100):
            result = dispatch_one()
            if result in ('idle', 'disabled', 'configuration_required'):
                break
            if result == 'sent':
                sent += 1
        __import__('click').echo(
            f'Customer summary emails queued: {count}; sent: {sent}'
        )
