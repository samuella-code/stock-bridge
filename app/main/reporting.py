"""Recorded financial totals shared by Reports, Dashboard and Insights.

UTC ranges are half-open. Sale-time prices/costs are authoritative; purchases
are not operating expenses. Never join expenses to sale items (fan-out).
"""
from sqlalchemy import case, distinct, func
from app import db
from app.models import Expense, Sale, SaleItem


def financial_totals(business_id, start, end):
    sales = db.session.query(
        func.count(distinct(Sale.id)),
        func.coalesce(func.sum(SaleItem.quantity), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0),
        func.coalesce(func.sum(case((SaleItem.unit_cost == 0, 1), else_=0)), 0),
    ).join(SaleItem).filter(
        Sale.business_id == business_id, Sale.voided_at.is_(None),
        Sale.sold_at >= start, Sale.sold_at < end).one()
    expenses = db.session.query(
        func.coalesce(func.sum(Expense.amount), 0), func.count(Expense.id)
    ).filter(Expense.business_id == business_id, Expense.voided_at.is_(None),
             Expense.spent_at >= start, Expense.spent_at < end).one()
    revenue, cogs = sales[2], sales[3]
    gross = revenue - cogs
    return dict(number_of_sales=sales[0], units_sold=sales[1], revenue=revenue,
                cogs=cogs, gross_profit=gross, expenses=expenses[0],
                net_profit=gross-expenses[0], zero_cost_lines=sales[4],
                expense_count=expenses[1])
