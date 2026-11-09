"""Scoped Staff stock views and goods receipts; owner routes stay owner-only."""
from decimal import Decimal, InvalidOperation
from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from app import db
from app.models import Product, Restock
from app.staff.routes import staff_bp
from app.staff import stock_access, stock_submissions, roles
from app.restocking.ledger import receive_stock, StockChanged
from app.transaction_submissions import complete, SubmissionError, SubmissionConflict


def authority(membership_id, business_id, permissions, *, write=False):
    user=current_user._get_current_object()
    for permission in permissions:
        try:
            return stock_access.authorize(user,membership_id,business_id,permission,write=write)
        except ValueError:
            pass
    db.session.rollback()
    abort(403)


def catalogue(business_id):
    # Only public catalogue columns. No deferred ORM cost fields can be fetched.
    query=request.args.get('q','').strip()[:80]
    rows=db.session.query(Product.id,Product.name,Product.unit,Product.stock_quantity).filter(
        Product.business_id==business_id,Product.active.is_(True))
    if query:
        term=query.replace('!','!!').replace('%','!%').replace('_','!_')
        rows=rows.filter(Product.name.ilike('%'+term+'%',escape='!'))
    return rows.order_by(Product.name,Product.id),query


@staff_bp.get('/memberships/<int:membership_id>/businesses/<int:business_id>/inventory')
@login_required
def inventory(membership_id,business_id):
    member,business=authority(membership_id,business_id,('inventory_view',))
    rows,query=catalogue(business.id)
    products=rows.paginate(page=max(1,request.args.get('page',1,type=int)),per_page=30,error_out=False)
    return render_template('staff/inventory.html',member=member,business=business,products=products,query=query)


def parse_items(form):
    ids,quantities,costs=(form.getlist(key) for key in ('product_id','quantity','unit_cost'))
    if not ids or len(ids)!=len(quantities) or len(ids)!=len(costs) or len(ids)>50:
        raise ValueError
    if any(form.getlist(key) for key in ('received_at','date','stock_quantity','unit_price','selling_price',
                                        'owner_id','user_id','business_id','membership_id')):
        raise ValueError
    parsed=[];seen=set()
    for identifier,quantity,cost in zip(ids,quantities,costs):
        if not quantity.strip() and not cost.strip():continue
        pid,qty,value=int(identifier),int(quantity),Decimal(cost)
        if (pid in seen or not 1<=pid<=2147483647 or not 1<=qty<=2147483647
                or not value.is_finite() or value<0 or value>Decimal('9999999999.99')
                or value.as_tuple().exponent < -2):raise ValueError
        parsed.append((pid,qty,value));seen.add(pid)
    if not parsed:raise ValueError
    supplier,note=form.get('supplier','').strip(),form.get('note','').strip()
    if len(supplier)>140 or len(note)>500:raise ValueError
    return parsed,supplier,note


@staff_bp.route('/memberships/<int:membership_id>/businesses/<int:business_id>/restocking',methods=['GET','POST'])
@login_required
def restocking(membership_id,business_id):
    member,business=authority(membership_id,business_id,('restocking_create',) if request.method=='POST'
                              else ('restocking_view','restocking_create'),write=request.method=='POST')
    permission=set(roles.resolve(member.owner_id,member.role).permissions)
    token=request.form.get('submission_token','') if request.method=='POST' else (
        stock_submissions.issue(member,business) if 'restocking_create' in permission else None)
    def page(message=None,status=200):
        # Any rollback releases the owner lock, so recheck current authority.
        member,business=authority(membership_id,business_id,('restocking_view','restocking_create'))
        permission=set(roles.resolve(member.owner_id,member.role).permissions)
        can_view,can_create='restocking_view' in permission,'restocking_create' in permission
        products=[];query='';history=None
        if can_create:
            rows,query=catalogue(business.id);products=rows.limit(50).all()
        if can_view:
            # Explicit safe columns, including when financial_view is assigned.
            history=db.session.query(Restock.id,Restock.received_at,Restock.quantity,Product.name.label('product_name'),Product.unit).join(
                Product,Product.id==Restock.product_id).filter(Restock.business_id==business.id,
                Product.business_id==business.id).order_by(Restock.received_at.desc(),Restock.id.desc()).paginate(
                page=max(1,request.args.get('page',1,type=int)),per_page=20,error_out=False)
        return render_template('staff/restocking.html',member=member,business=business,
            can_view=can_view,can_create=can_create,products=products,query=query,history=history,
            submission_token=token,notice=message,error=message if status>=400 else None),status
    if request.method=='GET':return page()
    try:
        parsed,supplier,note=parse_items(request.form)
        intent=dict(items=[dict(product_id=pid,quantity=qty,unit_cost=format(cost,'.2f'))
                           for pid,qty,cost in sorted(parsed)],supplier=supplier,note=note)
        receipt,replay=stock_submissions.claim(token,member,business,intent)
        if replay:
            batch=receipt.result_reference
            if not isinstance(batch,str) or not Restock.query.filter_by(business_id=business.id,batch_id=batch).first():
                raise SubmissionConflict('Original receipt unavailable.')
            db.session.rollback()
            return page('This restock was already recorded. Stock was not increased again.')
        products={p.id:p for p in Product.query.filter(Product.business_id==business.id,
            Product.active.is_(True),Product.id.in_([pid for pid,_,_ in parsed])).order_by(
            Product.id).with_for_update().populate_existing().all()}
        if len(products)!=len(parsed) or any(products[pid].stock_quantity>2147483647-qty for pid,qty,_ in parsed):
            raise StockChanged('Review products and quantities.')
        batch=receive_stock(business,parsed,products,supplier=supplier,note=note,
                            actor=current_user._get_current_object())
        complete(receipt,batch);db.session.commit()
    except SubmissionConflict:
        db.session.rollback();return page('Submission conflicts with an earlier receipt. Check with the owner.',409)
    except (ValueError,InvalidOperation,SubmissionError):
        db.session.rollback();return page('Restock not saved. Check products, quantities, new purchase costs and the submission reference.',400)
    except Exception:
        db.session.rollback();raise
    return redirect(url_for('staff.restocking',membership_id=member.id,business_id=business.id,recorded=1),code=303)
