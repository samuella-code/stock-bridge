from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, update
from app import db
from app.models import Product, Sale, SaleItem, StockMovement, Restock

restocking_bp = Blueprint("restocking", __name__, url_prefix="/restocking")

def recommendations(b):
    since = datetime.utcnow() - timedelta(days=30)
    now = datetime.utcnow()
    sales = db.session.query(SaleItem.product_id, func.sum(SaleItem.quantity),
        func.min(Sale.sold_at)).join(Sale).filter(Sale.business_id == b.id,
        Sale.voided_at.is_(None), Sale.sold_at >= since).group_by(SaleItem.product_id).all()
    observed = {row[0]: (row[1], row[2]) for row in sales}
    rows = []
    for p in Product.query.filter_by(business_id=b.id, active=True).order_by(Product.name).all():
        figures = observed.get(p.id)
        # Recent history is an estimate, not a promise of future demand.
        days = min(30, max(1, (now - max(p.created_at, since)).days + 1))
        avg = float(figures[0]) / days if figures else None
        remaining = p.stock_quantity / avg if avg else None
        point = avg * p.supplier_lead_time + p.safety_stock if avg else None
        priority = ("Urgent" if avg and p.stock_quantity <= point else
                    "Watch" if p.is_low_stock else "Healthy")
        rows.append({"product": p, "avg": avg, "days": remaining, "point": point,
                     "suggested": max(0, round(point + avg*7 - p.stock_quantity)) if avg else None,
                     "priority": priority})
    return sorted(rows, key=lambda x: {"Urgent":0,"Watch":1,"Healthy":2}[x["priority"]])

@restocking_bp.get("/")
@login_required
def index():
    b = current_user.businesses[0]
    restocks = Restock.query.filter_by(business_id=b.id).order_by(Restock.received_at.desc(), Restock.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    return render_template("restocking/index.html", business=b, recommendations=recommendations(b), restocks=restocks)

@restocking_bp.post("/<int:product_id>/settings")
@login_required
def settings(product_id):
    b = current_user.businesses[0]
    p = db.session.get(Product, product_id)
    if not p or p.business_id != b.id: abort(404)
    try:
        lead = int(request.form["supplier_lead_time"])
        safety = int(request.form["safety_stock"])
        minimum = int(request.form["minimum_stock_level"])
        if min(lead, safety, minimum) < 0: raise ValueError
    except (ValueError, KeyError):
        flash("Restocking settings must be whole numbers of zero or more.", "error")
    else:
        p.supplier_lead_time, p.safety_stock, p.minimum_stock_level = lead, safety, minimum
        db.session.commit()
        flash(f"Restocking settings updated for {p.name}.", "success")
    return redirect(url_for("restocking.index"))

@restocking_bp.post("/receive")
@login_required
def receive():
    b = current_user.businesses[0]
    ids = request.form.getlist("product_id")
    quantities = request.form.getlist("quantity")
    costs = request.form.getlist("unit_cost")
    if not ids or len(ids) != len(quantities) or len(ids) != len(costs) or len(ids) > 100:
        flash("Add at least one valid product to restock.", "error")
        return redirect(url_for("restocking.index"))
    try:
        parsed = []
        seen = set()
        for raw_id, raw_qty, raw_cost in zip(ids, quantities, costs):
            pid, qty, cost = int(raw_id), int(raw_qty), Decimal(raw_cost)
            if pid in seen or qty < 1 or not cost.is_finite() or cost < 0: raise ValueError
            seen.add(pid)
            parsed.append((pid, qty, cost))
    except (ValueError, InvalidOperation):
        flash("Check the product, quantity and cost. Each product should appear once.", "error")
        return redirect(url_for("restocking.index"))
    products = {p.id: p for p in Product.query.filter(Product.business_id == b.id,
        Product.active.is_(True), Product.id.in_(seen)).all()}
    if len(products) != len(parsed): abort(404)
    supplier = request.form.get("supplier", "").strip()[:140]
    note = request.form.get("note", "").strip()[:500]
    batch = str(uuid4())
    try:
        for pid, qty, cost in parsed:
            product = products[pid]
            receipt = Restock(business_id=b.id, product_id=pid, quantity=qty,
                unit_cost=cost, supplier=supplier, note=note, batch_id=batch)
            db.session.add(receipt)
            db.session.flush()
            db.session.execute(update(Product).where(Product.id == pid, Product.business_id == b.id)
                .values(stock_quantity=Product.stock_quantity + qty,
                        buying_price=cost, updated_at=datetime.utcnow(),
                        supplier_name=supplier or Product.supplier_name))
            db.session.add(StockMovement(business_id=b.id, product_id=pid, kind="restock",
                quantity_change=qty, restock_id=receipt.id, occurred_at=receipt.received_at))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash("Received stock and updated inventory.", "success")
    return redirect(url_for("restocking.index"))
