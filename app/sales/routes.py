from app.subscriptions.entitlements import selected_business
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import update, func
from sqlalchemy.orm import selectinload
from app import db
from app.models import Product, Sale, SaleItem, StockMovement
from app.admin.routes import log
from app.transaction_submissions import issue_token, validate_token, claim, complete, SubmissionError, SubmissionConflict, SubmissionIncomplete

sales_bp = Blueprint("sales", __name__, url_prefix="/sales")
PAYMENT_METHODS = ("Cash", "Bank Transfer", "POS", "Other")

def business():
    return selected_business(current_user)

@sales_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    b = business()
    token = request.form.get('submission_token', '') if request.method == 'POST' else issue_token(current_user.id, b.id, 'sale')
    def show_form(status=200):
        has_products = Product.query.filter_by(business_id=b.id, active=True).first() is not None
        sales = (Sale.query.options(selectinload(Sale.items).selectinload(SaleItem.product))
                 .filter_by(business_id=b.id).order_by(Sale.sold_at.desc(), Sale.id.desc())
                 .paginate(page=request.args.get('page', 1, type=int), per_page=20, error_out=False))
        totals = db.session.query(func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price), 0),
            func.coalesce(func.sum(SaleItem.quantity * (SaleItem.unit_price - SaleItem.unit_cost)), 0)
            ).join(Sale).filter(Sale.business_id == b.id, Sale.voided_at.is_(None)).one()
        submitted = []
        if request.method == 'POST':
            for raw_id, qty, price in zip(request.form.getlist('product_id'), request.form.getlist('quantity'), request.form.getlist('unit_price')):
                try:
                    product = Product.query.filter_by(id=int(raw_id), business_id=b.id).first()
                except ValueError:
                    product = None
                if product:
                    submitted.append(dict(id=product.id, name=product.name, stock=product.stock_quantity,
                        unit=product.unit, price=price, quantity=qty))
        return render_template('sales/index.html', business=b, has_products=has_products, sales=sales,
            total=totals[0], profit=totals[1], payment_methods=PAYMENT_METHODS, submission_token=token, submitted_items=submitted), status
    if request.method == "POST":
        try:
            validate_token(token, current_user.id, b.id, 'sale')
        except SubmissionError:
            flash('Submission token is invalid or expired. Check sales history before refreshing the form or recording another sale.', 'error')
            return show_form(400)
        ids = request.form.getlist("product_id")
        quantities = request.form.getlist("quantity")
        prices = request.form.getlist("unit_price") or ([""] if len(ids) == 1 else [])
        if not (ids and len(ids) == len(quantities) == len(prices)) or len(ids) > 100:
            flash("Add at least one valid sale item.", "error")
            return show_form()
        method = request.form.get("payment_method", "Other")
        if method not in PAYMENT_METHODS:
            flash("Choose a valid payment method.", "error")
            return show_form()
        try:
            sold_at = datetime.strptime(request.form["sold_at"], "%Y-%m-%d") if request.form.get("sold_at") else datetime.utcnow()
            parsed = []
            seen = set()
            for raw_id, raw_qty, raw_price in zip(ids, quantities, prices):
                pid, qty = int(raw_id), int(raw_qty)
                price = Decimal(raw_price) if raw_price.strip() else None
                if pid in seen or not 1 <= qty <= 2147483647 or (price is not None and (not price.is_finite() or price < 0 or price > Decimal('9999999999.99') or price.as_tuple().exponent < -2)):
                    raise ValueError
                seen.add(pid)
                parsed.append((pid, qty, price))
        except (KeyError, ValueError, InvalidOperation):
            flash("Check products, quantities, prices and date. Each product should appear once.", "error")
            return show_form()
        note = request.form.get("note", "").strip()[:500]
        intent = {'items': [{'product_id': pid, 'quantity': qty,
                    'price': {'default': True} if price is None else {'explicit': format(price, '.2f')}}
                    for pid, qty, price in sorted(parsed)], 'payment_method': method,
                  'date': request.form.get('sold_at', '') or None, 'note': note}
        try:
            receipt, replay = claim(token, current_user.id, b.id, 'sale', intent)
        except SubmissionIncomplete:
            db.session.rollback()
            flash('This submission has no confirmed completed result. Check sales history before submitting again.', 'error')
            return show_form(409)
        except SubmissionConflict:
            db.session.rollback()
            flash('This submission was already used with different contents. Check sales history.', 'error')
            return show_form(409)
        except Exception:
            db.session.rollback()
            raise
        if replay:
            original = Sale.query.filter_by(id=int(receipt.result_reference.split(':')[1]), business_id=b.id).first()
            if not original:
                db.session.rollback()
                abort(409, description='Original sale is unavailable. Check sales history.')
            original_id = original.id
            db.session.rollback()
            flash(f'Sale {original_id} was already recorded. Stock was not changed again.', 'success')
            return redirect(url_for('sales.index'))
        try:
            products = {p.id: p for p in Product.query.filter(Product.business_id == b.id, Product.id.in_(seen), Product.active.is_(True)).order_by(Product.id).with_for_update().all()}
        except Exception:
            db.session.rollback()
            raise
        if len(products) != len(parsed):
            db.session.rollback()
            abort(404)
        for pid, qty, _ in parsed:
            if products[pid].stock_quantity < qty:
                available, name = products[pid].stock_quantity, products[pid].name
                db.session.rollback()
                flash(f"Only {available} units of {name} are currently available.", "error")
                return show_form()
        try:
            sale = Sale(business_id=b.id, sold_at=sold_at, payment_method=method,
                        note=request.form.get("note", "").strip()[:500])
            db.session.add(sale)
            db.session.flush()
            total = Decimal('0')
            for pid, qty, price in sorted(parsed):
                product = products[pid]
                before = product.stock_quantity
                result = db.session.execute(
                    update(Product).where(Product.id == pid, Product.business_id == b.id,
                        Product.active.is_(True), Product.stock_quantity >= qty)
                    .values(stock_quantity=Product.stock_quantity - qty,
                            updated_at=datetime.utcnow())
                    .execution_options(synchronize_session=False))
                if result.rowcount != 1:
                    db.session.rollback()
                    flash(f"Stock changed while recording {product.name}. Please review the sale.", "error")
                    return show_form()
                db.session.add(SaleItem(sale_id=sale.id, product_id=pid, quantity=qty,
                                        unit_price=product.selling_price if price is None else price,
                                        unit_cost=product.buying_price))
                db.session.add(StockMovement(business_id=b.id, product_id=pid, kind="sale",
                    quantity_change=-qty, sale_id=sale.id, occurred_at=sold_at))
                from app.notifications.service import stock_transition
                stock_transition(product, before, before - qty, f'sale:{sale.id}:product:{pid}')
                total += qty * (product.selling_price if price is None else price)
            from app.notifications.service import notify
            notify(b.id, 'sale_recorded', 'Sale recorded', f'₦{total:,.2f} sale recorded successfully.', f'sale:{sale.id}:created', sale.id)
            log("SALE_CREATED", f"Sale {sale.id} created with {len(parsed)} items.", actor=current_user, business_id=b.id)
            complete(receipt, f'sale:{sale.id}')
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        flash("Sale recorded and stock updated.", "success")
        return redirect(url_for("sales.index"))
    return show_form()

@sales_bp.post("/<int:sale_id>/delete")
@login_required
def delete(sale_id):
    # Keep the historic route, but void the transaction with an audit trail.
    b = business()
    try:
        # Refresh any identity-map copy after acquiring the lock: another request
        # may have committed a void while this request waited.
        sale = (Sale.query.filter_by(id=sale_id, business_id=b.id)
                .populate_existing().with_for_update().first())
        if not sale:
            abort(404)
        if sale.voided_at:
            flash("This sale is already voided.", "warning")
            return redirect(url_for("sales.index"))
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("Give a reason before voiding a sale.", "error")
            return redirect(url_for("sales.index"))
        now = datetime.utcnow()
        for item in sorted(sale.items, key=lambda item: (item.product_id, item.id)):
            db.session.execute(update(Product).where(Product.id == item.product_id, Product.business_id == b.id)
                               .values(stock_quantity=Product.stock_quantity + item.quantity))
            db.session.add(StockMovement(business_id=b.id, product_id=item.product_id,
                kind="void", quantity_change=item.quantity, reason=reason[:300], sale_id=sale.id,
                occurred_at=now))
        sale.voided_at = now
        sale.void_reason = reason[:300]
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    flash("Sale voided and stock restored.", "success")
    return redirect(url_for("sales.index"))
