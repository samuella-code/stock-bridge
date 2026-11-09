"""Separate Staff sales surface; owner sales endpoints remain owner-only."""
from datetime import datetime
from flask import abort, render_template, request, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload, load_only
from app import db
from app.models import Product, Sale, SaleItem
from app.staff.routes import staff_bp
from app.staff import sales_access, sales_submissions, roles
from app.sales.ledger import create_sale, StockChanged
from app.transaction_submissions import complete, SubmissionError, SubmissionConflict


PAYMENTS = ('Cash', 'Bank Transfer', 'POS', 'Other')


def authority(membership_id, business_id, *, write=False):
    user = current_user._get_current_object()
    try:
        if write:
            return sales_access.authorize(user, membership_id, business_id, 'sales_create', write=True)
        try:
            return sales_access.authorize(user, membership_id, business_id, 'sales_view')
        except ValueError:
            return sales_access.authorize(user, membership_id, business_id, 'sales_create')
    except ValueError:
        db.session.rollback()
        abort(403)


def parse_items(form):
    ids, quantities = form.getlist('product_id'), form.getlist('quantity')
    if not ids or len(ids) != len(quantities) or len(ids) > 50:
        raise ValueError
    # Staff use the current catalogue price and server date, never submitted costs/prices.
    if any(form.getlist(k) for k in ('unit_price', 'unit_cost', 'sold_at')):
        raise ValueError
    parsed, seen = [], set()
    for identifier, quantity in zip(ids, quantities):
        if not quantity.strip():
            continue
        pid, qty = int(identifier), int(quantity)
        if pid in seen or not 1 <= pid <= 2147483647 or not 1 <= qty <= 2147483647:
            raise ValueError
        parsed.append((pid, qty, None)); seen.add(pid)
    if not parsed:
        raise ValueError
    method, note = form.get('payment_method', ''), form.get('note', '').strip()
    if method not in PAYMENTS or len(note) > 500:
        raise ValueError
    return parsed, method, note


@staff_bp.route('/memberships/<int:membership_id>/businesses/<int:business_id>/sales', methods=['GET', 'POST'])
@login_required
def sales(membership_id, business_id):
    member, business = authority(membership_id, business_id, write=request.method == 'POST')
    permission = set(roles.resolve(member.owner_id, member.role).permissions)
    can_view, can_create = 'sales_view' in permission, 'sales_create' in permission
    token = (request.form.get('submission_token', '') if request.method == 'POST'
             else sales_submissions.issue(member, business) if can_create else None)

    def page(message=None, status=200):
        # A rollback releases the owner lock; recheck authority before rendering data.
        member, business = authority(membership_id, business_id)
        permission = set(roles.resolve(member.owner_id, member.role).permissions)
        can_view, can_create = 'sales_view' in permission, 'sales_create' in permission
        query = request.args.get('q', '').strip()[:80]
        products = []
        if can_create:
            catalogue = Product.query.options(load_only(Product.id, Product.name, Product.unit,
                Product.selling_price, Product.stock_quantity)).filter_by(business_id=business.id, active=True)
            if query:
                term = query.replace('!', '!!').replace('%', '!%').replace('_', '!_')
                catalogue = catalogue.filter(Product.name.ilike('%'+term+'%', escape='!'))
            products = catalogue.order_by(Product.name, Product.id).limit(50).all()
        history = None
        if can_view:
            # No cost or profit columns are loaded into the Staff view.
            history = Sale.query.options(load_only(Sale.id, Sale.sold_at, Sale.payment_method,
                Sale.note, Sale.voided_at), selectinload(Sale.items).load_only(
                SaleItem.id, SaleItem.product_id, SaleItem.quantity, SaleItem.unit_price)
                .selectinload(SaleItem.product).load_only(Product.id, Product.name)
            ).filter_by(business_id=business.id).order_by(Sale.sold_at.desc(), Sale.id.desc()).paginate(
                page=max(1, request.args.get('page', 1, type=int)), per_page=20, error_out=False)
        return render_template('staff/sales.html', business=business, member=member,
            can_view=can_view, can_create=can_create, products=products, history=history,
            submission_token=token, payment_methods=PAYMENTS, query=query,
            notice=message, error=message if status >= 400 else None), status

    if request.method == 'GET':
        return page()
    try:
        parsed, method, note = parse_items(request.form)
        intent = dict(items=[dict(product_id=p, quantity=q, price={'default': True})
                             for p,q,_ in sorted(parsed)], payment_method=method, date=None, note=note)
        receipt, replay = sales_submissions.claim(token, member, business, intent)
        if replay:
            identifier = receipt.result_reference
            if not isinstance(identifier, str) or not identifier.startswith('sale:'):
                raise SubmissionConflict('Unavailable original sale.')
            original = Sale.query.filter_by(id=int(identifier.split(':')[1]), business_id=business.id).first()
            if not original:
                raise SubmissionConflict('Unavailable original sale.')
            db.session.rollback()
            return page('This sale was already recorded. Stock was not changed again.')
        products = {p.id:p for p in Product.query.filter(Product.business_id == business.id,
            Product.id.in_([p for p,_,_ in parsed]), Product.active.is_(True))
            .order_by(Product.id).with_for_update().all()}
        if len(products) != len(parsed) or any(products[p].stock_quantity < q for p,q,_ in parsed):
            raise StockChanged('Review products and available stock.')
        sale = create_sale(business, parsed, products, sold_at=datetime.utcnow(), method=method,
                           note=note, actor=current_user._get_current_object())
        complete(receipt, f'sale:{sale.id}')
        db.session.commit()
    except SubmissionConflict:
        db.session.rollback()
        return page('This submission has conflicting or unconfirmed contents. Check with the owner.', 409)
    except (ValueError, SubmissionError, StockChanged):
        db.session.rollback()
        return page('Sale not saved. Check the items, stock and submission reference.', 400)
    except Exception:
        db.session.rollback()
        raise
    return redirect(url_for('staff.sales', membership_id=member.id, business_id=business.id,
                            recorded=1), code=303)
