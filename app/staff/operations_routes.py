"""Products, expenses and single assigned-business reports for verified Staff."""
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
import re
from flask import abort,render_template,request,redirect,url_for
from flask_login import current_user,login_required
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import Product,Expense
from app.staff.routes import staff_bp
from app.staff import operations_access as access,operations_submissions as submissions,roles
from app.transaction_submissions import complete,SubmissionError,SubmissionConflict
from app.products.catalogue import product_data,duplicate_errors,add_product
from app.expenses.routes import CATEGORIES
from app.expenses.ledger import create_expense
from app.admin.routes import log

BASE='/memberships/<int:membership_id>/businesses/<int:business_id>'
EDIT_FIELDS=('name','sku','category','unit','supplier_name','description','selling_price',
             'minimum_stock_level','supplier_lead_time','safety_stock')
CREATE_FIELDS=EDIT_FIELDS+('buying_price','stock_quantity')


def authority(mid,bid,permissions,*,write=False):
    for permission in permissions:
        try:return access.authorize(current_user._get_current_object(),mid,bid,permission,write=write)
        except ValueError:pass
    db.session.rollback();abort(403)


def product_fields(row):return {key:getattr(row,key) for key in EDIT_FIELDS}


def safe_product(bid,pid):
    return db.session.query(Product.id,*[getattr(Product,key) for key in EDIT_FIELDS]).filter(
        Product.id==pid,Product.business_id==bid,Product.active.is_(True)).first_or_404()


@staff_bp.get(BASE+'/products')
@login_required
def products(membership_id,business_id):
    member,business=authority(membership_id,business_id,('products_edit','products_create'))
    permissions=set(roles.resolve(member.owner_id,member.role).permissions)
    rows=None;query=request.args.get('q','').strip()[:80]
    if 'products_edit' in permissions:
        rows=db.session.query(Product.id,Product.name,Product.sku).filter_by(business_id=business.id,active=True)
        if query:
            term=query.replace('!','!!').replace('%','!%').replace('_','!_')
            rows=rows.filter(Product.name.ilike('%'+term+'%',escape='!'))
        rows=rows.order_by(Product.name,Product.id).paginate(page=max(1,request.args.get('page',1,type=int)),per_page=30,error_out=False)
    return render_template('staff/products.html',member=member,business=business,rows=rows,query=query,
                           can_create='products_create' in permissions)


def product_form(mid,bid,pid=None):
    permission='products_edit' if pid is not None else 'products_create'
    member,business=authority(mid,bid,(permission,),write=request.method=='POST')
    safe=safe_product(bid,pid) if pid is not None else None
    op='product_edit' if pid is not None else 'product_create'
    key=request.form.get('submission_token','') if request.method=='POST' else submissions.issue(
        member,business,op,product_id=pid,revision=submissions.fingerprint(product_fields(safe)) if safe else None)
    def page(message=None,status=200):
        member,business=authority(mid,bid,(permission,))
        current=safe_product(bid,pid) if pid is not None else None
        values=product_fields(current) if current else {}
        if request.method=='POST':values.update({k:request.form.get(k,'') for k in (EDIT_FIELDS if pid else CREATE_FIELDS)})
        return render_template('staff/product_form.html',member=member,business=business,editing=pid is not None,
            fields=EDIT_FIELDS if pid is not None else CREATE_FIELDS,values=values,submission_token=key,
            error=message if status>=400 else None,notice=message),status
    if request.method=='GET':return page()
    try:
        allowed=EDIT_FIELDS if pid is not None else CREATE_FIELDS
        if set(request.form)-set(allowed)-{'csrf_token','submission_token'}:raise ValueError
        data,errors=product_data(request.form,editing=pid is not None)
        if pid is not None:data.pop('buying_price',None)
        if errors:raise ValueError
        intent={k:format(v,'.2f') if isinstance(v,Decimal) else v for k,v in data.items()}
        receipt,replay,revision=submissions.claim(key,member,business,op,intent,product_id=pid)
        if replay:
            ref=receipt.result_reference
            if not isinstance(ref,str) or not re.fullmatch(r'product:[1-9][0-9]*',ref):raise SubmissionConflict
            original=Product.query.filter_by(id=int(ref.split(':')[1]),business_id=business.id).first()
            if not original or pid is not None and original.id!=pid:raise SubmissionConflict
            db.session.rollback();return page('This product submission was already saved.',200)
        if duplicate_errors(business.id,data,pid):raise SubmissionConflict
        if pid is not None:
            product=Product.query.filter_by(id=pid,business_id=business.id,active=True).with_for_update().populate_existing().first_or_404()
            if submissions.fingerprint(product_fields(product))!=revision:raise SubmissionConflict
            for field in EDIT_FIELDS:setattr(product,field,data[field])
            product.updated_at=datetime.utcnow();action='PRODUCT_UPDATED'
        else:product=add_product(business.id,data);action='PRODUCT_CREATED'
        db.session.flush();log(action,f'Product {product.id} saved by Staff.',actor=current_user._get_current_object(),business_id=business.id,target_type='product',target_id=product.id)
        complete(receipt,f'product:{product.id}');db.session.commit()
    except (SubmissionConflict,IntegrityError):
        db.session.rollback();return page('Product changed, SKU is already used, or submission conflicts. Check the catalogue and reopen the form.',409)
    except (ValueError,SubmissionError):
        db.session.rollback();return page('Product not saved. Check the fields and submission reference.',400)
    except Exception:
        db.session.rollback();raise
    return redirect(url_for('staff.products',membership_id=member.id,business_id=business.id),code=303)


@staff_bp.route(BASE+'/products/new',methods=['GET','POST'])
@login_required
def product_create(membership_id,business_id):return product_form(membership_id,business_id)


@staff_bp.route(BASE+'/products/<int:product_id>/edit',methods=['GET','POST'])
@login_required
def product_edit(membership_id,business_id,product_id):return product_form(membership_id,business_id,product_id)


@staff_bp.route(BASE+'/expenses',methods=['GET','POST'])
@login_required
def staff_expenses(membership_id,business_id):
    member,business=authority(membership_id,business_id,('expenses_create',) if request.method=='POST'
                              else ('expenses_view','expenses_create'),write=request.method=='POST')
    permissions=set(roles.resolve(member.owner_id,member.role).permissions)
    key=request.form.get('submission_token','') if request.method=='POST' else (
        submissions.issue(member,business,'expense') if 'expenses_create' in permissions else None)
    def page(message=None,status=200):
        member,business=authority(membership_id,business_id,('expenses_view','expenses_create'))
        permissions=set(roles.resolve(member.owner_id,member.role).permissions)
        rows=None
        if 'expenses_view' in permissions:
            rows=db.session.query(Expense.id,Expense.spent_at,Expense.description,Expense.category,Expense.amount,Expense.voided_at).filter(
                Expense.business_id==business.id).order_by(Expense.spent_at.desc(),Expense.id.desc()).paginate(
                page=max(1,request.args.get('page',1,type=int)),per_page=20,error_out=False)
        return render_template('staff/expenses.html',member=member,business=business,rows=rows,
            can_create='expenses_create' in permissions,categories=CATEGORIES,submission_token=key,
            error=message if status>=400 else None,notice=message),status
    if request.method=='GET':return page()
    try:
        if set(request.form)-{'description','category','amount','note','csrf_token','submission_token'}:raise ValueError
        amount=Decimal(request.form.get('amount',''))
        description=request.form.get('description','').strip();category=request.form.get('category','');note=request.form.get('note','').strip()
        if (not amount.is_finite() or not 0<amount<=Decimal('9999999999.99') or amount.as_tuple().exponent < -2
                or not description or len(description)>180 or category not in CATEGORIES or len(note)>500):raise ValueError
        intent=dict(description=description,category=category,amount=format(amount,'.2f'),note=note)
        receipt,replay,_=submissions.claim(key,member,business,'expense',intent)
        if replay:
            ref=receipt.result_reference
            if not isinstance(ref,str) or not re.fullmatch(r'expense:[1-9][0-9]*',ref):raise SubmissionConflict
            if not Expense.query.filter_by(id=int(ref.split(':')[1]),business_id=business.id).first():raise SubmissionConflict
            db.session.rollback();return page('This expense was already recorded.',200)
        row=create_expense(business,description=description,amount=amount,category=category,note=note,
                           actor=current_user._get_current_object(),spent_at=datetime.utcnow())
        complete(receipt,f'expense:{row.id}');db.session.commit()
    except SubmissionConflict:
        db.session.rollback();return page('Submission conflicts with an earlier expense. Check with the owner.',409)
    except (ValueError,InvalidOperation,SubmissionError):
        db.session.rollback();return page('Expense not saved. Check description, category, amount and reference.',400)
    except Exception:
        db.session.rollback();raise
    return redirect(url_for('staff.staff_expenses',membership_id=member.id,business_id=business.id,recorded=1),code=303)


@staff_bp.get(BASE+'/reports')
@login_required
def staff_reports(membership_id,business_id):
    member,business=authority(membership_id,business_id,('financial_view',))
    today=datetime.utcnow().date()
    try:
        start=datetime.strptime(request.args.get('start',today.replace(day=1).isoformat()),'%Y-%m-%d')
        last=datetime.strptime(request.args.get('end',today.isoformat()),'%Y-%m-%d')
        if not 2000<=start.year<=2100 or not 2000<=last.year<=2100 or not 0<=(last-start).days<=365:raise ValueError
        end=last+timedelta(days=1)
    except ValueError:abort(400)
    from app.main.reporting import financial_totals
    totals=financial_totals(business.id,start,end)
    return render_template('staff/reports.html',member=member,business=business,totals=totals,
                           start=start.date().isoformat(),end=last.date().isoformat())
