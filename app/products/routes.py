from app.subscriptions.entitlements import selected_business
from datetime import datetime
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, func, update
from app import db
from app.models import Product, Restock, Sale, SaleItem, StockMovement
from app.admin.routes import log
from app.products.catalogue import product_data, duplicate_errors, add_product, search_products
from sqlalchemy.exc import IntegrityError

products_bp = Blueprint("products", __name__, url_prefix="/products")
ADJUSTMENT_REASONS = ("Damaged", "Expired", "Lost", "Stock count correction", "Returned", "Other")

def current_business():
    return selected_business(current_user)

def owned_product(product_id):
    business = current_business()
    product = db.session.get(Product, product_id)
    if not business or not product or product.business_id != business.id:
        abort(404)
    return product

@products_bp.get("/")
@login_required
def index():
    b = current_business()
    query = request.args.get("q", "").strip()[:100]
    stock_filter = request.args.get("stock", "all")
    category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "recent")
    rows = Product.query.filter_by(business_id=b.id, active=stock_filter != "archived")
    if query:
        escaped = query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        term = f"%{escaped}%"
        rows = rows.filter(or_(Product.name.ilike(term, escape='\\'), Product.sku.ilike(term, escape='\\'), Product.category.ilike(term, escape='\\'), Product.supplier_name.ilike(term, escape='\\')))
    if category:
        rows = rows.filter(Product.category == category)
    if stock_filter == "out":
        rows = rows.filter(Product.stock_quantity == 0)
    elif stock_filter == "low":
        rows = rows.filter(Product.stock_quantity > 0, Product.stock_quantity <= Product.minimum_stock_level)
    elif stock_filter in ("healthy", "in"):
        rows = rows.filter(Product.stock_quantity > Product.minimum_stock_level, Product.stock_quantity > 0)
    order = {"name": Product.name.asc(), "stock": Product.stock_quantity.asc(),
             "price": Product.selling_price.asc(), "cost": Product.buying_price.asc(),
             "value": (Product.stock_quantity * Product.buying_price).desc(),
             "oldest": Product.created_at.asc(), "priority": (Product.stock_quantity - Product.minimum_stock_level).asc(),
             "recent": Product.created_at.desc()}
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
        errors.extend(duplicate_errors(b.id, data))
        if not errors:
            try:
                product = add_product(b.id, data)
                log("PRODUCT_CREATED", f"Product {product.id} created.", actor=current_user, business_id=b.id)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors.append("Another product already uses this SKU. Please check it.")
            else:
                flash(f"{product.name} was added to inventory.", "success")
                if request.form.get("after_save") == "another":
                    return redirect(url_for("products.create"))
                return redirect(url_for("products.detail", product_id=product.id))
        for error in errors: flash(error, "error")
    return render_template("products/form.html", business=b, product=None)

@products_bp.route("/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit(product_id):
    b, product = current_business(), owned_product(product_id)
    if request.method == "POST":
        data, errors = product_data(request.form, editing=True)
        errors.extend(duplicate_errors(b.id, data, product.id))
        if not errors:
            try:
                for key, value in data.items(): setattr(product, key, value)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                errors.append("Another product already uses this SKU. Please check it.")
            else:
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
    # Lock/read the actual pre-adjustment stock before generating transition alerts.
    product = Product.query.filter_by(id=product.id, business_id=b.id).with_for_update().populate_existing().one()
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
    movement = StockMovement(business_id=b.id, product_id=product.id,
        kind="adjustment", quantity_change=delta, reason=reason,
        note=request.form.get("note", "").strip()[:500], occurred_at=when)
    db.session.add(movement)
    db.session.flush()
    from app.notifications.service import stock_transition
    stock_transition(product, product.stock_quantity, product.stock_quantity + delta, f'adjustment:{movement.id}')
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

@products_bp.get('/lookup')
@login_required
def lookup():
    b = current_business()
    if set(request.args) - {'q'}:
        abort(400)
    term = request.args.get('q', '').strip()[:100]
    rows = search_products(b.id, term).order_by(Product.name, Product.id).limit(15).all()
    response = jsonify(products=[{'id':p.id, 'name':p.name, 'sku':p.sku,
        'active':p.active, 'stock':p.stock_quantity, 'unit':p.unit, 'price':str(p.selling_price), 'cost':str(p.buying_price),
        'url':url_for('products.detail', product_id=p.id)} for p in rows])
    response.headers['Cache-Control'] = 'no-store, private'
    return response

@products_bp.route('/quick-add', methods=['GET','POST'])
@login_required
def quick_add():
    from app.products.catalogue import HEADERS, validate_rows, save_batch
    b = current_business()
    rows, issues = [{}], []
    if request.method == 'POST':
        fields = list(HEADERS.values())
        values = {key:request.form.getlist(key) for key in fields}
        count = len(values['name'])
        if not 1 <= count <= 100 or any(len(items) != count for items in values.values()):
            flash('Quick Add supports 1 to 100 complete product rows per batch.', 'error')
        else:
            rows = [{key:items[i] for key,items in values.items()} for i in range(count)]
            _, issues = validate_rows(b.id, rows)
            if not issues:
                try:
                    saved = save_batch(b.id, rows, current_user.id)
                except (ValueError, IntegrityError):
                    db.session.rollback()
                    flash('Products could not be saved. Check duplicate SKUs and try again.', 'error')
                else:
                    flash(f'{saved} products added with opening stock.', 'success')
                    return redirect(url_for('products.index'))
    return render_template('products/quick_add.html', business=b, rows=rows, issues=issues)

@products_bp.get('/import/template')
@login_required
def import_template():
    import csv, io
    from app.products.catalogue import HEADERS
    buffer = io.StringIO()
    csv.writer(buffer).writerow([h + (' *' if h == 'Product Name' else '') for h in HEADERS])
    return send_file(io.BytesIO(buffer.getvalue().encode('utf-8-sig')), mimetype='text/csv',
                     as_attachment=True, download_name='stockbridge-products.csv')

def import_signer():
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='stockbridge-product-import')

@products_bp.route('/import', methods=['GET','POST'])
@login_required
def import_products():
    from app.products.catalogue import HEADERS, read_spreadsheet, validate_rows, save_batch
    from app.models import AuditLog
    from app.products.product_lists import parse_product_list, read_text_file, existing_matches
    from itsdangerous import BadSignature, SignatureExpired
    from uuid import uuid4
    from time import time
    b = current_business()
    if request.method == 'GET':
        return render_template('products/import.html', business=b)
    if request.content_length and request.content_length > 4 * 1024 * 1024:
        abort(413)
    try:
        if request.form.get('confirm') == 'yes':
            token = request.form.get('preview', '')
            if len(token) > 450000:
                raise ValueError('This preview is too large. Use a smaller batch.')
            payload = import_signer().loads(token, max_age=1200)
            if 'issued_at' in payload and time() - payload['issued_at'] > 1200:
                raise SignatureExpired('Import review expired')
            if payload['business'] != b.id or payload['user'] != current_user.id:
                abort(403)
            if AuditLog.query.filter_by(business_id=b.id, action='PRODUCT_IMPORT', description='Import ' + payload['nonce']).first():
                flash('This import was already completed. No products were added again.', 'success')
                return redirect(url_for('products.index'))
            rows = payload['rows']
            skipped, existing = payload.get('skipped', 0), payload.get('existing', 0)
            if request.form.get('edited') == 'yes':
                reviewed = []
                selected_ids = set()
                for index in range(len(rows)):
                    choice = request.form.get(f'rows.{index}.action', 'new')
                    if choice.startswith('existing:'):
                        try:
                            selected_ids.add(int(choice.split(':', 1)[1]))
                        except ValueError:
                            abort(400)
                owned_ids = {p[0] for p in db.session.query(Product.id).filter(
                    Product.business_id == b.id, Product.id.in_(selected_ids))} if selected_ids else set()
                if selected_ids != owned_ids:
                    abort(403)
                for index, original in enumerate(rows):
                    prefix = f'rows.{index}.'
                    action = request.form.get(prefix + 'action', 'new')
                    if action == 'skip':
                        skipped += 1
                        continue
                    if action.startswith('existing:'):
                        try:
                            product_id = int(action.split(':', 1)[1])
                        except ValueError:
                            abort(400)
                        # Use Existing is a skip, never an overwrite or a stock update.
                        if product_id not in owned_ids:
                            abort(403)
                        existing += 1
                        continue
                    if action != 'new':
                        abort(400)
                    reviewed.append({**{field:request.form.get(prefix + field, '') for field in HEADERS.values()},
                                     '_row':original.get('_row', index + 1)})
                rows = reviewed
            # Preserve the original nonce when correcting validation errors or retrying.
            _, issues = validate_rows(b.id, rows) if rows else ([], [])
            if issues:
                revised_token = import_signer().dumps({**payload, 'rows':rows, 'skipped':skipped, 'existing':existing})
                if len(revised_token) > 450000:
                    raise ValueError('This preview is too large. Use a smaller batch.')
                return render_template('products/import_preview.html', business=b, rows=rows, issues=issues,
                    ready=len(rows)-len(issues), preview=revised_token,
                    fields=HEADERS, matches=existing_matches(b.id, rows)), 400
            saved = save_batch(b.id, rows, current_user.id, receipt='Import ' + payload['nonce'])
            flash(f'Products imported successfully: {saved} products created; {skipped} rows skipped; {existing} existing products selected.'
                  if saved else f'No products added. This import is completed; {skipped} rows skipped; {existing} existing products selected.', 'success')
            return redirect(url_for('products.index'))
        method = request.form.get('method', 'spreadsheet')
        if method == 'paste':
            rows = parse_product_list(request.form.get('product_list', ''))
        elif method == 'text':
            upload = request.files.get('text_file')
            if not upload:
                raise ValueError('Choose a .txt product list.')
            rows = read_text_file(upload)
        elif method == 'spreadsheet':
            upload = request.files.get('spreadsheet')
            if not upload:
                raise ValueError('Choose a CSV or Excel spreadsheet.')
            rows = read_spreadsheet(upload)
        else:
            abort(400)
        _, issues = validate_rows(b.id, rows)
        token = import_signer().dumps({'business':b.id,'user':current_user.id,'rows':rows,'nonce':str(uuid4()),'issued_at':int(time())})
        if len(token) > 450000:
            raise ValueError('This preview is too large. Split the list into smaller batches.')
        return render_template('products/import_preview.html', business=b, rows=rows, issues=issues,
            ready=len(rows)-len(issues), preview=token, fields=HEADERS, matches=existing_matches(b.id, rows))
    except (BadSignature, SignatureExpired):
        flash('This preview expired or was changed. Import the product list again.', 'error')
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        flash(str(error) if isinstance(error, ValueError) else 'A SKU was added since the preview. Review the list again.', 'error')
    return render_template('products/import.html', business=b), 400
