"""In-memory Staff inventory/restock integration; no PostgreSQL or external I/O."""
from datetime import datetime,timedelta
from decimal import Decimal
import re
from unittest.mock import patch
import pytest
from app import db
from app.models import (Product,Restock,StockMovement,TransactionSubmission,AuditLog,
    StaffRole,StaffMembershipBusiness,RecurringSubscription,Notification)
from app.staff import stock_submissions,sales_submissions
from tests.test_staff_foundation import app,seed,issue,accept_new,login,NOW,staff_clock


@pytest.fixture(autouse=True)
def stock_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return NOW
    monkeypatch.setattr('app.staff.stock_access.datetime',Clock)
    monkeypatch.setattr('app.restocking.ledger.datetime',Clock)


def setup(app,permissions=None):
    app.config['STAFF_STOCK_ENABLED']='true'
    owner,shops=seed('pro',businesses=2)
    _,raw=issue(owner,shops,role='manager');user,member=accept_new(raw)
    if permissions is not None:db.session.get(StaffRole,(owner.id,member.role)).permissions=permissions
    product=Product(business_id=shops[0].id,name='Assigned stock',stock_quantity=20,opening_quantity=20,
                    selling_price=Decimal('6543.21'),buying_price=Decimal('9876.54'))
    db.session.add(product);db.session.commit();client=app.test_client();login(client,user)
    return owner,shops,user,member,product,client


def path(member,shop,view='restocking'):
    return f'/staff/memberships/{member.id}/businesses/{shop.id}/{view}'


def token(client,url):
    response=client.get(url);assert response.status_code==200
    return re.search(r'name="submission_token" value="([^"]+)"',response.get_data(as_text=True))[1]


def payload(product,key):
    return dict(product_id=str(product.id),quantity='2',unit_cost='12.50',supplier='Synthetic supplier',note='Received goods',submission_token=key)


def counts():return tuple(m.query.count() for m in (Restock,StockMovement,TransactionSubmission))


def test_manager_can_view_assigned_inventory_and_record_atomic_restock(app):
    owner,shops,user,member,product,client=setup(app)
    response=client.get(path(member,shops[0],'inventory'));html=response.get_data(as_text=True)
    assert response.status_code==200 and 'Assigned stock' in html
    assert not any(cost in html for cost in ('9876.54','9,876.54','6543.21','6,543.21'))
    url=path(member,shops[0]);data=payload(product,token(client,url))
    assert client.post(url,data=data).status_code==303
    db.session.expire_all();assert product.stock_quantity==22 and product.buying_price==Decimal('12.50')
    receipt=Restock.query.one();assert receipt.quantity==2 and receipt.unit_cost==Decimal('12.50')
    assert receipt.supplier=='Synthetic supplier' and receipt.note=='Received goods'
    assert StockMovement.query.one().quantity_change==2
    submission=TransactionSubmission.query.one()
    assert submission.user_id==user.id and submission.user_id!=owner.id
    assert submission.operation=='restock' and submission.result_reference==receipt.batch_id and submission.completed_at
    audit=AuditLog.query.filter_by(action='RESTOCK_CREATED').one()
    assert audit.actor_id==user.id and audit.business_id==shops[0].id
    assert Notification.query.filter_by(kind='restock_recorded').count()==1
    html=client.get(url).get_data(as_text=True)
    assert 'Recorded restocks' in html and 'Assigned stock' in html
    assert '12.50' not in html and 'Received goods' not in html
    assert client.post(url,data=data).status_code==200
    assert counts()==(1,1,1) and product.stock_quantity==22
    assert Notification.query.filter_by(kind='restock_recorded').count()==1
    data['quantity']='3';assert client.post(url,data=data).status_code==409
    assert counts()==(1,1,1)


@pytest.mark.parametrize('flag',['',False,True,'TRUE','false',None])
def test_stock_flag_requires_exact_true(app,flag):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    app.config['STAFF_STOCK_ENABLED']=flag
    assert client.get(url).status_code==client.get(path(member,shops[0],'inventory')).status_code==403
    assert client.post(url,data=payload(product,key)).status_code==403
    assert counts()==(0,0,0)


@pytest.mark.parametrize('permissions',[[],['sales_view','sales_create'],['financial_view'],['products_create','products_edit'],['records_export']])
def test_other_permissions_do_not_grant_stock_access(app,permissions):
    _,shops,_,member,product,client=setup(app,permissions)
    assert client.get(path(member,shops[0],'inventory')).status_code==403
    assert client.get(path(member,shops[0])).status_code==403
    assert client.post(path(member,shops[0]),data=payload(product,'invalid')).status_code==403
    assert counts()==(0,0,0)


@pytest.mark.parametrize('permission',['inventory_view','restocking_view','restocking_create'])
def test_stock_permissions_are_independent(app,permission):
    _,shops,_,member,product,client=setup(app,[permission])
    inventory=client.get(path(member,shops[0],'inventory'))
    assert inventory.status_code==(200 if permission=='inventory_view' else 403)
    url=path(member,shops[0]);response=client.get(url)
    assert response.status_code==(403 if permission=='inventory_view' else 200)
    html=response.get_data(as_text=True)
    assert ('name="submission_token"' in html)==(permission=='restocking_create')
    assert ('Recorded restocks' in html)==(permission=='restocking_view')
    if permission=='restocking_create':
        assert client.post(url,data=payload(product,token(client,url))).status_code==303
        assert 'Recorded restocks' not in client.get(url).get_data(as_text=True)
    else:assert client.post(url,data=payload(product,'invalid')).status_code==403


@pytest.mark.parametrize('change',['revoked','unverified','suspended','role','assignment','business',
    'expired','downgrade','owner_suspended','archived_role'])
def test_current_authority_rejects_old_restock_form(app,change):
    owner,shops,user,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    if change=='revoked':member.state='revoked'
    if change=='unverified':user.email_verified_at=None
    if change=='suspended':user.suspended_at=NOW
    if change=='role':db.session.get(StaffRole,(owner.id,member.role)).permissions=['restocking_view']
    if change=='assignment':StaffMembershipBusiness.query.filter_by(membership_id=member.id).delete()
    if change=='business':shops[0].suspended_at=NOW
    if change=='expired':member.access_until=NOW-timedelta(seconds=1)
    if change=='downgrade':RecurringSubscription.query.filter_by(user_id=owner.id).one().plan_code='plus'
    if change=='owner_suspended':owner.suspended_at=NOW
    if change=='archived_role':db.session.get(StaffRole,(owner.id,member.role)).archived=True
    db.session.commit()
    assert client.post(url,data=data).status_code in (302,403)
    assert counts()==(0,0,0) and product.stock_quantity==20


def test_foreign_business_product_membership_and_session_selection_cannot_widen_scope(app):
    owner,shops,user,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    foreign=Product(business_id=shops[1].id,name='FOREIGN STOCK SECRET',stock_quantity=7,buying_price=123)
    db.session.add(foreign);db.session.commit()
    with client.session_transaction() as session:session['business_id']=shops[1].id
    for view in ('inventory','restocking'):assert client.get(path(member,shops[1],view)).status_code==403
    assert client.post(url,data=payload(foreign,key)).status_code==400
    _,raw=issue(owner,shops,email='different-stock@example.invalid');_,other=accept_new(raw)
    assert client.get(path(other,shops[0],'inventory')).status_code==403
    assert 'FOREIGN STOCK SECRET' not in client.get(url).get_data(as_text=True)
    assert counts()==(0,0,0) and foreign.stock_quantity==7


@pytest.mark.parametrize('field,value',[
    ('quantity','0'),('quantity','-1'),('quantity','1.5'),('quantity','2147483648'),
    ('unit_cost','NaN'),('unit_cost','Infinity'),('unit_cost','-1'),('unit_cost','1.001'),
    ('unit_cost','10000000000'),('unit_cost',''),('date','2000-01-01'),('received_at','2000'),
    ('selling_price','1'),('stock_quantity','999'),('owner_id','1'),('business_id','2'),
    ('supplier','X'*141),('note','X'*501),('submission_token','invalid')])
def test_invalid_or_forged_restock_fields_leave_no_mutations(app,field,value):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url));data[field]=value
    assert client.post(url,data=data).status_code==400
    db.session.expire_all();assert counts()==(0,0,0) and product.stock_quantity==20 and product.buying_price==Decimal('9876.54')


@pytest.mark.parametrize('change',['inactive','overflow','duplicate','missing_cost'])
def test_invalid_product_set_never_creates_partial_receipt(app,change):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    if change=='inactive':product.active=False
    if change=='overflow':product.stock_quantity=2147483647
    if change=='duplicate':data.update(product_id=[str(product.id)]*2,quantity=['1','2'],unit_cost=['1','2'])
    if change=='missing_cost':data['unit_cost']=[]
    db.session.commit();before=product.stock_quantity
    assert client.post(url,data=data).status_code==400
    db.session.expire_all();assert counts()==(0,0,0) and product.stock_quantity==before


@pytest.mark.parametrize('failure',['commit','second_update','notification'])
def test_all_restock_side_effects_rollback_together_and_same_token_can_retry(app,monkeypatch,failure):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    second=Product(business_id=shops[0].id,name='Second stock',stock_quantity=3,buying_price=5);db.session.add(second);db.session.commit()
    data=dict(product_id=[str(product.id),str(second.id)],quantity=['2','1'],unit_cost=['12.50','6.00'],submission_token=key)
    session=db.session();original_execute=session.execute;calls=[]
    def execute(statement,*args,**kw):
        if str(statement).startswith('UPDATE product SET'):
            calls.append(True)
            if len(calls)==2:raise RuntimeError('Synthetic ledger failure')
        return original_execute(statement,*args,**kw)
    target='app.notifications.service.notify' if failure=='notification' else None
    with monkeypatch.context() as m:
        if failure=='commit':m.setattr(session,'commit',lambda:(_ for _ in ()).throw(RuntimeError('Synthetic commit failure')))
        if failure=='second_update':m.setattr(session,'execute',execute)
        if target:m.setattr(target,lambda *a,**k:(_ for _ in ()).throw(RuntimeError('Synthetic notification failure')))
        with pytest.raises(RuntimeError):client.post(url,data=data)
    db.session.expire_all();assert counts()==(0,0,0) and product.stock_quantity==20 and second.stock_quantity==3
    assert Notification.query.filter_by(kind='restock_recorded').count()==0
    assert AuditLog.query.filter_by(action='RESTOCK_CREATED').count()==0
    assert client.post(url,data=data).status_code==303
    assert counts()==(2,2,1)


def test_employee_tokens_are_bound_to_operation_member_owner_and_business(app):
    owner,shops,user,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    for field,value in [('user_id',owner.id),('owner_id',999),('membership_id',999),('business_id',shops[1].id),('operation','sale'),('version',True)]:
        raw=stock_submissions.signer().loads(key);raw[field]=value
        assert client.post(url,data=payload(product,stock_submissions.signer().dumps(raw))).status_code==400
    sale_key=sales_submissions.issue(member,shops[0])
    assert client.post(url,data=payload(product,sale_key)).status_code==400
    from app.transaction_submissions import issue_token
    assert client.post(url,data=payload(product,issue_token(owner.id,shops[0].id,'restock'))).status_code==400
    assert counts()==(0,0,0)


def test_csrf_and_owner_surfaces_are_not_bypassed(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post(url,data=payload(product,key)).status_code==400
    html=client.get(url).get_data(as_text=True);csrf=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
    assert client.post(url,data=dict(payload(product,key),csrf_token=csrf)).status_code==303
    for owner_path in ('/products/','/restocking/','/expenses/','/reports'):
        assert client.get(owner_path).status_code==403


def test_stock_links_and_permissions_are_independent_from_sales_flag(app):
    owner,shops,_,member,_,client=setup(app,['inventory_view','restocking_view'])
    app.config['STAFF_SALES_ENABLED']=''
    html=client.get('/staff/').get_data(as_text=True)
    assert path(member,shops[0],'inventory') in html and path(member,shops[0]) in html
    assert path(member,shops[1]) not in html and '>Sales</a>' not in html
    app.config['STAFF_STOCK_ENABLED']=''
    html=client.get('/staff/').get_data(as_text=True)
    assert '>Inventory</a>' not in html and '>Restocking</a>' not in html


def test_literal_search_and_pagination_do_not_load_or_leak_cost_columns(app):
    _,shops,_,member,product,client=setup(app,['inventory_view','restocking_view','financial_view'])
    product.name='Exact %_ stock';db.session.add(Product(business_id=shops[0].id,name='Other secret',buying_price=9999));db.session.commit()
    from sqlalchemy import event
    queries=[]
    def capture(conn,cursor,statement,*args):queries.append(statement)
    event.listen(db.engine,'before_cursor_execute',capture)
    try:
        response=client.get(path(member,shops[0],'inventory')+'?q=%25_');html=response.get_data(as_text=True)
        assert 'Exact %_ stock' in html and 'Other secret' not in html
        client.get(path(member,shops[0]))
    finally:event.remove(db.engine,'before_cursor_execute',capture)
    selects=[q.lower() for q in queries if q.lstrip().lower().startswith('select') and ('from product' in q.lower() or 'from restock' in q.lower())]
    assert selects and all(not any(c in q for c in ('buying_price','unit_cost','selling_price')) for q in selects)


def test_staff_restocks_lock_owner_before_receipt_and_product_ledger(app):
    owner,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    from app.staff import stock_access,stock_routes
    events=[];real_lock=stock_access.service.lock_owner;real_claim=stock_submissions.claim;real_ledger=stock_routes.receive_stock
    def lock(identifier):
        assert identifier==owner.id;events.append('owner');return real_lock(identifier)
    def claim(*args,**kwargs):
        assert events==['owner'];events.append('receipt');return real_claim(*args,**kwargs)
    def ledger(*args,**kwargs):
        assert events==['owner','receipt'];events.append('ledger');return real_ledger(*args,**kwargs)
    with patch('app.staff.service.lock_owner',side_effect=lock),patch('app.staff.stock_submissions.claim',side_effect=claim),patch('app.staff.stock_routes.receive_stock',side_effect=ledger):
        assert client.post(url,data=data).status_code==303
    assert events==['owner','receipt','ledger']


def test_expired_restock_reference_never_changes_stock(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0])
    with patch('itsdangerous.timed.TimestampSigner.get_timestamp',return_value=1):
        key=stock_submissions.issue(member,shops[0])
    assert client.post(url,data=payload(product,key)).status_code==400
    assert counts()==(0,0,0) and product.stock_quantity==20


def test_mixed_business_items_roll_back_the_entire_batch(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);key=token(client,url)
    foreign=Product(business_id=shops[1].id,name='Other shop',stock_quantity=5,buying_price=9);db.session.add(foreign);db.session.commit()
    data=dict(product_id=[str(product.id),str(foreign.id)],quantity=['2','2'],unit_cost=['10','11'],submission_token=key)
    assert client.post(url,data=data).status_code==400
    db.session.expire_all();assert counts()==(0,0,0) and product.stock_quantity==20 and foreign.stock_quantity==5
