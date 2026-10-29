"""Deterministic selected-business explanations; no forecasts or provider calls."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from flask import current_app, session
from app import db
from app.models import Business, Notification, Product, Sale, SaleItem
from app.main.reporting import financial_totals
from app.subscriptions.entitlements import EntitlementSnapshot, access_label


def insights_context(user, *, now, plans=('plus', 'pro')):
    """Resolve one request-local business and presentation context, without writes.

    Match selected_business's permitted fallback order: session, primary, then
    first permitted owned business. Never use its read-only/locked fallback.
    The route and shared shell must consume this same context, not resolve again.
    """
    context = dict(subscription_business=None, business_choices=[],
                   business_switch_ids=set(), business_selection_required=False,
                   business_can_add=False, billing_label='Business Insights unavailable')
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        return context
    snapshot = EntitlementSnapshot([user], now=now)
    access = snapshot.access(user)
    from app.businesses.service import selection_required
    context.update(billing_access=access, billing_label=access_label(user, access=access),
                   business_selection_required=selection_required(user, access=access))
    account = snapshot.accounts.get(user.id)
    if access.kind == 'trial':
        context['billing_trial_end'] = account.trial_ends_at
    if not (access.can_write and access.subscription
            and access.subscription.plan_code in plans):
        return context
    businesses = Business.query.filter_by(user_id=user.id).order_by(Business.id).all()
    allowed = [b for b in businesses if snapshot.access(user, business=b).can_write]
    active = next((b for b in allowed if b.id == session.get('active_business_id')), None)
    primary = next((b for b in allowed if account and b.id == account.primary_business_id), None)
    business = active or primary or next(iter(allowed), None)
    context.update(subscription_business=business, business_choices=allowed,
                   business_switch_ids={b.id for b in allowed},
                   business_can_add=bool(business and len(businesses) < access.business_limit))
    if business:
        context.update(billing_access=snapshot.access(user, business=business),
                       notification_unread=Notification.query.filter_by(
                           user_id=user.id, business_id=business.id, read_at=None).count())
    return context


def insights_business(user, *, now):
    """Business-only convenience API; the page itself reuses the whole context."""
    return insights_context(user, now=now)['subscription_business']


def comparison(current, previous, *, incomplete=False, uncertain_cost=False):
    delta = current - previous
    if incomplete:
        return dict(delta=delta, percent=None, state='incomplete')
    if uncertain_cost:
        return dict(delta=delta, percent=None, state='cost-review')
    if previous <= 0:
        return dict(delta=delta, percent=None, state='no-baseline' if previous == 0 else 'negative-baseline')
    return dict(delta=delta, percent=delta / previous * Decimal(100), state='comparable')


def slow_stock(business_id, *, now):
    """Established stock without sales in 30 complete UTC calendar days.

    Independent of the selected financial period. Today's sales are deliberately
    outside this window. Return at most 10; stable order keeps the list bounded.
    """
    end = datetime.combine(now.date(), time.min)
    start = end - timedelta(days=30)
    sold = db.session.query(SaleItem.id).join(Sale).filter(
        SaleItem.product_id == Product.id, Sale.business_id == business_id,
        Sale.voided_at.is_(None), Sale.sold_at >= start, Sale.sold_at < end).exists()
    products = Product.query.filter(
        Product.business_id == business_id, Product.active.is_(True),
        Product.stock_quantity > 0, Product.created_at <= start, ~sold
    ).order_by(Product.stock_quantity.desc(), Product.id).limit(10).all()
    return dict(start=start, end=end, products=products)


def build_insights(business_id, start, end, *, now):
    previous_start = start - (end - start)
    current = financial_totals(business_id, start, end)
    previous = financial_totals(business_id, previous_start, start)
    incomplete = end > datetime.combine(now.date(), time.min)
    uncertain_cost = bool(current['zero_cost_lines'] or previous['zero_cost_lines'])
    trends = {key: comparison(current[key], previous[key], incomplete=incomplete,
                              uncertain_cost=uncertain_cost and key in ('gross_profit', 'net_profit'))
              for key in ('revenue', 'gross_profit', 'expenses')}
    explanations = []
    if not current['number_of_sales'] and not current['expense_count']:
        explanations.append('No sales or operating expenses were recorded in the selected period. Check that records are up to date before drawing conclusions.')
    if incomplete:
        explanations.append('This period includes today or future dates. Totals contain recorded activity only; directional comparisons are withheld until all selected UTC days are complete.')
    else:
        sales_trend = trends['revenue']
        if sales_trend['state'] == 'comparable' and sales_trend['delta'] < 0:
            explanations.append('Recorded revenue is lower than in the preceding period. Review sales records and product demand before changing prices or promotions; these totals do not establish a cause.')
        elif sales_trend['state'] == 'comparable' and sales_trend['delta'] > 0:
            explanations.append('Recorded revenue is higher than in the preceding period. Check saved product costs and recorded expenses before treating the increase as improved profitability.')
        if trends['expenses']['state'] == 'comparable' and trends['expenses']['delta'] > 0:
            explanations.append('Recorded operating expenses increased. Review expense categories and receipts for recurring or one-off costs.')
        profit_trend = trends['gross_profit']
        if profit_trend['state'] == 'comparable' and profit_trend['delta'] < 0:
            explanations.append('Estimated gross profit decreased. Review sale-time prices and saved costs; do not substitute current product costs into historical sales.')
    if uncertain_cost:
        explanations.append('Some sale lines have a saved unit cost of zero in either period. This may be intentional or incomplete recording. Review those costs; profit direction is withheld and the estimates retain the saved values.')
    return dict(start=start, end=end, last=end-timedelta(days=1),
                previous_start=previous_start, previous_last=start-timedelta(days=1),
                current=current, previous=previous, trends=trends,
                incomplete=incomplete, explanations=explanations,
                stock=slow_stock(business_id, now=now))
