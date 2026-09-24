from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, func, update
from app import db
from app.models import Product, Restock, Sale, SaleItem, StockMovement

products_bp = Blueprint("products", __name__, url_prefix="/products")
ADJUSTMENT_REASONS = ("Damaged", "Expired", "Lost", "Stock count correction", "Returned", "Other")

def current_business():
    return current_user.businesses[0] if current_user.businesses else None

def owned_product(product_id):
    business = current_business()
    product = db.session.get(Product, product_id)
    if not business or not product or product.business_id != business.id:
        abort(404)
    return product

def product_data(form, editing=False):
    errors = []
    data = {"name": form.get("name", "").strip(), "sku": form.get("sku", "").strip().upper() or None,
            "category": form.get("category", "").strip() or None,
            "supplier_name": form.get("supplier_name", "").strip() or None,
            "unit": form.get("unit", "unit").strip()[:30] or "unit",
            "description": form.get("description", "").strip()[:500] or None}
    if not data["name"] or len(data["name"]) > 140:
        errors.append("Product name must be 1 to 140 characters.")
    try:
        for field in ("buying_price", "selling_price"):
            value = Decimal(form.get(field, "0"))
            if not value.is_finite() or value < 0:
                raise ValueError
            data[field] = value
    except (InvalidOperation, ValueError):
        errors.append("Prices must be valid amounts of zero or more.")
    fields = [("minimum_stock_level",0),("supplier_lead_time",2),("safety_stock",0)]
    if not editing:
        fields.append(("stock_quantity",0))
    for field, default in fields:
        try:
            value = int(form.get(field, default))
            if value < 0:
                raise ValueError
            data[field] = value
        except ValueError:
            errors.append(f"{field.replace('_',' ').title()} must be a whole number of zero or more.")
    return data, errors

@products_bp.get("/")
@login_required
def index():
    b = current_business()
    query = request.args.get("q", "").strip()[:100]
    stock_filter = request.args.get("stock", "all")
    category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "recent")
    rows = Product.query.filter_by(business_id=b.id, active=stock_filter == "archived")
    if stock_filter != "archived":
        rows = Product.query.filter_by(business_id=b.id, active=True)
    if query:
        term = f"%{query}%"
        rows = rows.filter(or_(Product.name.ilike(term), Product.sku.ilike(term), Product.category.ilike(term), Product.supplier_name.ilike(term)))
    if category:
        rows = rows.filter(Product.category == category)
    if stock_filter == "out":
        rows = rows.filter(Product.stock_quantity == 0)
    elif stock_filter == "low":
        rows = rows.filter(Product.stock_quantity > 0, Product.stock_quantity <= Product.minimum_stock_level)
    elif stock_filter in ("healthy", "in"):
        rows = rows.filter(Product.stock_quantity > Product.minimum_stock_level, Product.stock_quantity > 0)
    order = {"name": Product.name.asc(), "stock": Product.stock_quantity.asc(),
             "price": Product.selling_price.asc(), "recent": Product.created_at.desc()}
    page = rows.order_by(order.get(sort, order["recent"]), Product.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    summary = db.session.query(func.count(Product.id),
        func.coalesce(func.sum(Product.stock_quantity * Product.buying_price), 0),
        func.coalesce(func.sum(db.case((Product.stock_quantity <= Product.minimum_stock_level, 1), else_=0)), 0)
    ).filter(Product.business_id == b.id, Product.active.is_(True)).one()
    categories = db.session.query(Product.category).filter(Product.business_id == b.id,
        Product.active.is_(True), Product.category.isnot(None)).distinct().order_by(Product.category).all()
    return render_template("products/index.html", business=b, products=page.items, pagination=page,
        query=query, stock_filter=stock_filter, category=category, sort=sort,
        categories=[row[0] for row in categories], total_products=summary[0],
        low_stock_count=summary[2], inventory_value=summary[1])

@products_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    b = current_business()
    if request.method == "POST":
        data, errors = product_data(request.form)
        if data["sku"] and Product.query.filter_by(business_id=b.id, sku=data["sku"]).first():
            errors.append("That SKU is already used in this business.")
        if not errors:
            quantity = data.pop("stock_quantity")
            product = Product(business_id=b.id, stock_quantity=quantity, opening_quantity=quantity, **data)
            db.session.add(product)
            db.session.flush()
            db.session.add(StockMovement(business_id=b.id, product_id=product.id,
                kind="opening", quantity_change=quantity, occurred_at=product.created_at))
            db.session.commit()
            flash(f"{product.name} was added to inventory.", "success")
            return redirect(url_for("products.detail", product_id=product.id))
        for error in errors: flash(error, "error")
    return render_template("products/form.html", business=b, product=None)

@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit(product_id):
    b, product = current_business(), owned_product(product_id)
    if request.method == "POST":
        data, errors = product_data(request.form, editing=True)
        duplicate = Product.query.filter(Product.business_id == b.id, Product.sku == data["sku"],
            Product.id != product.id).first() if data["sku"] else None
        if duplicate: errors.append("That SKU is already used in this business.")
        if not errors:
            for key, value in data.items(): setattr(product, key, value)
            db.session.commit()
            flash(f"{product.name} was updated.", "success")
            return redirect(url_for("products.detail", product_id=product.id))
        for error in errors: flash(error, "error")
    return render_template("products/form.html", business=b, product=product)

@products_bp.get("/<int:product_id>")
@login_required
def detail(product_id):
    b, product = current_business(), owned_product(product_id)
    movements = StockMovement.query.filter_by(business_id=b.id, product_id=product.id).order_by(
        StockMovement.occurred_at.desc(), StockMovement.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=25, error_out=False)
    sold = db.session.query(func.coalesce(func.sum(SaleItem.quantity),0),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.unit_price),0)).join(Sale).filter(
        Sale.business_id == b.id, SaleItem.product_id == product.id, Sale.voided_at.is_(None)).one()
    last_restock = db.session.query(func.max(Restock.received_at)).filter(
        Restock.business_id == b.id, Restock.product_id == product.id).scalar()
    return render_template("products/detail.html", business=b, product=product,
        movements=movements, sold=sold, last_restock=last_restock, reasons=ADJUSTMENT_REASONS)

@products_bp.post("/<int:product_id>/adjust")
@login_required
def adjust(product_id):
    b, product = current_business(), owned_product(product_id)
    try:
        magnitude = int(request.form.get("quantity", ""))
        direction = request.form.get("direction")
        reason = request.form.get("reason")
        when = datetime.strptime(request.form["date"], "%Y-%m-%d") if request.form.get("date") else datetime.utcnow()
        if magnitude < 1 or direction not in ("increase", "decrease") or reason not in ADJUSTMENT_REASONS:
            raise ValueError
    except ValueError:
        flash("Choose a valid amount, direction, reason and date.", "error")
        return redirect(url_for("products.detail", product_id=product.id))
    delta = magnitude if direction == "increase" else -magnitude
    if product.stock_quantity + delta < 0:
        flash(f"Only {product.stock_quantity} units are currently available.", "error")
        return redirect(url_for("products.detail", product_id=product.id))
    condition = [Product.id == product.id, Product.business_id == b.id]
    if delta < 0:
        condition.append(Product.stock_quantity >= -delta)
    result = db.session.execute(update(Product).where(*condition)
        .values(stock_quantity=Product.stock_quantity + delta,
                updated_at=datetime.utcnow()).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.session.rollback()
        flash("Stock changed while recording the adjustment. Please try again.", "error")
        return redirect(url_for("products.detail", product_id=product.id))
    db.session.add(StockMovement(business_id=b.id, product_id=product.id,
        kind="adjustment", quantity_change=delta, reason=reason,
        note=request.form.get("note", "").strip()[:500], occurred_at=when))
    db.session.commit()
    flash("Stock adjustment recorded.", "success")
    return redirect(url_for("products.detail", product_id=product.id))

@products_bp.post("/<int:product_id>/delete")
@login_required
def delete(product_id):
    product = owned_product(product_id)
    if product.stock_quantity:
        flash("Reduce remaining stock to zero with a recorded adjustment before archiving.", "error")
        return redirect(url_for("products.detail", product_id=product.id))
    product.active = False
    db.session.commit()
    flash(f"{product.name} archived. Its history is preserved.", "success")
    return redirect(url_for("products.index"))

@products_bp.post("/<int:product_id>/restore")
@login_required
def restore(product_id):
    product = owned_product(product_id)
    product.active = True
    db.session.commit()
    flash(f"{product.name} is active again.", "success")
    return redirect(url_for("products.detail", product_id=product.id))
