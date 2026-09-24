from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from app import db
from app.models import Product, Restock, Sale

products_bp = Blueprint("products", __name__, url_prefix="/products")

def current_business():
    return current_user.businesses[0] if current_user.businesses else None

def owned_product(product_id):
    business = current_business()
    product = db.session.get(Product, product_id)
    if not business or not product or product.business_id != business.id:
        abort(404)
    return product

def product_data(form):
    errors = []
    data = {"name": form.get("name", "").strip(), "sku": form.get("sku", "").strip().upper() or None,
            "category": form.get("category", "").strip() or None,
            "supplier_name": form.get("supplier_name", "").strip() or None}
    if not data["name"]:
        errors.append("Product name is required.")
    try:
        data["buying_price"] = Decimal(form.get("buying_price", "0"))
        data["selling_price"] = Decimal(form.get("selling_price", "0"))
        if data["buying_price"] < 0 or data["selling_price"] < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        errors.append("Prices must be valid amounts of zero or more.")
        data["buying_price"] = data["selling_price"] = Decimal("0")
    for field, label, default in (("stock_quantity","Stock quantity",0),("minimum_stock_level","Minimum stock level",0),("supplier_lead_time","Supplier lead time",2),("safety_stock","Safety stock",0)):
        try:
            data[field] = int(form.get(field, default))
            if data[field] < 0:
                raise ValueError
        except ValueError:
            errors.append(f"{label} must be a whole number of zero or more.")
            data[field] = default
    return data, errors

@products_bp.route("/")
@login_required
def index():
    business = current_business()
    query = request.args.get("q", "").strip()
    stock_filter = request.args.get("stock", "all")
    products_query = Product.query.filter_by(business_id=business.id)
    if query:
        term = f"%{query}%"
        products_query = products_query.filter(or_(Product.name.ilike(term), Product.sku.ilike(term), Product.category.ilike(term), Product.supplier_name.ilike(term)))
    if stock_filter == "low":
        products_query = products_query.filter(Product.stock_quantity <= Product.minimum_stock_level)
    elif stock_filter == "healthy":
        products_query = products_query.filter(Product.stock_quantity > Product.minimum_stock_level)
    products = products_query.order_by(Product.created_at.desc()).all()
    all_products = Product.query.filter_by(business_id=business.id).all()
    return render_template("products/index.html", business=business, products=products, query=query, stock_filter=stock_filter,
        total_products=len(all_products), low_stock_count=sum(p.is_low_stock for p in all_products),
        inventory_value=sum(p.buying_price * p.stock_quantity for p in all_products))

@products_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    business = current_business()
    if request.method == "POST":
        data, errors = product_data(request.form)
        if data["sku"] and Product.query.filter_by(business_id=business.id, sku=data["sku"]).first():
            errors.append("That SKU is already used in this business.")
        if not errors:
            product = Product(business_id=business.id, **data)
            db.session.add(product)
            db.session.commit()
            flash(f"{product.name} was added to inventory.", "success")
            return redirect(url_for("products.index"))
        for error in errors:
            flash(error, "error")
    return render_template("products/form.html", business=business, product=None)

@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit(product_id):
    business, product = current_business(), owned_product(product_id)
    if request.method == "POST":
        data, errors = product_data(request.form)
        duplicate = Product.query.filter(Product.business_id == business.id, Product.sku == data["sku"], Product.id != product.id).first() if data["sku"] else None
        if duplicate:
            errors.append("That SKU is already used in this business.")
        if not errors:
            # Stock is changed only by sales and restock receipts after setup.
            data.pop("stock_quantity", None)
            for key, value in data.items():
                setattr(product, key, value)
            db.session.commit()
            flash(f"{product.name} was updated.", "success")
            return redirect(url_for("products.index"))
        for error in errors:
            flash(error, "error")
    return render_template("products/form.html", business=business, product=product)

@products_bp.route("/<int:product_id>/delete", methods=["POST"])
@login_required
def delete(product_id):
    product = owned_product(product_id)
    if Sale.query.filter_by(product_id=product.id).first() or Restock.query.filter_by(product_id=product.id).first():
        flash("This product has transaction history and cannot be deleted.", "error")
        return redirect(url_for("products.index"))
    name = product.name
    db.session.delete(product)
    db.session.commit()
    flash(f"{name} was removed from inventory.", "success")
    return redirect(url_for("products.index"))
