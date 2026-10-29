"""Recorded financial totals shared by Reports, Dashboard and Insights.

UTC ranges are half-open. Sale-time prices/costs are authoritative; purchases
are not operating expenses. Never join expenses to sale items (fan-out).
"""
from decimal import Decimal
from sqlalchemy import case, distinct, func
from app import db
from app.models import Expense, Sale, SaleItem


def _sales_columns():
    return (
        func.count(distinct(Sale.id)),
        func.coalesce(func.sum(SaleItem.quantity), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0),
        func.coalesce(func.sum(case((SaleItem.unit_cost == 0, 1), else_=0)), 0),
    )


def _expense_columns():
    return func.coalesce(func.sum(Expense.amount), 0), func.count(Expense.id)


def _totals(sales, expenses):
    revenue, cogs = sales[2], sales[3]
    gross = revenue - cogs
    return dict(number_of_sales=sales[0], units_sold=sales[1], revenue=revenue,
                cogs=cogs, gross_profit=gross, expenses=expenses[0],
                net_profit=gross-expenses[0], zero_cost_lines=sales[4],
                expense_count=expenses[1])


def financial_totals(business_id, start, end):
    sales = db.session.query(*_sales_columns()).join(SaleItem).filter(
        Sale.business_id == business_id, Sale.voided_at.is_(None),
        Sale.sold_at >= start, Sale.sold_at < end).one()
    expenses = db.session.query(*_expense_columns()).filter(
        Expense.business_id == business_id, Expense.voided_at.is_(None),
        Expense.spent_at >= start, Expense.spent_at < end).one()
    return _totals(sales, expenses)


REPORTING_BATCH_SIZE = 200


def grouped_financial_totals(business_ids, start, end):
    """Two set-based reads per bounded batch, including zero-activity businesses.

    IDs must already be authorized by the caller. Pro currently permits at most
    five. Batching also bounds SQL parameters for other internal callers.
    """
    ids = tuple(dict.fromkeys(business_ids))
    zero = Decimal('0.00')
    result = {bid: _totals((0, 0, zero, zero, 0), (zero, 0)) for bid in ids}
    for offset in range(0, len(ids), REPORTING_BATCH_SIZE):
        batch = ids[offset:offset+REPORTING_BATCH_SIZE]
        sales = {row[0]: row[1:] for row in db.session.query(
            Sale.business_id, *_sales_columns()).join(SaleItem).filter(
                Sale.business_id.in_(batch), Sale.voided_at.is_(None),
                Sale.sold_at >= start, Sale.sold_at < end).group_by(Sale.business_id).all()}
        expenses = {row[0]: row[1:] for row in db.session.query(
            Expense.business_id, *_expense_columns()).filter(
                Expense.business_id.in_(batch), Expense.voided_at.is_(None),
                Expense.spent_at >= start, Expense.spent_at < end).group_by(Expense.business_id).all()}
        for bid in batch:
            result[bid] = _totals(sales.get(bid, (0, 0, zero, zero, 0)),
                                  expenses.get(bid, (zero, 0)))
    return result


def combined_financial_totals(rows):
    """Sum the exact displayed business records, without a second report query."""
    zero = Decimal('0.00')
    empty = _totals((0, 0, zero, zero, 0), (zero, 0))
    rows = tuple(rows)
    return {key: sum((row[key] for row in rows), value) for key, value in empty.items()}
