from datetime import datetime, time, timedelta
from decimal import Decimal
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, case, distinct
from app import db
from app.models import Expense, Product, Restock, Sale, SaleItem

main_bp = Blueprint("main", __name__)

def period_dates(args):
    today = datetime.utcnow().date()
    period = args.get("period", "today")
    if period == "week":
        start, end = today - timedelta(days=today.weekday()), today + timedelta(days=1)
    elif period == "month":
        start, end = today.replace(day=1), today + timedelta(days=1)
    elif period == "custom":
        start = datetime.strptime(args["from"], "%Y-%m-%d").date()
        last = datetime.strptime(args["to"], "%Y-%m-%d").date()
        if last < start or (last - start).days > 366:
            raise ValueError("Choose a date range of up to one year.")
        end = last + timedelta(days=1)
    else:
        period = "today"
        start, end = today, today + timedelta(days=1)
    return period, datetime.combine(start, time.min), datetime.combine(end, time.min)

def figures(b, start, end):
    conditions = (Sale.business_id == b.id, Sale.voided_at.is_(None),
                  Sale.sold_at >= start, Sale.sold_at < end)
    sales = db.session.query(
        func.count(distinct(Sale.id)),
        func.coalesce(func.sum(SaleItem.quantity), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_cost), 0)
    ).join(SaleItem).filter(*conditions).one()
    expenses = db.session.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
        Expense.business_id == b.id, Expense.spent_at >= start, Expense.spent_at < end, Expense.voided_at.is_(None)).scalar()
    purchased = db.session.query(func.coalesce(func.sum(Restock.quantity * Restock.unit_cost), 0)).filter(
        Restock.business_id == b.id, Restock.received_at >= start, Restock.received_at < end).scalar()
    stock = db.session.query(func.count(Product.id),
        func.coalesce(func.sum(case((Product.stock_quantity == 0, 1), else_=0)), 0),
        func.coalesce(func.sum(case(((Product.stock_quantity > 0) &
            (Product.stock_quantity <= Product.minimum_stock_level), 1), else_=0)), 0),
        func.coalesce(func.sum(Product.stock_quantity * Product.buying_price), 0)
    ).filter(Product.business_id == b.id, Product.active.is_(True)).one()
    revenue, cogs = sales[2], sales[3]
    gross = revenue - cogs
    return {"number_of_sales": sales[0], "units_sold": sales[1], "revenue": revenue,
            "cogs": cogs, "gross_profit": gross, "expenses": expenses,
            "net_profit": gross - expenses, "inventory_purchased": purchased,
            "product_count": stock[0], "out_stock_count": stock[1],
            "low_stock_count": stock[2], "inventory_value": stock[3]}

def top_products(b, start, end, order="units"):
    units = func.sum(SaleItem.quantity).label("units")
    revenue = func.sum(SaleItem.quantity * SaleItem.unit_price).label("revenue")
    rows = db.session.query(Product, units, revenue).join(
        SaleItem, SaleItem.product_id == Product.id).join(Sale).filter(
        Sale.business_id == b.id, Sale.voided_at.is_(None),
        Sale.sold_at >= start, Sale.sold_at < end).group_by(Product.id)
    return rows.order_by(revenue.desc() if order == "revenue" else units.desc()).limit(5).all()

def recent_activity(b):
    sales = Sale.query.filter(Sale.business_id == b.id, Sale.voided_at.is_(None)).order_by(Sale.sold_at.desc()).limit(5).all()
    receipts = Restock.query.filter_by(business_id=b.id).order_by(Restock.received_at.desc()).limit(5).all()
    expenses = Expense.query.filter(Expense.business_id == b.id, Expense.voided_at.is_(None)).order_by(Expense.spent_at.desc()).limit(5).all()
    rows = ([{"title": f"Sale · {s.units} units", "date": s.sold_at, "amount": f"+₦{s.total:,.2f}"} for s in sales] +
            [{"title": f"Restock · {r.product.name} +{r.quantity}", "date": r.received_at,
              "amount": f"₦{r.total:,.2f}"} for r in receipts] +
            [{"title": f"Expense · {e.description}", "date": e.spent_at,
              "amount": f"-₦{e.amount:,.2f}"} for e in expenses])
    return sorted(rows, key=lambda row: row["date"], reverse=True)[:8]

@main_bp.get("/")
def index():
    return redirect(url_for("main.dashboard")) if current_user.is_authenticated else redirect(url_for("auth.login"))

@main_bp.get("/dashboard")
@login_required
def dashboard():
    b = current_user.businesses[0]
    try:
        period, start, end = period_dates(request.args)
    except (ValueError, KeyError):
        flash("Choose a valid reporting period.", "error")
        return redirect(url_for("main.dashboard"))
    metrics = figures(b, start, end)
    low = Product.query.filter(Product.business_id == b.id, Product.active.is_(True),
        Product.stock_quantity <= Product.minimum_stock_level).order_by(Product.stock_quantity.asc()).limit(5).all()
    # Seven daily sums, calculated in SQL rather than loading every sale.
    week_start = datetime.combine(datetime.utcnow().date() - timedelta(days=6), time.min)
    per_day = db.session.query(func.date(Sale.sold_at),
        func.sum(SaleItem.quantity * SaleItem.unit_price)).join(SaleItem).filter(
        Sale.business_id == b.id, Sale.voided_at.is_(None), Sale.sold_at >= week_start
    ).group_by(func.date(Sale.sold_at)).all()
    daily = {day: float(total) for day, total in per_day}
    chart = []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        chart.append({"label": day.strftime("%a"), "total": daily.get(day.isoformat(), 0)})
    maximum = max([row["total"] for row in chart] + [1])
    for row in chart:
        row["height"] = max(3, round(row["total"] / maximum * 100))
    return render_template("dashboard.html", business=b, metrics=metrics,
        low_stock_products=low, chart=chart, period=period,
        recent_transactions=recent_activity(b), top=top_products(b, start, end))

@main_bp.get("/reports")
@login_required
def reports():
    b = current_user.businesses[0]
    if not b.has_write_access:
        return redirect(url_for("subscriptions.index"))
    try:
        period, start, end = period_dates(request.args)
    except (ValueError, KeyError):
        flash("Choose a valid reporting period.", "error")
        return redirect(url_for("main.reports"))
    metrics = figures(b, start, end)
    cutoff = datetime.utcnow() - timedelta(days=30)
    slow = Product.query.filter(Product.business_id == b.id, Product.active.is_(True),
        Product.stock_quantity > 0, Product.created_at < cutoff,
        ~Product.id.in_(db.session.query(SaleItem.product_id).join(Sale).filter(
            Sale.business_id == b.id, Sale.voided_at.is_(None), Sale.sold_at >= cutoff))
    ).order_by(Product.stock_quantity.desc()).limit(10).all()
    return render_template("reports.html", business=b, metrics=metrics, period=period,
        start=start, end=end-timedelta(days=1), slow=slow,
        by_units=top_products(b, start, end), by_revenue=top_products(b, start, end, "revenue"))
