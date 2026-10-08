"""Subscription acceptance tests: disposable database, mocked provider, no live charges."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import pytest
from sqlalchemy import text, inspect
from app import create_app, db
from app.models import User, Business, Payment, AccountBilling, RecurringSubscription, BillingEvent, Product
from app.subscriptions import billing, provider
from app.subscriptions.entitlements import effective_access, start_trial, plan_spec


def iso(dt):return dt.isoformat()+'Z'


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv('VERCEL_ENV',raising=False)
    monkeypatch.setattr('app.email_service._send_email',lambda *args,**kwargs:False)
    app=create_app({'TESTING':True,'SECRET_KEY':'billing-test','SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:',
        'SERVER_NAME':'localhost','SUBSCRIPTIONS_ENABLED':True,'BILLING_PROVIDER_ENABLED':True,'PAYSTACK_PUBLIC_KEY':'pk_test_fixture',
        'PAYSTACK_SECRET_KEY':'sk_test_fixture',**{f'PAYSTACK_{p.upper()}_{i.upper()}_PLAN':f'PLN_{p}_{i}' for p in ('basic','plus') for i in ('monthly','yearly')}})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove();db.drop_all()


@pytest.fixture
def client(app):return app.test_client()


def seed(email='owner@example.com',eligible=True,verified=True):
    u=User(full_name='Owner',email=email,email_verified_at=datetime.utcnow() if verified else None)
    u.set_password('password123');db.session.add(u);db.session.flush()
    b=Business(user_id=u.id,name='Shop',subscription_status='inactive');db.session.add(b);db.session.flush()
    db.session.add(AccountBilling(user_id=u.id,trial_eligible=eligible,primary_business_id=b.id));db.session.commit()
    return u,b


def login(client,u):
    with client.session_transaction() as session:session['_user_id']=str(u.id);session['_fresh']=True
    from flask import g
    g.pop('_login_user',None)


def legacy(u,b):
    p=Payment(customer_email=u.email,business_id=b.id,reference=f'old-{u.id}',amount_kobo=300000,status='success',paid_at=datetime.utcnow())
    db.session.add(p);db.session.flush()
    a=db.session.get(AccountBilling,u.id);a.legacy_payment_id=p.id;a.legacy_business_id=b.id;a.legacy_granted_at=datetime.utcnow()
    b.subscription_plan='lifetime';b.subscription_status='active';db.session.commit()
    return p


class FakeProvider:
    def __init__(self):self.calls=[];self.transactions={};self.overrides={};self.failure=None
    def plan(self,p,i):return {'plan_code':f'PLN_{p}_{i}','domain':'test','amount':plan_spec(p,i)['amount'],'currency':'NGN','interval':plan_spec(p,i)['interval']}
    def details(self,code):
        sub=RecurringSubscription.query.filter_by(provider_subscription_code=code).first()
        if not sub:sub=RecurringSubscription.query.filter(RecurringSubscription.provider_subscription_code.is_(None)).order_by(RecurringSubscription.id.desc()).first()
        u=db.session.get(User,sub.user_id)
        data={'domain':'test','subscription_code':code,'plan':self.plan(sub.plan_code,sub.billing_interval),
            'customer':{'email':u.email,'customer_code':f'CUS_{u.id}'},'email_token':'fixture-token',
            'authorization':{'reusable':True,'authorization_code':'AUTH_fixture'},'status':'active',
            'next_payment_date':iso(datetime.utcnow()+timedelta(days=31 if sub.billing_interval=='monthly' else 365))}
        data.update(self.overrides.get(code,{}));return data
    def verified(self,ref):
        if ref in self.transactions:return deepcopy(self.transactions[ref])
        p=Payment.query.filter_by(reference=ref).one();s=db.session.get(RecurringSubscription,p.subscription_id);u=db.session.get(User,p.user_id)
        return {'reference':ref,'domain':'test','status':'success','amount':s.amount_kobo,'currency':'NGN',
            'paid_at':iso(datetime.utcnow()),'customer':{'email':u.email,'customer_code':f'CUS_{u.id}'},
            'subscription':{'subscription_code':s.provider_subscription_code or 'SUB_new'},
            'metadata':{'product':'stockbridge_subscription','user_id':u.id,'subscription_id':s.id,'plan_code':s.plan_code,'billing_interval':s.billing_interval}}
    def call(self,path,method='GET',payload=None):
        self.calls.append((path,method,deepcopy(payload)))
        if self.failure and self.failure in path:raise provider.PaystackError('fixture failure')
        if path.startswith('/plan/'):
            _,p,i=path.split('/')[-1].split('_');return self.plan(p,i)
        if path=='/transaction/initialize':return {'reference':payload['reference'],'authorization_url':'https://checkout.paystack.com/'+payload['reference']}
        if path.startswith('/transaction/verify/'):return self.verified(path.split('/')[-1])
        if path=='/subscription/disable':return {}
        if path=='/subscription':return {'subscription_code':'SUB_replacement'}
        if path.startswith('/subscription/'):return self.details(path.split('/')[-1])
        raise AssertionError(path)


@pytest.fixture
def fake(monkeypatch):
    f=FakeProvider();monkeypatch.setattr(provider,'call',f.call);return f


def purchase(u,p='basic',i='monthly'):
    billing.begin_checkout(u,p,i);s=RecurringSubscription.query.order_by(RecurringSubscription.id.desc()).first()
    billing.verify_checkout(u,s.checkout_reference);return s


def webhook(client,event):
    raw=json.dumps(event).encode();sig=hmac.new(b'sk_test_fixture',raw,hashlib.sha512).hexdigest()
    return client.post('/payments/webhook',data=raw,headers={'x-paystack-signature':sig},content_type='application/json')


def test_trial_only_starts_after_verification_and_never_restarts(client,monkeypatch):
    monkeypatch.setattr('app.auth.routes.send_verification_email',lambda u:True)
    client.post('/auth/signup',data={'full_name':'Owner','business_name':'Shop','email':'new@example.com','password':'password123','trial_ends_at':'2099-01-01'})
    u=User.query.one();a=db.session.get(AccountBilling,u.id)
    assert a.trial_eligible and a.trial_started_at is None and not start_trial(u)
    from app.email_service import verification_token
    client.get('/auth/verify/'+verification_token(u.email))
    db.session.refresh(a);start=a.trial_started_at
    assert a.trial_ends_at-start==timedelta(days=7)
    assert effective_access(u).kind=='trial'
    client.post('/auth/logout');client.post('/auth/login',data={'email':u.email,'password':'password123'})
    client.get('/dashboard');assert not start_trial(u)
    db.session.refresh(a);assert a.trial_started_at==start and BillingEvent.query.filter_by(kind='trial_started').count()==1


def test_business_changes_do_not_reset_account_trial(app):
    u,b=seed();start_trial(u);db.session.commit();a=db.session.get(AccountBilling,u.id);start=a.trial_started_at
    # Replacing a business never grants another account trial. Clear the selected FK deliberately in this fixture.
    a.primary_business_id=None;db.session.delete(b);db.session.commit()
    b=Business(user_id=u.id,name='Replacement');db.session.add(b);db.session.commit()
    assert not start_trial(u) and a.trial_started_at==start


@pytest.mark.parametrize('path',['/products/new','/products/quick-add','/products/import','/sales/','/sales/1/delete','/expenses/','/expenses/1/delete','/restocking/receive','/products/1/adjust','/products/1/delete','/products/1/edit'])
def test_expired_trial_blocks_business_posts(client,path):
    u,b=seed();a=db.session.get(AccountBilling,u.id);a.trial_started_at=datetime.utcnow()-timedelta(days=8);a.trial_ends_at=datetime.utcnow()-timedelta(days=1);db.session.commit();login(client,u)
    response=client.post(path,data={'trial_ends_at':'2099-01-01'})
    assert response.status_code==302 and response.location.endswith('/plans/')
    assert Business.query.count()==1 and Product.query.count()==0


@pytest.mark.parametrize('path',['/dashboard','/reports','/products/','/sales/','/expenses/','/restocking/','/plans/','/profile/'])
def test_expired_account_keeps_read_only_pages(client,path):
    u,b=seed(eligible=False);login(client,u);r=client.get(path)
    assert r.status_code==200


def test_trial_product_and_isolation(client):
    u,b=seed();start_trial(u);db.session.commit();login(client,u)
    assert client.post('/products/new',data={'name':'Coke','stock_quantity':20,'buying_price':250,'selling_price':350}).status_code==302
    p=Product.query.one();assert p.stock_quantity==20
    other,_=seed('other@example.com');start_trial(other);db.session.commit();login(client,other)
    assert client.post(f'/products/{p.id}/edit',data={'name':'Stolen'}).status_code==404
    assert Product.query.one().name=='Coke'


@pytest.mark.parametrize('p,i,amount,limit',[('basic','monthly',300000,1),('basic','yearly',3000000,1),('plus','monthly',500000,2),('plus','yearly',5000000,2)])
def test_four_plans_only_grant_access_after_verified_payment(app,fake,p,i,amount,limit):
    u,b=seed(eligible=False);billing.begin_checkout(u,p,i)
    assert not effective_access(u).can_write
    s=RecurringSubscription.query.one();assert s.amount_kobo==amount
    billing.verify_checkout(u,s.checkout_reference)
    assert effective_access(u,business=b).can_write and effective_access(u).business_limit==limit
    assert Payment.query.one().paid_at and s.next_renewal_at==s.current_period_end
    billing.verify_checkout(u,s.checkout_reference)
    assert Payment.query.count()==1 and BillingEvent.query.filter_by(kind='subscription_paid').count()==1


@pytest.mark.parametrize('field,value',[('amount',1),('amount','300000'),('currency','USD'),('domain','live'),('status','failed'),('reference','forged'),('customer',{'email':'wrong@example.com','customer_code':'CUS_1'}),('metadata',{'product':'stockbridge_subscription','user_id':999})])
def test_invalid_payment_never_activates(app,fake,field,value):
    u,b=seed(eligible=False);billing.begin_checkout(u,'basic','monthly');s=RecurringSubscription.query.one()
    tx=fake.verified(s.checkout_reference);tx[field]=value;fake.transactions[s.checkout_reference]=tx
    with pytest.raises(ValueError):billing.verify_checkout(u,s.checkout_reference)
    db.session.rollback();assert not effective_access(u).can_write and Payment.query.one().status!='success'


@pytest.mark.parametrize('i',['monthly','yearly'])
def test_lifetime_plus_cancel_expire_failure_falls_back_without_deleting(app,fake,i):
    u,b=seed();old=legacy(u,b);s=purchase(u,'plus',i)
    db.session.add(Business(user_id=u.id,name='Second business'));db.session.commit()
    assert effective_access(u).kind=='legacy_lifetime_plus'
    billing.cancel(u,s);assert effective_access(u).kind=='legacy_lifetime_plus'
    assert any(path=='/subscription/disable' for path,_,_ in fake.calls)
    s.status='past_due';s.current_period_start=datetime.utcnow()-timedelta(days=35);s.current_period_end=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
    assert effective_access(u,business=b).kind=='legacy_lifetime' and not b.has_write_access
    from app.businesses.service import choose_primary
    assert choose_primary(u,b.id).id==b.id
    db.session.commit()
    assert b.has_write_access
    other=Business.query.filter(Business.id!=b.id).one();assert not effective_access(u,business=other).can_write
    assert Business.query.count()==2 and db.session.get(AccountBilling,u.id).legacy_payment_id==old.id
    with pytest.raises(ValueError):billing.begin_checkout(u,'basic','monthly')


def test_lifetime_never_auto_subscribes_and_unpaid_never_becomes_lifetime(app,fake):
    u,b=seed(eligible=False);assert effective_access(u).kind=='restricted'
    legacy(u,b);assert effective_access(u).kind=='legacy_lifetime' and not fake.calls


def test_signature_and_user_ownership(client,fake):
    u,b=seed(eligible=False);s=purchase(u);other,_=seed('other@example.com');login(client,other)
    assert client.post(f'/plans/{s.id}/cancel',data={'confirm':'yes'}).status_code==404
    assert client.post(f'/plans/{s.id}/change',data={'confirm':'yes','plan':'plus','interval':'monthly'}).status_code==404
    assert client.get('/plans/callback?reference='+s.checkout_reference).status_code==302
    assert client.post('/payments/webhook',json={'event':'charge.success','data':{'reference':s.checkout_reference}}).status_code==401
    assert not s.cancel_at_period_end


def renewal_event(s,ref,start,end,paid=True):
    return {'event':'invoice.update' if paid else 'invoice.payment_failed','data':{'domain':'test','subscription':{'subscription_code':s.provider_subscription_code},'invoice_code':'INV_'+ref,'paid':paid,'status':'success' if paid else 'failed','period_start':iso(start),'period_end':iso(end),'transaction':{'reference':ref}}}


def test_renewal_duplicates_out_of_order_and_failure(client,fake):
    u,b=seed(eligible=False);s=purchase(u);start=s.current_period_end;end=start+timedelta(days=28)
    tx=fake.verified(s.checkout_reference);tx.update(reference='renewal',paid_at=iso(start),metadata={});fake.transactions['renewal']=tx
    event=renewal_event(s,'renewal',start,end)
    assert webhook(client,event).status_code==200 and webhook(client,event).status_code==200
    assert Payment.query.count()==2 and s.current_period_end==end
    failed=renewal_event(s,'old-failed',start-timedelta(days=28),start,False)
    assert webhook(client,failed).status_code==200 and s.status=='active'
    failed=renewal_event(s,'next-failed',end,end+timedelta(days=28),False)
    assert webhook(client,failed).status_code==200 and s.status=='past_due'
    assert effective_access(u,now=end+timedelta(seconds=1)).kind=='restricted'
    assert s.current_period_end==end and BillingEvent.query.filter_by(kind='renewal_failed').count()==1


def test_charge_before_subscription_create_reconciles_safely(client,fake):
    u,b=seed(eligible=False);billing.begin_checkout(u,'basic','monthly');s=RecurringSubscription.query.one()
    assert webhook(client,{'event':'charge.success','data':{'domain':'test','reference':s.checkout_reference}}).status_code==200
    assert not effective_access(u).can_write
    event={'event':'subscription.create','data':{'domain':'test','subscription_code':'SUB_new'}}
    assert webhook(client,event).status_code==200 and webhook(client,event).status_code==200
    assert effective_access(u).can_write and BillingEvent.query.filter_by(kind='subscription_paid').count()==1


def test_subscription_embedded_plan_without_domain_reconciles_idempotently(client,fake):
    u,b=seed(eligible=False)
    billing.begin_checkout(u,'basic','monthly')
    plan=fake.plan('basic','monthly');plan.pop('domain')
    fake.overrides['SUB_new']={'plan':plan}
    event={'event':'subscription.create','data':{'domain':'test','subscription_code':'SUB_new'}}
    assert webhook(client,event).status_code==200
    assert webhook(client,event).status_code==200
    assert effective_access(u).kind=='basic'
    assert Payment.query.filter_by(status='success').count()==1
    assert BillingEvent.query.filter_by(kind='subscription_paid').count()==1
    assert all(method=='GET' for path,method,_ in fake.calls if path.startswith('/plan/'))


@pytest.mark.parametrize('field,value',[('domain','live'),('plan_code','PLN_other'),('amount',500000),('currency','USD'),('interval','annually')])
def test_missing_embedded_domain_rejects_authoritative_plan_mismatch(app,fake,monkeypatch,field,value):
    u,b=seed(eligible=False)
    billing.begin_checkout(u,'basic','monthly')
    plan=fake.plan('basic','monthly');plan.pop('domain')
    fake.overrides['SUB_new']={'plan':plan}
    original=fake.call
    def mismatched(path,method='GET',payload=None):
        result=original(path,method,payload)
        if path=='/plan/PLN_basic_monthly':result={**result,field:value}
        return result
    monkeypatch.setattr(provider,'call',mismatched)
    with pytest.raises(ValueError):provider.fetch_subscription('SUB_new')
    assert Payment.query.filter_by(status='success').count()==0


@pytest.mark.parametrize('old,old_interval,new,new_interval',[('basic','monthly','plus','monthly'),('plus','monthly','basic','monthly'),('basic','monthly','basic','yearly'),('plus','yearly','plus','monthly')])
def test_safe_replacement_changes_and_no_duplicate_creation(app,fake,old,old_interval,new,new_interval):
    u,b=seed(eligible=False);s=purchase(u,old,old_interval);end=s.current_period_end
    link=billing.change_plan(u,s,new,new_interval)
    r=RecurringSubscription.query.filter_by(replacement_of_id=s.id).one()
    assert s.cancel_at_period_end and s.current_period_end==end and Payment.query.filter_by(subscription_id=s.id).count()==1
    if link:assert r.status=='checkout_pending' and r.plan_code==new
    else:
        assert r.status=='scheduled' and r.starts_at==end and r.current_period_end is None
        payload=[data for path,_,data in fake.calls if path=='/subscription'][0]
        assert payload['start_date']==end.isoformat()+'+00:00'
        assert effective_access(u).subscription.id==s.id
    before=len(fake.calls)
    with pytest.raises(ValueError):billing.change_plan(u,s,new,new_interval)
    assert not any(path in ('/subscription','/transaction/initialize') for path,_,_ in fake.calls[before:])


def test_scheduled_replacement_activates_only_on_paid_invoice(client,fake):
    u,b=seed(eligible=False);s=purchase(u,'plus');billing.change_plan(u,s,'basic','monthly')
    r=RecurringSubscription.query.filter_by(replacement_of_id=s.id).one();start=r.starts_at;end=start+timedelta(days=28)
    fake.transactions['replacement-charge']={'reference':'replacement-charge','domain':'test','status':'success','amount':300000,'currency':'NGN','paid_at':iso(start),'customer':{'email':u.email,'customer_code':f'CUS_{u.id}'},'metadata':{}}
    assert not effective_access(u,now=start+timedelta(seconds=1)).can_write
    assert webhook(client,renewal_event(r,'replacement-charge',start,end)).status_code==200
    assert effective_access(u,now=start+timedelta(seconds=1)).kind=='basic'


def test_unknown_external_outcome_is_durable_and_not_retried(app,fake):
    u,b=seed(eligible=False);fake.failure='/transaction/initialize'
    with pytest.raises(provider.PaystackError):billing.begin_checkout(u,'basic','monthly')
    db.session.rollback();assert RecurringSubscription.query.one().status=='initializing'
    fake.failure=None
    with pytest.raises(ValueError):billing.begin_checkout(u,'basic','monthly')
    assert sum(path=='/transaction/initialize' for path,_,_ in fake.calls)==1


def test_cancel_failure_keeps_existing_access_and_state(app,fake):
    u,b=seed(eligible=False);s=purchase(u);fake.failure='/subscription/disable'
    with pytest.raises(provider.PaystackError):billing.cancel(u,s)
    db.session.rollback();assert not s.cancel_at_period_end and effective_access(u).can_write


def test_readonly_preflight_and_migration_preserve_verified_lifetime(tmp_path):
    from flask_migrate import upgrade
    app=create_app({'TESTING':True,'SECRET_KEY':'migration','SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'old.db'}"})
    with app.app_context():
        upgrade(revision='0012_product_catalogue')
        db.session.execute(text("INSERT INTO user (id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES (1,'Owner','OWNER@example.com','preserved',CURRENT_TIMESTAMP,'user',0,0),(2,'Admin','admin@example.com','admin-preserved',CURRENT_TIMESTAMP,'admin',1,7)"))
        db.session.execute(text("INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at) VALUES (1,1,'Original',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        db.session.execute(text("INSERT INTO payment (id,business_id,customer_email,reference,provider,product,amount_kobo,currency,status,paid_at,created_at) VALUES (1,1,'owner@example.com','old','paystack','lifetime',300000,'NGN','success',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        db.session.commit()
        report=app.test_cli_runner().invoke(args=['billing-preflight']);assert report.exit_code==0 and 'READ ONLY: no records changed.' in report.output
        upgrade();a=db.session.get(AccountBilling,1)
        assert a.legacy_payment_id==1 and not a.trial_eligible and Payment.query.one().reference=='old'
        assert db.session.get(User,2).password_hash=='admin-preserved' and db.session.get(User,2).admin_auth_version==7
        assert db.session.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0016_inventory_email_outbox'
        assert {'account_billing','recurring_subscription','billing_event','social_identity'} <= set(inspect(db.engine).get_table_names())
        assert {'notification','notification_preference'} <= set(inspect(db.engine).get_table_names())
        assert db.session.execute(text('SELECT logo_key FROM business WHERE id=1')).scalar() is None


def test_migration_stops_before_ddl_for_unproven_active_lifetime(tmp_path):
    from flask_migrate import upgrade
    app=create_app({'TESTING':True,'SECRET_KEY':'migration','SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'unsafe.db'}"})
    with app.app_context():
        upgrade(revision='0012_product_catalogue')
        db.session.execute(text("INSERT INTO user (id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES (1,'Owner','owner@example.com','preserved',CURRENT_TIMESTAMP,'user',0,0)"))
        db.session.execute(text("INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at) VALUES (1,1,'Original',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"));db.session.commit()
        with pytest.raises(SystemExit):upgrade()
        assert 'account_billing' not in inspect(db.engine).get_table_names()
        assert db.session.execute(text('SELECT password_hash FROM user')).scalar()=='preserved'
        result=app.test_cli_runner().invoke(args=['billing-preflight']);assert result.exit_code!=0 and 'STOP' in result.output


def test_notifications_branded_once_and_not_at_startup(app,monkeypatch):
    from app.subscriptions.notifications import queue_due,deliver
    u,b=seed();start_trial(u);db.session.commit();a=db.session.get(AccountBilling,u.id)
    sent=[];monkeypatch.setattr('app.email_service._send_email',lambda *args,**kwargs:sent.append((args,kwargs)) or True)
    queue_due(now=a.trial_ends_at-timedelta(days=1));assert deliver()==2 and deliver()==0
    assert 'Stock' in sent[0][1]['html'] and '7-day' in sent[0][1]['html']
    queue_due(now=a.trial_ends_at+timedelta(seconds=1));assert deliver()==1
    queue_due(now=a.trial_ends_at+timedelta(seconds=1));assert deliver()==0


def test_provider_gate_preview_and_prices(app,monkeypatch):
    assert provider.configured()
    monkeypatch.setenv('VERCEL_ENV','preview');assert not provider.configured()
    monkeypatch.delenv('VERCEL_ENV');app.config['PAYSTACK_PUBLIC_KEY']='pk_live_fixture';assert not provider.configured()
    with pytest.raises(ValueError):plan_spec('business','monthly')


def test_csrf_protects_billing_forms(client,fake):
    u,b=seed();login(client,u);client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post('/plans/checkout',data={'plan':'basic','interval':'monthly'}).status_code==400
    assert not fake.calls


def test_new_entitlements_preserve_complete_business_workflow(client):
    from app.models import Sale, SaleItem, Restock, Expense, StockMovement
    from app.main.routes import figures
    u,b=seed();start_trial(u);db.session.commit();login(client,u)
    client.post('/products/new',data={'name':'Coke','stock_quantity':20,'buying_price':250,'selling_price':350,'minimum_stock_level':5})
    p=Product.query.one()
    client.post('/sales/',data={'product_id':str(p.id),'quantity':'3','unit_price':'350','payment_method':'Cash'})
    client.post('/restocking/receive',data={'product_id':str(p.id),'quantity':'20','unit_cost':'280'})
    client.post('/sales/',data={'product_id':str(p.id),'quantity':'5','unit_price':'350','payment_method':'Cash'})
    client.post('/expenses/',data={'description':'Delivery','category':'Transportation','amount':'1500'})
    db.session.refresh(p)
    assert p.stock_quantity==32 and Sale.query.count()==2 and SaleItem.query.count()==2
    assert Restock.query.one().unit_cost==280 and Expense.query.one().amount==1500
    assert sum(m.quantity_change for m in StockMovement.query.all())==32
    start=datetime.combine(datetime.utcnow().date(),datetime.min.time())
    totals=figures(b,start,start+timedelta(days=1));assert totals['revenue']==2800 and totals['expenses']==1500
    assert client.get('/dashboard').status_code==200 and client.get('/reports').status_code==200
    assert client.get('/products/quick-add').status_code==200 and client.get('/products/import').status_code==200


def test_resubscribe_after_expiry_restores_write_access(app,fake):
    u,b=seed(eligible=False);s=purchase(u);s.current_period_end=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
    assert not effective_access(u).can_write
    # Provider-created subscription codes are unique across independent checkouts.
    fake.overrides['SUB_new']={'status':'cancelled'}
    original=fake.verified
    def verified(ref):
        data=original(ref)
        if ref!=s.checkout_reference:data['subscription']={'subscription_code':'SUB_second'}
        return data
    fake.verified=verified
    billing.begin_checkout(u,'plus','monthly');r=RecurringSubscription.query.order_by(RecurringSubscription.id.desc()).first()
    billing.verify_checkout(u,r.checkout_reference);assert effective_access(u).kind=='plus' and effective_access(u).can_write


def test_two_concurrent_checkouts_initialize_only_once(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.delenv('VERCEL_ENV',raising=False)
    app=create_app({'TESTING':True,'SERVER_NAME':'localhost','SECRET_KEY':'race','SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'race.db'}",'SUBSCRIPTIONS_ENABLED':True,'BILLING_PROVIDER_ENABLED':True,'PAYSTACK_SECRET_KEY':'sk_test_fixture','PAYSTACK_PUBLIC_KEY':'pk_test_fixture','PAYSTACK_BASIC_MONTHLY_PLAN':'PLN_basic_monthly'})
    fake=FakeProvider();monkeypatch.setattr(provider,'call',fake.call)
    with app.app_context():db.create_all();u,b=seed(eligible=False);uid=u.id
    def checkout():
        with app.app_context():
            try:return billing.begin_checkout(db.session.get(User,uid),'basic','monthly')
            except ValueError:return 'pending'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:checkout(),range(2)))
    with app.app_context():
        assert Payment.query.count()==1 and RecurringSubscription.query.count()==1
        assert sum(path=='/transaction/initialize' for path,_,_ in fake.calls)==1
        assert any(result.startswith('https://checkout.paystack.com/') for result in results)


def test_provider_dates_not_guessed_and_incomplete_invoice_stops(client,fake):
    u,b=seed(eligible=False);billing.begin_checkout(u,'basic','monthly');s=RecurringSubscription.query.one()
    fake.overrides['SUB_new']={'next_payment_date':None}
    with pytest.raises(ValueError):billing.verify_checkout(u,s.checkout_reference)
    db.session.rollback();assert not effective_access(u).can_write
    fake.overrides.clear();billing.verify_checkout(u,s.checkout_reference)
    prior=s.current_period_end
    event=renewal_event(s,'bad',prior,prior+timedelta(days=28));event['data'].pop('transaction')
    assert webhook(client,event).status_code==503
    assert s.current_period_end==prior and Payment.query.count()==1


def test_legacy_admin_business_access_and_security_identity_survive(app):
    u,b=seed();legacy(u,b);u.admin_enabled=True;u.admin_auth_version=7;db.session.commit()
    assert b.has_write_access and effective_access(u).legacy
    assert not start_trial(u) and u.admin_auth_version==7


def test_readonly_route_inventory_covers_all_actual_mutations(client):
    u,b=seed(eligible=False);login(client,u)
    # Enumerate Flask's registered business mutation routes, including import confirmation and settings.
    rules=[r for r in client.application.url_map.iter_rules() if r.endpoint.split('.')[0] in {'products','sales','restocking','expenses'} and 'POST' in r.methods]
    assert len(rules)>=10
    for rule in rules:
        path=str(rule)
        import re
        path=re.sub(r'<int:[^>]+>','999',path)
        response=client.post(path,data={'confirm':'yes'})
        assert response.status_code==302 and response.location.endswith('/plans/'),rule.endpoint


def test_scheduled_subscription_blocks_unrelated_new_checkout(app,fake):
    u,b=seed(eligible=False);s=purchase(u,'plus');billing.change_plan(u,s,'basic','monthly')
    with pytest.raises(ValueError):billing.begin_checkout(u,'plus','yearly')
    assert RecurringSubscription.query.count()==2


def test_cancel_scheduled_change_prevents_future_charge(app,fake):
    u,b=seed(eligible=False);s=purchase(u,'plus');billing.change_plan(u,s,'basic','yearly')
    r=RecurringSubscription.query.filter_by(replacement_of_id=s.id).one()
    billing.cancel(u,r)
    assert r.cancel_at_period_end and r.status=='cancelled' and r.current_period_end is None
    assert effective_access(u).kind=='plus' and effective_access(u,now=s.current_period_end+timedelta(seconds=1)).kind=='restricted'


def test_response_without_data_accepted_only_for_disable(app,monkeypatch):
    from app.payments.service import _request,PaystackError
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"status":true,"message":"Subscription disabled successfully"}'
    monkeypatch.setattr('app.payments.service.urlopen',lambda *a,**kw:Response())
    assert _request('/subscription/disable','sk_test_fixture','POST',{},allow_empty=True)=={}
    with pytest.raises(PaystackError):_request('/transaction/verify/fixture','sk_test_fixture')


def test_price_is_server_owned_and_lifetime_checkout_disabled(client,fake):
    u,b=seed(eligible=False);login(client,u)
    response=client.post('/plans/checkout',data={'plan':'plus','interval':'yearly','amount':'1','user_id':'999'})
    assert response.status_code==303 and Payment.query.one().amount_kobo==5000000 and Payment.query.one().user_id==u.id
    assert client.post('/payments/initialize').location.endswith('/plans/')
    assert client.get('/payments/checkout').location.endswith('/plans/')


@pytest.mark.parametrize('plan,interval,amount,limit',[
    ('basic','monthly',300000,1),('basic','yearly',3000000,1),
    ('plus','monthly',500000,2),('plus','yearly',5000000,2),
])
def test_acceptance_checkout_rejects_browser_owned_billing_fields(client,fake,plan,interval,amount,limit):
    user,business=seed(eligible=False); login(client,user)
    response=client.post('/plans/checkout',data={
        'plan':plan,'interval':interval,'amount':'1','amount_kobo':'1',
        'currency':'USD','provider_plan_code':'PLN_attacker','paystack_plan':'PLN_attacker',
        'business_limit':'999','billing_interval':'weekly','user_id':'999',
    })
    assert response.status_code==303
    payment=Payment.query.one(); sub=RecurringSubscription.query.one()
    assert payment.user_id==user.id and payment.amount_kobo==amount
    assert sub.amount_kobo==amount and sub.billing_interval==interval
    assert sub.provider_plan_code==f'PLN_{plan}_{interval}'
    payload=next(payload for path,method,payload in fake.calls if path=='/transaction/initialize')
    assert payload['amount']==amount and payload['currency']=='NGN'
    assert payload['plan']==f'PLN_{plan}_{interval}'
    billing.verify_checkout(user,payment.reference)
    assert effective_access(user).business_limit==limit


@pytest.mark.parametrize('interval',['weekly','lifetime','2099'])
def test_acceptance_checkout_invalid_interval_never_contacts_provider(client,fake,interval):
    user,_=seed(eligible=False); login(client,user)
    response=client.post('/plans/checkout',data={'plan':'plus','interval':interval,'amount':'1','business_limit':'999'})
    assert response.status_code==302 and response.location.endswith('/plans/')
    assert fake.calls==[] and Payment.query.count()==0 and RecurringSubscription.query.count()==0


def test_acceptance_duplicate_callback_does_not_extend_paid_period(client,fake):
    user,_=seed(eligible=False); login(client,user)
    billing.begin_checkout(user,'plus','monthly')
    sub=RecurringSubscription.query.one()
    callback='/plans/callback?reference='+sub.checkout_reference
    assert client.get(callback).status_code==302
    period=(sub.current_period_start,sub.current_period_end)
    assert client.get(callback).status_code==302
    db.session.refresh(sub)
    assert (sub.current_period_start,sub.current_period_end)==period
    assert Payment.query.count()==1
    assert BillingEvent.query.filter_by(kind='subscription_paid').count()==1


def test_card_link_is_owned_and_host_checked(client,fake,monkeypatch):
    u,b=seed(eligible=False);s=purchase(u);login(client,u)
    original=fake.call
    def call(path,method='GET',payload=None):
        if path.endswith('/manage/link'):return {'link':'https://paystack.com/manage/subscriptions/fixture?subscription_token=fixture'}
        return original(path,method,payload)
    monkeypatch.setattr(provider,'call',call)
    assert client.post(f'/plans/{s.id}/card').status_code==303
    other,_=seed('other@example.com');login(client,other)
    assert client.post(f'/plans/{s.id}/card').status_code==404
    login(client,u)
    monkeypatch.setattr(provider,'call',lambda path,*args: {'link':'https://attacker.example/'} if path.endswith('/manage/link') else original(path,*args))
    assert client.post(f'/plans/{s.id}/card').status_code==302


def test_replayed_payment_never_extends_another_invoice_period(client,fake):
    u,b=seed(eligible=False);s=purchase(u);prior=s.current_period_end
    event=renewal_event(s,s.checkout_reference,prior,prior+timedelta(days=31))
    assert webhook(client,event).status_code==200 and s.current_period_end==prior
    assert Payment.query.count()==1


def test_pending_subscription_alone_and_mode_mismatch_never_grant(client,fake):
    u,b=seed(eligible=False);billing.begin_checkout(u,'basic','monthly');s=RecurringSubscription.query.one()
    tx=fake.verified(s.checkout_reference);tx['status']='pending';fake.transactions[s.checkout_reference]=tx
    assert webhook(client,{'event':'subscription.create','data':{'domain':'test','subscription_code':'SUB_new'}}).status_code==200
    assert not effective_access(u).can_write
    assert webhook(client,{'event':'subscription.create','data':{'domain':'live','subscription_code':'SUB_new'}}).status_code==503
    assert not effective_access(u).can_write


def test_primary_business_remains_owned_during_one_business_fallback(client):
    u,b=seed();legacy(u,b)
    second=Business(user_id=u.id,name='Chosen primary');db.session.add(second);db.session.flush()
    from app.businesses.service import choose_primary
    choose_primary(u,second.id);db.session.commit();login(client,u)
    client.post('/products/new',data={'name':'Right business','stock_quantity':1,'buying_price':10,'selling_price':20})
    assert Product.query.one().business_id==second.id
    assert not effective_access(u,business=b).can_write


def test_old_lifetime_webhook_remains_honored_with_recurring_provider_disabled(client):
    u,b=seed(eligible=False)
    p=Payment(customer_email=u.email,reference='SB_pending_old',amount_kobo=300000)
    db.session.add(p);db.session.commit();client.application.config['BILLING_PROVIDER_ENABLED']=False
    event={'event':'charge.success','data':{'reference':p.reference,'domain':'test','status':'success','amount':300000,'currency':'NGN','metadata':{'product':'stockbridge_lifetime','customer_email':u.email}}}
    assert webhook(client,event).status_code==200
    assert effective_access(u).kind=='legacy_lifetime' and db.session.get(AccountBilling,u.id).legacy_payment_id==p.id
