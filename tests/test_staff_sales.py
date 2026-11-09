"""Disconnected Staff sales integration; no PostgreSQL or external services."""
from datetime import datetime, timedelta
from decimal import Decimal
import re
from unittest.mock import patch
import pytest
from app import db
from app.models import (Product, Sale, SaleItem, StockMovement, AuditLog,
                        TransactionSubmission, StaffRole, StaffMembershipBusiness)
from app.staff import sales_access, sales_submissions
from tests.test_staff_foundation import app, seed, issue, accept_new, login, NOW, staff_clock


@pytest.fixture(autouse=True)
def sales_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls): return NOW
    monkeypatch.setattr('app.staff.sales_access.datetime', Clock)
    monkeypatch.setattr('app.staff.sales_routes.datetime', Clock)
    monkeypatch.setattr('app.sales.ledger.datetime', Clock)


def setup(app, *, permission=None, verify=True):
    app.config['STAFF_SALES_ENABLED']='true'
    owner,shops=seed('pro',businesses=2)
    _,raw=issue(owner,shops)
    user,member=accept_new(raw,verify=verify)
    if permission is not None:
        role=db.session.get(StaffRole,(owner.id,member.role))
        role.permissions=permission
    product=Product(business_id=shops[0].id,name='Public sale product',stock_quantity=20,
                    selling_price=Decimal('17.25'),buying_price=Decimal('9876.54'))
    db.session.add(product);db.session.commit()
    client=app.test_client();login(client,user)
    return owner,shops,user,member,product,client


def path(member,business):
    return f'/staff/memberships/{member.id}/businesses/{business.id}/sales'


def token(client,url):
    response=client.get(url)
    assert response.status_code==200
    return re.search(r'name="submission_token" value="([^"]+)"',response.get_data(as_text=True))[1]


def payload(product,reference,**kw):
    return dict(product_id=str(product.id),quantity='2',payment_method='Cash',
                note='Synthetic sale',submission_token=reference,**kw)


def counts():
    return tuple(model.query.count() for model in (Sale,SaleItem,StockMovement,TransactionSubmission))


def test_staff_sale_atomic_ledger_actual_actor_and_idempotent_replay(app):
    owner,shops,user,member,product,client=setup(app)
    url=path(member,shops[0]);reference=token(client,url);data=payload(product,reference)
    assert client.post(url,data=data).status_code==303
    db.session.expire_all()
    assert product.stock_quantity==18 and counts()==(1,1,1,1)
    item=SaleItem.query.one();assert item.unit_price==Decimal('17.25') and item.unit_cost==Decimal('9876.54')
    receipt=TransactionSubmission.query.one()
    assert receipt.user_id==user.id and receipt.business_id==shops[0].id and receipt.completed_at
    audit=AuditLog.query.filter_by(action='SALE_CREATED').one()
    assert audit.actor_id==user.id and audit.actor_id!=owner.id and audit.business_id==shops[0].id
    product.selling_price=Decimal('99.00');db.session.commit()
    assert client.post(url,data=data).status_code==200
    db.session.expire_all();assert counts()==(1,1,1,1) and product.stock_quantity==18
    assert SaleItem.query.one().unit_price==Decimal('17.25')
    data['quantity']='3';assert client.post(url,data=data).status_code==409
    assert counts()==(1,1,1,1)


@pytest.mark.parametrize('setting',['',False,True,'TRUE','false',None])
def test_disabled_or_malformed_flag_blocks_reads_and_writes(app,setting):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);reference=token(client,url)
    app.config['STAFF_SALES_ENABLED']=setting
    assert client.get(url).status_code==403
    assert client.post(url,data=payload(product,reference)).status_code==403
    assert counts()==(0,0,0,0)


@pytest.mark.parametrize('permission',[[],['inventory_view'],['financial_view'],['records_export'],['staff_admin']])
def test_non_sales_permissions_do_not_grant_sales(app,permission):
    _,shops,_,member,product,client=setup(app,permission=permission);url=path(member,shops[0])
    assert client.get(url).status_code==403
    assert client.post(url,data=payload(product,'invalid')).status_code==403
    assert counts()==(0,0,0,0)


def test_view_only_has_history_but_no_sale_form_catalogue_or_costs(app):
    _,shops,_,member,product,client=setup(app,permission=['sales_view'])
    row=Sale(business_id=shops[0].id);db.session.add(row);db.session.flush()
    db.session.add(SaleItem(sale_id=row.id,product_id=product.id,quantity=1,unit_price=17.25,unit_cost=9876.54));db.session.commit()
    response=client.get(path(member,shops[0]));html=response.get_data(as_text=True)
    assert response.status_code==200 and 'Public sale product' in html
    assert 'submission_token' not in html and '9876.54' not in html and '9,876.54' not in html
    assert 'name="quantity"' not in html and 'action="/sales/' not in html
    assert client.post(path(member,shops[0]),data=payload(product,'invalid')).status_code==403


def test_create_only_does_not_expose_history_or_owner_endpoints(app):
    _,shops,user,member,product,client=setup(app,permission=['sales_create'])
    row=Sale(business_id=shops[0].id,note='PRIVATE HISTORY NOTE');db.session.add(row);db.session.commit()
    url=path(member,shops[0]);response=client.get(url);html=response.get_data(as_text=True)
    assert 'PRIVATE HISTORY NOTE' not in html and 'Recorded sales' not in html
    assert client.post(url,data=payload(product,token(client,url))).status_code==303
    for endpoint in ('/sales/','/products/','/expenses/','/dashboard','/sales/1/delete'):
        response=client.post(endpoint) if endpoint.endswith('delete') else client.get(endpoint)
        assert response.status_code==403


@pytest.mark.parametrize('change',['revoked','unverified','suspended','role','assignment','business',
                                  'expired','downgrade','owner_suspended','archived_role'])
def test_live_revocation_and_plan_or_scope_changes_reject_old_form(app,change):
    owner,shops,user,member,product,client=setup(app);url=path(member,shops[0]);reference=token(client,url)
    from app.models import RecurringSubscription
    if change=='revoked':member.state='revoked'
    if change=='unverified':user.email_verified_at=None
    if change=='suspended':user.suspended_at=NOW
    if change=='role':db.session.get(StaffRole,(owner.id,member.role)).permissions=['sales_view']
    if change=='assignment':StaffMembershipBusiness.query.filter_by(membership_id=member.id).delete()
    if change=='business':shops[0].suspended_at=NOW
    if change=='expired':member.access_until=NOW-timedelta(seconds=1)
    if change=='downgrade':RecurringSubscription.query.filter_by(user_id=owner.id).one().plan_code='plus'
    if change=='owner_suspended':owner.suspended_at=NOW
    if change=='archived_role':db.session.get(StaffRole,(owner.id,member.role)).archived=True
    db.session.commit()
    assert client.post(url,data=payload(product,reference)).status_code in (302,403)
    assert counts()==(0,0,0,0)


def test_unassigned_business_foreign_product_and_other_membership_are_denied(app):
    owner,shops,user,member,product,client=setup(app)
    url=path(member,shops[0]);reference=token(client,url)
    foreign=Product(business_id=shops[1].id,name='FOREIGN SECRET',stock_quantity=20,selling_price=90)
    db.session.add(foreign);db.session.commit()
    assert client.get(path(member,shops[1])).status_code==403
    assert client.post(url,data=payload(foreign,reference)).status_code==400
    _,raw=issue(owner,shops,email='other-staff@example.invalid');other,other_member=accept_new(raw)
    assert client.get(path(other_member,shops[0])).status_code==403
    assert 'FOREIGN SECRET' not in client.get(url).get_data(as_text=True)
    assert counts()==(0,0,0,0)


@pytest.mark.parametrize('change',['negative','zero','fraction','huge','stock','price','date','bad_token','inactive'])
def test_invalid_inputs_do_not_leave_receipts_stock_or_sales(app,change):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    if change in ('negative','zero','fraction','huge','stock'):
        data['quantity']={'negative':'-1','zero':'0','fraction':'1.5','huge':'2147483648','stock':'21'}[change]
    if change=='price':data['unit_price']='0.01'
    if change=='date':data['sold_at']='2020-01-01'
    if change=='bad_token':data['submission_token']='invalid'
    if change=='inactive':product.active=False;db.session.commit()
    assert client.post(url,data=data).status_code==400
    db.session.expire_all();assert product.stock_quantity==20 and counts()==(0,0,0,0)


def test_late_notification_failure_rolls_back_all_ledger_changes(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    with patch('app.notifications.service.notify',side_effect=RuntimeError('synthetic failure')):
        with pytest.raises(RuntimeError):client.post(url,data=data)
    db.session.expire_all();assert product.stock_quantity==20 and counts()==(0,0,0,0)
    assert not AuditLog.query.filter_by(action='SALE_CREATED').count()


def test_staff_receipts_are_bound_to_actor_membership_business_and_not_owner_tokens(app):
    owner,shops,user,member,product,client=setup(app);url=path(member,shops[0]);reference=token(client,url)
    from app.transaction_submissions import issue_token
    with app.test_request_context():owner_token=issue_token(owner.id,shops[0].id,'sale')
    assert client.post(url,data=payload(product,owner_token)).status_code==400
    _,raw=issue(owner,shops,email='other-staff@example.invalid');other,other_member=accept_new(raw)
    login(client,other)
    assert client.post(path(other_member,shops[0]),data=payload(product,reference)).status_code==400
    assert counts()==(0,0,0,0)


def test_current_authority_is_reloaded_after_common_owner_lock(app):
    owner,shops,user,member,product,client=setup(app)
    real=sales_access.service.lock_owner
    def revoke_after_lock(identifier):
        real(identifier)
        member.state='revoked';db.session.flush()
    with patch('app.staff.sales_access.service.lock_owner',side_effect=revoke_after_lock) as lock:
        assert client.post(path(member,shops[0]),data=payload(product,'irrelevant')).status_code==403
    assert lock.call_args.args==(owner.id,) and counts()==(0,0,0,0)


def test_staff_sales_post_rejects_missing_csrf_before_mutation(app):
    _,shops,_,member,product,client=setup(app);url=path(member,shops[0]);data=payload(product,token(client,url))
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post(url,data=data).status_code==400
    assert counts()==(0,0,0,0)


def test_multi_item_failure_after_first_stock_update_rolls_back_every_item(app):
    _,shops,_,member,first,client=setup(app)
    second=Product(business_id=shops[0].id,name='Second product',stock_quantity=9,
                   selling_price=5,buying_price=2)
    db.session.add(second);db.session.commit()
    url=path(member,shops[0]);data=payload(first,token(client,url))
    data.update(product_id=[str(first.id),str(second.id)],quantity=['2','3'])
    with patch('app.notifications.service.stock_transition',side_effect=[None,RuntimeError('second item')]):
        with pytest.raises(RuntimeError):client.post(url,data=data)
    db.session.expire_all()
    assert first.stock_quantity==20 and second.stock_quantity==9 and counts()==(0,0,0,0)


def test_active_search_and_navigation_do_not_grant_unassigned_businesses(app):
    _,shops,_,member,product,client=setup(app)
    response=client.get('/staff/')
    assert response.status_code==200 and path(member,shops[0]) in response.get_data(as_text=True)
    assert path(member,shops[1]) not in response.get_data(as_text=True)
    response=client.get(path(member,shops[0]),query_string={'q':'no matching item'})
    assert 'Public sale product' not in response.get_data(as_text=True)
    app.config['STAFF_SALES_ENABLED']=''
    assert path(member,shops[0]) not in client.get('/staff/').get_data(as_text=True)


def test_real_https_sale_form_preserves_csrf_and_same_origin_requirement(app):
    _,shops,_,member,product,client=setup(app)
    app.config.update(WTF_CSRF_ENABLED=True,WTF_CSRF_SSL_STRICT=True)
    url=path(member,shops[0]);origin='https://localhost'
    page=client.get(url,base_url=origin);assert page.status_code==200
    assert page.headers['Referrer-Policy']=='same-origin'
    html=page.get_data(as_text=True)
    reference=re.search(r'name="submission_token" value="([^"]+)"',html)[1]
    csrf=re.search(r'name="csrf_token" value="([^"]+)"',html)[1]
    data=payload(product,reference);data['csrf_token']=csrf
    assert client.post(url,base_url=origin,data=data,headers={'Referer':'https://foreign.example/'}).status_code==400
    assert counts()==(0,0,0,0)
    assert client.post(url,base_url=origin,data=data,headers={'Referer':origin+url}).status_code==303
    assert counts()==(1,1,1,1)


def test_expired_signed_staff_submission_is_rejected_without_writes(app):
    _,shops,_,member,product,client=setup(app)
    import time
    from app.transaction_submissions import TOKEN_MAX_AGE
    with patch('itsdangerous.timed.time.time',return_value=time.time()-TOKEN_MAX_AGE-10):
        reference=sales_submissions.issue(member,shops[0])
    assert client.post(path(member,shops[0]),data=payload(product,reference)).status_code==400
    assert counts()==(0,0,0,0)


@pytest.mark.parametrize('operation',['sale','restock'])
def test_owner_sale_and_restock_take_owner_lock_before_claim_and_ledger(app,operation):
    owner,shops,_,_,product,client=setup(app)
    login(client,owner)
    from app.transaction_submissions import issue_token
    reference=issue_token(owner.id,shops[0].id,operation)
    events=[]
    real_lock=sales_access.service.lock_owner
    def lock(identifier):
        assert identifier==owner.id
        events.append('owner-lock')
        return real_lock(identifier)
    module='app.sales.routes' if operation=='sale' else 'app.restocking.routes'
    from importlib import import_module
    real_claim=import_module(module).claim
    def claim(*args,**kwargs):
        assert events==['owner-lock']
        events.append('claim')
        return real_claim(*args,**kwargs)
    data=payload(product,reference)
    endpoint='/sales/'
    if operation=='restock':
        endpoint='/restocking/receive';data.pop('payment_method');data['unit_cost']='8.25'
    with patch('app.staff.service.lock_owner',side_effect=lock),patch(module+'.claim',side_effect=claim):
        assert client.post(endpoint,data=data).status_code==302
    assert events==['owner-lock','claim']
    db.session.expire_all()
    assert product.stock_quantity==(18 if operation=='sale' else 22)


@pytest.mark.parametrize('term',['%','_','!','\\'])
def test_staff_product_search_treats_wildcard_and_escape_characters_literally(app,term):
    _,shops,_,member,_,client=setup(app)
    product=Product(business_id=shops[0].id,name='Literal '+term+' marker',
                    stock_quantity=3,selling_price=7,buying_price=2)
    db.session.add(product);db.session.commit()
    response=client.get(path(member,shops[0]),query_string={'q':term})
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'Literal '+term+' marker' in html and 'Public sale product' not in html
