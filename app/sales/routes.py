from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import update, func
from sqlalchemy.orm import selectinload
from app import db
from app.models import Product, Sale, SaleItem, StockMovement
from app.admin.routes import log

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")
PAYMENT_METHODS = ("Cash", "Bank Transfer", "POS", "Other")

def business():
    return current_user.businesses[0]

@sales_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    b = business()
    if request.method == "POST":
        ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")
        prices = request.form.getlist("unit_price") or ([""] if len(ids) == 1 else [])
        if not (ids and len(ids) == len(quantities) == len(prices)) or len(ids) > 100:
            flash("Add at least one valid sale item.", "error")
            return redirect(url_for("sales.index"))
        method = request.form.get("payment_method", "Other")
        if method not in PAYMENT_METHODS:
            flash("Choose a valid payment method.", "error")
            return redirect(url_for("sales.index"))
        try:
            sold_at = datetime.strptime(request.form["sold_at"], "%Y-%m-%d") if request.form.get("sold_at") else datetime.utcnow()
            parsed = []
            seen = set()
            for raw_id, raw_qty, raw_price in zip(ids, quantities, prices):
                pid, qty = int(raw_id), int(raw_qty)
                price = Decimal(raw_price) if raw_price.strip() else None
                if pid in seen or qty < 1 or (price is not None and (not price.is_finite() or price < 0)):
                    raise ValueError
                seen.add(pid)
                parsed.append((pid, qty, price))
        except (KeyError, ValueError, InvalidOperation):
            flash("Check products, quantities, prices and date. Each product should appear once.", "error")
            return redirect(url_for("sales.index"))
        products = {p.id: p for p in Product.query.filter(Product.business_id == b.id, Product.id.in_(seen), Product.active.is_(True)).all()}
        if len(products) != len(parsed):
            abort(404)
        for pid, qty, _ in parsed:
            if products[pid].stock_quantity < qty:
                flash(f"Only {products[pid].stock_quantity} units of {products[pid].name} are currently available.", "error")
                return redirect(url_for("sales.index"))
        try:
            sale = Sale(business_id=b.id, sold_at=sold_at, payment_method=method,
                        note=request.form.get("note", "").strip()[:500])
            db.session.add(sale)
            db.session.flush()
            for pid, qty, price in parsed:
                product = products[pid]
                result = db.session.execute(
                    update(Product).where(Product.id == pid, Product.business_id == b.id,
                        Product.active.is_(True), Product.stock_quantity >= qty)
                    .values(stock_quantity=Product.stock_quantity - qty,
                            updated_at=datetime.utcnow())
                    .execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    db.session.rollback()
                    flash(f"Stock changed while recording {product.name}. Please review the sale.", "error")
                    return redirect(url_for("sales.index"))
                db.session.add(SaleItem(sale_id=sale.id, product_id=pid, quantity=qty,
                                        unit_price=product.selling_price if price is None else price,
                                        unit_cost=product.buying_price))
                db.session.add(StockMovement(business_id=b.id, product_id=pid, kind="sale",
                    quantity_change=-qty, sale_id=sale.id, occurred_at=sold_at))
            log("SALE_CREATED", f"Sale {sale.id} created with {len(parsed)} items.", actor=current_user, business_id=b.id)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        flash("Sale recorded and stock updated.", "success")
        return redirect(url_for("sales.index"))
    products = Product.query.filter_by(business_id=b.id, active=True).order_by(Product.name).all()
    sales = (Sale.query.options(selectinload(Sale.items).selectinload(SaleItem.product))
             .filter_by(business_id=b.id).order_by(Sale.sold_at.desc(), Sale.id.desc())
             .paginate(page=request.args.get("page", 1, type=int), per_page=20, error_out=False))
    totals = db.session.query(
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
        func.coalesce(func.sum(SaleItem.quantity * (SaleItem.unit_price - SaleItem.unit_cost)), 0)
    ).join(Sale).filter(Sale.business_id == b.id, Sale.voided_at.is_(None)).one()
    return render_template("sales/index.html", business=b, products=products, sales=sales,
                           total=totals[0], profit=totals[1], payment_methods=PAYMENT_METHODS)

@sales_bp.post("/<int:sale_id>/delete")
@login_required
def delete(sale_id):
    # Keep the historic route, but void the transaction with an audit trail.
    b = business()
    sale = db.session.get(Sale, sale_id)
    if not sale or sale.business_id != b.id:
        abort(404)
    if sale.voided_at:
        flash("This sale is already voided.", "warning")
        return redirect(url_for("sales.index"))
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("Give a reason before voiding a sale.", "error")
        return redirect(url_for("sales.index"))
    now = datetime.utcnow()
    for item in sale.items:
        db.session.execute(update(Product).where(Product.id == item.product_id, Product.business_id == b.id)
                           .values(stock_quantity=Product.stock_quantity + item.quantity))
        db.session.add(StockMovement(business_id=b.id, product_id=item.product_id,
            kind="void", quantity_change=item.quantity, reason=reason[:300], sale_id=sale.id,
            occurred_at=now))
    sale.voided_at = now
    sale.void_reason = reason[:300]
    db.session.commit()
    flash("Sale voided and stock restored.", "success")
    return redirect(url_for("sales.index"))
