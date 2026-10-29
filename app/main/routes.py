from app.subscriptions.entitlements import selected_business
from datetime import datetime, time, timedelta
from decimal import Decimal
import re
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for, jsonify, abort, g
from flask_login import current_user, login_required
from sqlalchemy import func, case
from app import db
from app.models import Expense, Product, Restock, Sale, SaleItem
from app.main.onboarding import setup_progress
from app.main.reporting import financial_totals

main_bp = Blueprint("main", __name__)

def period_dates(args, *, now=None):
    today = (now or datetime.utcnow()).date()
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

def report_date_errors(args):
    """Validate the Reports form without changing Dashboard's period handling."""
    if args.get("period", "today") != "custom":
        return {}
    errors, dates = {}, {}
    for field, label in (("from", "From"), ("to", "To")):
        value = args.get(field, "")
        if not value.strip():
            errors[field] = f"Enter a {label} date."
        elif not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            errors[field] = f"Enter the {label} date in YYYY-MM-DD format."
        else:
            try:
                dates[field] = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                errors[field] = f"Enter a real calendar date for {label}."
    if errors:
        return errors
    if dates["to"] < dates["from"]:
        errors["to"] = "To date must be on or after the From date."
    elif (dates["to"] - dates["from"]).days > 366:
        errors["to"] = "Choose a date range of no more than 366 days."
    elif dates["to"] == datetime.max.date():
        errors["to"] = "To date must be earlier than 9999-12-31."
    return errors

def figures(b, start, end):
    totals = financial_totals(b.id, start, end)
    purchased = db.session.query(func.coalesce(func.sum(Restock.quantity * Restock.unit_cost), 0)).filter(
        Restock.business_id == b.id, Restock.received_at >= start, Restock.received_at < end).scalar()
    stock = db.session.query(func.count(Product.id),
        func.coalesce(func.sum(case((Product.stock_quantity == 0, 1), else_=0)), 0),
        func.coalesce(func.sum(case(((Product.stock_quantity > 0) &
            (Product.stock_quantity <= Product.minimum_stock_level), 1), else_=0)), 0),
        func.coalesce(func.sum(Product.stock_quantity * Product.buying_price), 0)
    ).filter(Product.business_id == b.id, Product.active.is_(True)).one()
    return {key: totals[key] for key in (
        "number_of_sales", "units_sold", "revenue", "cogs", "gross_profit", "expenses", "net_profit"
    )} | {"inventory_purchased": purchased, "product_count": stock[0],
           "out_stock_count": stock[1], "low_stock_count": stock[2], "inventory_value": stock[3]}

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

def public_plan_prices():
    from app.subscriptions.entitlements import PLANS
    return {plan: {interval: PLANS[(plan, interval)]['amount'] // 100
                   for interval in ('monthly', 'yearly')} for plan in ('basic', 'plus', 'pro')}


def sales_chart(per_day, week_start):
    # PostgreSQL returns date objects; SQLite returns ISO date strings.
    daily = {str(day): float(total) for day, total in per_day}
    chart = []
    for offset in range(7):
        day = (week_start + timedelta(days=offset)).date()
        chart.append({"date": day, "label": day.strftime("%a"), "total": daily.get(day.isoformat(), 0)})
    maximum = max([row["total"] for row in chart] + [1])
    for row in chart:
        row["height"] = max(3, round(row["total"] / maximum * 100))
    return chart


@main_bp.get("/")
def index():
    return redirect(url_for("main.dashboard")) if current_user.is_authenticated else render_template("home.html", plan_prices=public_plan_prices())

@main_bp.get("/dashboard")
@login_required
def dashboard():
    b = selected_business(current_user)
    from app.main.tour import tour_state
    tour = tour_state(current_user)
    if not b.has_write_access and not current_app.config.get("SUBSCRIPTIONS_ENABLED"):
        return render_template("dashboard_preview.html", business=b,
            lifetime_price=current_app.config["LIFETIME_PRICE_NAIRA"], onboarding_tour=tour)
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
    chart = sales_chart(per_day, week_start)
    return render_template("dashboard.html", business=b, metrics=metrics,
        setup=setup_progress(b), onboarding_tour=tour,
        low_stock_products=low, chart=chart, period=period,
        recent_transactions=recent_activity(b), top=top_products(b, start, end))

@main_bp.get("/reports")
@login_required
def reports():
    b = selected_business(current_user)
    if not b.has_write_access and not current_app.config.get("SUBSCRIPTIONS_ENABLED"):
        return redirect(url_for("subscriptions.index"))
    # Explicit dates take precedence on Reports, including without JavaScript.
    report_args = request.args.copy()
    if any(report_args.get(field, "") for field in ("from", "to")):
        report_args["period"] = "custom"
    errors = report_date_errors(report_args)
    if errors:
        return render_template("reports.html", business=b, period="custom",
            report_errors=errors), 400
    period, start, end = period_dates(report_args)
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


@main_bp.get('/onboarding/tour')
@login_required
def onboarding_tour_current():
    """Read only the authenticated account's state; never cache across devices."""
    from app.main.tour import tour_state
    response = jsonify(tour_state(current_user))
    response.headers['Cache-Control'] = 'no-store'
    return response


@main_bp.post('/onboarding/tour')
@login_required
def onboarding_tour_state():
    from app.main.tour import record_tour
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or set(data) != {'action','step','version'}
            or type(data['action']) is not str
            or data['action'] not in {'start','progress','skip','finish'}
            or type(data['step']) is not int or not 0 <= data['step'] < 7
            or type(data['version']) is not int or data['version'] < 0):
        abort(400)
    try:
        state = record_tour(current_user, data['action'], data['step'], data['version'])
        db.session.commit()
        return jsonify(state)
    except PermissionError:
        db.session.rollback()
        abort(403)
    except ValueError as error:
        db.session.rollback()
        return jsonify(error=str(error)), 409
    except Exception:
        db.session.rollback()
        raise


@main_bp.get("/insights")
@login_required
def insights():
    from app.main.insights import insights_context, build_insights
    now = datetime.utcnow()
    g.insights_subscription_context = insights_context(current_user, now=now)
    business = g.insights_subscription_context['subscription_business']
    if business is None:
        # No transaction/product reads for accounts without paid Plus/Pro access.
        return render_template("insights.html", insights_denied=True), 403
    args = request.args.copy()
    if any(args.get(field, "") for field in ("from", "to")):
        args["period"] = "custom"
    errors = report_date_errors(args)
    period = args.get("period", "today")
    start = end = None
    if not errors:
        period, start, end = period_dates(args, now=now)
        if start - datetime.min < end - start:
            errors["from"] = "Choose a later From date to allow an equal preceding comparison period."
    data = None if errors else build_insights(business.id, start, end, now=now)
    return render_template("insights.html", business=business, period=period,
                           report_errors=errors, insight=data), 400 if errors else 200


@main_bp.after_request
def protect_insights_response(response):
    # Includes authorization redirects/errors returned by application hooks.
    if request.endpoint == "main.insights":
        response.headers["Cache-Control"] = "private, no-store"
    return response
