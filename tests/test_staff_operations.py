"""Synthetic in-memory integration; no hosted databases, providers or transport."""
from datetime import datetime,timedelta
from decimal import Decimal
import re
from unittest.mock import patch
import pytest
from app import db
from app.models import (Product,Expense,Sale,SaleItem,StockMovement,TransactionSubmission,
    StaffRole,StaffMembershipBusiness,AuditLog,User)
from app.staff import operations_submissions as submissions,roles,defaults
from tests.test_staff_foundation import app,seed,issue,accept_new,login,NOW,staff_clock


@pytest.fixture(autouse=True)
def operations_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return NOW
    monkeypatch.setattr('app.staff.operations_access.datetime',Clock)
    monkeypatch.setattr('app.staff.operations_routes.datetime',Clock)
    monkeypatch.setattr('app.expenses.ledger.datetime',Clock)


def setup(app,permissions=None):
    app.config['STAFF_OPERATIONS_ENABLED']='true'
    owner,shops=seed('pro',businesses=2);_,raw=issue(owner,shops,role='manager');user,member=accept_new(raw)
    if permissions is not None:db.session.get(StaffRole,(owner.id,member.role)).permissions=permissions
    product=Product(business_id=shops[0].id,name='Assigned product',sku='START',stock_quantity=5,opening_quantity=5,buying_price=Decimal('9876.54'),selling_price=20)
    db.session.add(product);db.session.commit();client=app.test_client();login(client,user)
    return owner,shops,user,member,product,client


def path(member,shop,suffix):return f'/staff/memberships/{member.id}/businesses/{shop.id}/{suffix}'


def key(client,url):
    response=client.get(url);assert response.status_code==200
    return re.search(r'name="submission_token" value="([^"]+)"',response.get_data(as_text=True))[1]


def product_data(token):
    return dict(name='New product',sku='NEW',unit='unit',selling_price='20.00',buying_price='8.00',stock_quantity='3',submission_token=token)


def expense_data(token):return dict(description='Delivery',category='Transportation',amount='12.50',note='Local delivery',submission_token=token)


def test_manager_new_defaults_and_create_edit_expenses_reports(app):
    owner,shops,user,member,product,client=setup(app)
    assert 'financial_view' in roles.resolve(owner.id,member.role).permissions
    url=path(member,shops[0],'products/new');data=product_data(key(client,url))
    assert client.post(url,data=data).status_code==303
    created=Product.query.filter_by(sku='NEW').one();assert created.opening_quantity==created.stock_quantity==3 and created.buying_price==8
    assert StockMovement.query.one().kind=='opening'
    assert client.post(url,data=data).status_code==200 and Product.query.count()==2
    edit=path(member,shops[0],f'products/{product.id}/edit');reference=key(client,edit)
    html=client.get(edit).get_data(as_text=True);assert '9876.54' not in html and 'name="buying_price"' not in html and 'name="stock_quantity"' not in html
    data=dict(name='Edited product',sku='START',unit='box',selling_price='25.00',submission_token=reference)
    assert client.post(edit,data=data).status_code==303
    db.session.expire_all();assert product.name=='Edited product' and product.selling_price==25 and product.buying_price==Decimal('9876.54') and product.stock_quantity==5
    assert client.post(edit,data=data).status_code==200
    expenses=path(member,shops[0],'expenses');data=expense_data(key(client,expenses))
    assert client.post(expenses,data=data).status_code==303
    assert Expense.query.one().amount==Decimal('12.50')
    assert client.post(expenses,data=data).status_code==200 and Expense.query.count()==1
    data['amount']='13.00';assert client.post(expenses,data=data).status_code==409
    for action in ('PRODUCT_CREATED','PRODUCT_UPDATED','EXPENSE_CREATED'):
        assert AuditLog.query.filter_by(action=action).one().actor_id==user.id
    assert all(r.user_id==user.id and r.completed_at for r in TransactionSubmission.query.all())
    report=client.get(path(member,shops[0],'reports'));assert report.status_code==200 and b'Operating expenses' in report.data


@pytest.mark.parametrize('flag',['',False,True,'false','TRUE',None])
def test_disabled_or_malformed_flag_blocks_all_new_routes(app,flag):
    _,shops,_,member,product,client=setup(app);app.config['STAFF_OPERATIONS_ENABLED']=flag
    for suffix in ('products','products/new',f'products/{product.id}/edit','expenses','reports'):
        assert client.get(path(member,shops[0],suffix)).status_code==403
    assert client.post(path(member,shops[0],'expenses'),data=expense_data('invalid')).status_code==403


@pytest.mark.parametrize('permissions,allowed',[
    (['products_create'],{'products','products/new'}),(['products_edit'],{'products','edit'}),
    (['expenses_view'],{'expenses'}),(['expenses_create'],{'expenses'}),(['financial_view'],{'reports'}),
    (['sales_view','inventory_view','restocking_create'],set())])
def test_each_permission_is_independent(app,permissions,allowed):
    _,shops,_,member,product,client=setup(app,permissions)
    for name,suffix in [('products','products'),('products/new','products/new'),('edit',f'products/{product.id}/edit'),('expenses','expenses'),('reports','reports')]:
        assert client.get(path(member,shops[0],suffix)).status_code==(200 if name in allowed else 403)
    html=client.get(path(member,shops[0],'expenses')).get_data(as_text=True)
    assert ('name="amount"' in html)==('expenses_create' in permissions)
    assert ('Recorded expenses' in html)==('expenses_view' in permissions)
    if permissions==['products_create']:assert 'Assigned product' not in client.get(path(member,shops[0],'products')).get_data(as_text=True)


@pytest.mark.parametrize('change',['revoked','role','assignment','unverified','expired','business'])
@pytest.mark.parametrize('operation',['create','edit','expense'])
def test_live_authority_changes_reject_old_forms_without_mutation(app,change,operation):
    owner,shops,user,member,product,client=setup(app)
    suffix={'create':'products/new','edit':f'products/{product.id}/edit','expense':'expenses'}[operation]
    url=path(member,shops[0],suffix);reference=key(client,url)
    data=expense_data(reference) if operation=='expense' else product_data(reference)
    if operation=='edit':data=dict(name='Tampered',selling_price='1',submission_token=reference)
    if change=='revoked':member.state='revoked'
    if change=='role':db.session.get(StaffRole,(owner.id,member.role)).permissions=[]
    if change=='assignment':StaffMembershipBusiness.query.filter_by(membership_id=member.id).delete()
    if change=='unverified':user.email_verified_at=None
    if change=='expired':member.access_until=NOW-timedelta(seconds=1)
    if change=='business':shops[0].suspended_at=NOW
    db.session.commit()
    assert client.post(url,data=data).status_code in (302,403)
    assert Product.query.count()==1 and Expense.query.count()==TransactionSubmission.query.count()==0
    assert product.name=='Assigned product' and product.stock_quantity==5


@pytest.mark.parametrize('field,value',[('stock_quantity','999'),('opening_quantity','999'),('buying_price','0.01'),('business_id','2'),('active','false'),('owner_id','999')])
def test_edit_rejects_stock_cost_ownership_and_archiving_fields(app,field,value):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0],f'products/{product.id}/edit')
    data=dict(name='Changed',selling_price='2',submission_token=key(client,url));data[field]=value
    assert client.post(url,data=data).status_code==400
    db.session.expire_all();assert product.stock_quantity==product.opening_quantity==5 and product.buying_price==Decimal('9876.54') and product.active
    assert TransactionSubmission.query.count()==0


def test_stale_edit_form_cannot_overwrite_owner_changes(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0],f'products/{product.id}/edit');reference=key(client,url)
    product.name='Owner changed';db.session.commit()
    assert client.post(url,data=dict(name='Old form',selling_price='20',submission_token=reference)).status_code==409
    assert product.name=='Owner changed' and TransactionSubmission.query.count()==0


@pytest.mark.parametrize('field,value',[('amount','NaN'),('amount','Infinity'),('amount','0'),('amount','-1'),('amount','1.001'),('amount','10000000000'),('description',''),('category','Foreign'),('spent_at','2000-01-01'),('business_id','2')])
def test_expense_invalid_fields_do_not_create_rows(app,field,value):
    _,shops,_,member,_,client=setup(app);url=path(member,shops[0],'expenses');data=expense_data(key(client,url));data[field]=value
    assert client.post(url,data=data).status_code==400
    assert Expense.query.count()==TransactionSubmission.query.count()==0


def test_reports_reuse_snapshot_accounting_and_exclude_unassigned_business(app):
    _,shops,_,member,product,client=setup(app)
    sale=Sale(business_id=shops[0].id,sold_at=NOW);db.session.add(sale);db.session.flush()
    db.session.add(SaleItem(sale_id=sale.id,product_id=product.id,quantity=2,unit_price=20,unit_cost=8))
    db.session.add_all([Expense(business_id=shops[0].id,description='Local',amount=3,spent_at=NOW),
        Expense(business_id=shops[0].id,description='Voided',amount=999,spent_at=NOW,voided_at=NOW),
        Expense(business_id=shops[1].id,description='Foreign',amount=7777777,spent_at=NOW)])
    db.session.commit();product.buying_price=5000;db.session.commit()
    url=path(member,shops[0],'reports');html=client.get(url).get_data(as_text=True)
    for amount in ('40.00','16.00','24.00','3.00','21.00'):assert amount in html
    assert '7,777,777' not in html and '5,000.00' not in html
    assert client.get(path(member,shops[1],'reports')).status_code==403
    assert client.get(url+'?start=2000-01-01&end=2100-01-01').status_code==400
    assert client.get(url+'?start=bad').status_code==400
    assert client.get(url+'?start=2026-11-01&end=2026-10-01').status_code==400


def test_foreign_product_and_operation_references_fail(app):
    _,shops,_,member,product,client=setup(app);new=path(member,shops[0],'products/new');reference=key(client,new)
    assert client.post(path(member,shops[0],'expenses'),data=expense_data(reference)).status_code==400
    foreign=Product(business_id=shops[1].id,name='Foreign',selling_price=1);db.session.add(foreign);db.session.commit()
    assert client.get(path(member,shops[0],f'products/{foreign.id}/edit')).status_code==404
    for suffix in ('products/new','expenses','reports'):assert client.get(path(member,shops[1],suffix)).status_code==403


@pytest.mark.parametrize('operation',['create','edit','expense'])
def test_atomic_commit_failure_rolls_back_and_reference_can_retry(app,operation,monkeypatch):
    _,shops,_,member,product,client=setup(app)
    suffix={'create':'products/new','edit':f'products/{product.id}/edit','expense':'expenses'}[operation]
    url=path(member,shops[0],suffix);reference=key(client,url)
    data=expense_data(reference) if operation=='expense' else product_data(reference)
    if operation=='edit':data=dict(name='Edited',selling_price='30',submission_token=reference)
    with monkeypatch.context() as m:
        m.setattr(db.session(),'commit',lambda:(_ for _ in ()).throw(RuntimeError('Synthetic commit failure')))
        with pytest.raises(RuntimeError):client.post(url,data=data)
    db.session.expire_all();assert Product.query.count()==1 and product.name=='Assigned product' and product.selling_price==20
    assert Expense.query.count()==TransactionSubmission.query.count()==StockMovement.query.count()==0
    assert client.post(url,data=data).status_code==303


def test_csrf_and_owner_routes_remain_protected(app):
    _,shops,_,member,_,client=setup(app);url=path(member,shops[0],'expenses');data=expense_data(key(client,url));app.config['WTF_CSRF_ENABLED']=True
    assert client.post(url,data=data).status_code==400
    html=client.get(url).get_data(as_text=True);data['csrf_token']=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
    assert client.post(url,data=data).status_code==303
    for url in ('/products/','/expenses/','/reports','/businesses/'):
        assert client.get(url).status_code==403


def test_existing_team_defaults_plan_apply_is_audited_and_preserves_custom_roles_and_scope(app):
    owner,shops,user,member,_,_=setup(app)
    manager=roles.resolve(owner.id,'manager');manager.permissions=[p for p in manager.permissions if p!='financial_view'];db.session.commit()
    custom=roles.save(owner,None,'Custom role',['inventory_view'],now=NOW)
    admin=User(full_name='Admin',email='admin-defaults@example.invalid',role='admin',email_verified_at=NOW,password_hash='synthetic-unused')
    db.session.add(admin);db.session.commit();before_assignments=[(r.membership_id,r.business_id) for r in StaffMembershipBusiness.query.all()]
    preview=defaults.plan();assert preview['changed']==1 and preview['owners']==1
    assert 'financial_view' not in manager.permissions
    assert defaults.apply(preview['fingerprint'],admin)==1;db.session.commit()
    assert 'financial_view' in roles.resolve(owner.id,'manager').permissions
    assert custom.permissions==['inventory_view'] and before_assignments==[(r.membership_id,r.business_id) for r in StaffMembershipBusiness.query.all()]
    assert AuditLog.query.filter_by(action='STAFF_DEFAULTS_UPDATED').one().actor_id==admin.id
    assert defaults.apply(defaults.plan()['fingerprint'],admin)==0;db.session.rollback()


def test_defaults_reject_non_admin_and_changed_plan(app):
    owner,_,_,_,_,_=setup(app);preview=defaults.plan()
    with pytest.raises(ValueError):defaults.apply(preview['fingerprint'],owner)
    admin=User(full_name='Admin',email='admin2@example.invalid',role='admin',password_hash='synthetic-unused');db.session.add(admin);db.session.commit()
    roles.resolve(owner.id,'manager').permissions=['sales_view'];db.session.commit()
    with pytest.raises(ValueError):defaults.apply(preview['fingerprint'],admin)
    db.session.rollback();assert AuditLog.query.filter_by(action='STAFF_DEFAULTS_UPDATED').count()==0


def test_defaults_preserve_owner_grants_names_archives_and_rollback_atomically(app,monkeypatch):
    owner,_,_,_,_,_=setup(app)
    manager=roles.resolve(owner.id,'manager')
    manager.name='My manager';manager.archived=True
    manager.permissions=['sales_view','staff_admin','records_export']
    db.session.add(User(full_name='Admin',email='atomic-admin@example.invalid',role='admin',password_hash='synthetic-unused'))
    db.session.commit()
    admin=User.query.filter_by(email='atomic-admin@example.invalid').one()
    preview=defaults.plan();before=defaults.snapshot()
    with monkeypatch.context() as m:
        m.setattr(roles,'record',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('Synthetic audit failure')))
        with pytest.raises(RuntimeError):defaults.apply(preview['fingerprint'],admin)
    db.session.rollback()
    assert defaults.snapshot()==before
    assert AuditLog.query.filter_by(action='STAFF_DEFAULTS_UPDATED').count()==0
    assert defaults.apply(preview['fingerprint'],admin)==1
    db.session.commit()
    manager=db.session.get(StaffRole,(owner.id,'manager'))
    assert manager.name=='My manager' and manager.archived is True
    assert set(manager.permissions)==set(roles.TEMPLATES['manager'][1])|{'staff_admin','records_export'}
