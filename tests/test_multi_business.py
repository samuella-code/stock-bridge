"""Multi-business acceptance uses disposable databases; no Paystack or SMTP."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from threading import Barrier
import pytest
from app import create_app, db
from app.models import User, Business, AccountBilling, RecurringSubscription, Payment, Product, Sale, SaleItem, Expense, Restock, StockMovement, BillingEvent
from app.businesses.service import creation_token, create_business, selection_required
from app.subscriptions.entitlements import effective_access, selected_business
from app.main.routes import figures


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr('app.email_service._send_email', lambda *a, **k: False)
    application = create_app({'TESTING': True, 'SECRET_KEY': 'isolated-multi-business',
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:', 'SUBSCRIPTIONS_ENABLED': True,
        'BILLING_PROVIDER_ENABLED': False, 'SERVER_NAME': 'localhost'})
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def seed(kind='plus', businesses=1, email='multi@example.invalid'):
    now = datetime.utcnow()
    user = User(full_name='Owner', email=email, password_hash='unused', email_verified_at=now)
    db.session.add(user); db.session.flush()
    shops = [Business(user_id=user.id, name=f'Shop {i}') for i in range(businesses)]
    db.session.add_all(shops); db.session.flush()
    account = AccountBilling(user_id=user.id, primary_business_id=shops[0].id if shops else None)
    db.session.add(account)
    if kind == 'trial':
        account.trial_started_at=now; account.trial_ends_at=now+timedelta(days=7)
    elif kind in ('lifetime', 'lifetime_plus'):
        payment=Payment(business_id=shops[0].id if shops else None, customer_email=email,
            reference=email, amount_kobo=300000, status='success')
        db.session.add(payment); db.session.flush()
        account.legacy_payment_id=payment.id; account.legacy_granted_at=now
        account.legacy_business_id=shops[0].id if shops else None
    if kind in ('basic', 'plus', 'lifetime_plus'):
        sub = RecurringSubscription(user_id=user.id, business_id=shops[0].id if shops else 999,
            plan_code='basic' if kind=='basic' else 'plus', billing_interval='monthly', amount_kobo=300000 if kind=='basic' else 500000,
            provider_plan_code='PLN_fixture', status='active', current_period_start=now-timedelta(hours=1), current_period_end=now+timedelta(days=30))
        db.session.add(sub)
    db.session.commit()
    return user, shops


def login(client, user):
    with client.session_transaction() as session:
        session['_user_id']=str(user.id); session['_fresh']=True
    from flask import g
    g.pop('_login_user', None)


def add_business(client, user, name):
    return client.post('/businesses/new', data={'name': name, 'creation_token': creation_token(user)})


@pytest.mark.parametrize('kind,limit', [('basic',1),('trial',1),('lifetime',1),('plus',2),('lifetime_plus',2)])
def test_entitlement_limits_and_crafted_posts(app, kind, limit):
    user, shops=seed(kind); client=app.test_client(); login(client,user)
    if limit==2:
        assert add_business(client,user,'Second').status_code==302
    result=add_business(client,user,'Forbidden')
    assert result.status_code==302 and Business.query.filter_by(user_id=user.id).count()==limit
    assert b'Forbidden' not in client.get('/businesses/').data


@pytest.mark.parametrize('kind', ['basic','trial','plus'])
def test_first_business_creation(app,kind):
    user, _=seed(kind,businesses=0); client=app.test_client(); login(client,user)
    assert add_business(client,user,'First').status_code==302
    assert Business.query.filter_by(user_id=user.id).count()==1
    assert db.session.get(AccountBilling,user.id).primary_business_id==Business.query.one().id
    assert client.get('/dashboard').status_code==200


def test_duplicate_submission_and_token_ownership(app):
    user,_=seed('plus'); client=app.test_client(); login(client,user)
    token=creation_token(user)
    for _ in range(2):
        assert client.post('/businesses/new',data={'name':'Second','creation_token':token}).status_code==302
    assert Business.query.filter_by(user_id=user.id).count()==2
    assert BillingEvent.query.filter_by(kind='business_created').count()==1
    other,_=seed('plus',email='other@example.invalid')
    client.post('/businesses/new',data={'name':'Forged','creation_token':creation_token(other)})
    assert Business.query.filter_by(user_id=user.id).count()==2


def test_switch_persistence_invalid_session_and_foreign_ids(app):
    user,shops=seed('plus',2); other,foreign=seed('plus',email='private@example.invalid')
    client=app.test_client(); login(client,user)
    assert client.post(f'/businesses/{shops[1].id}/switch').status_code==302
    with client.session_transaction() as session: assert session['active_business_id']==shops[1].id
    assert shops[1].name.encode() in client.get('/dashboard').data
    assert client.post(f'/businesses/{foreign[0].id}/switch').status_code==404
    assert client.post(f'/businesses/{foreign[0].id}/choose').status_code==404
    assert client.get(f'/businesses/{foreign[0].id}/edit').status_code==404
    with client.session_transaction() as session: session['active_business_id']=foreign[0].id
    assert client.get('/dashboard').status_code==200
    with client.session_transaction() as session: assert session['active_business_id']==shops[0].id
    assert client.post(f'/businesses/{shops[0].id}/edit', data={'name':'Renamed','user_id':other.id}).status_code==302
    assert db.session.get(Business,shops[0].id).user_id==user.id
    assert db.session.get(Business,shops[1].id).name=='Shop 1'


def add_product(client,name,qty,cost,price):
    client.post('/products/new',data={'name':name,'stock_quantity':qty,'buying_price':cost,'selling_price':price,'unit':'unit','minimum_stock_level':'5'},follow_redirects=True)
    return Product.query.filter_by(name=name).one()


def test_two_business_end_to_end_and_all_route_isolation(app):
    user,shops=seed('plus'); shops[0].name='Tosin Mini Mart'; db.session.commit()
    client=app.test_client(); login(client,user)
    coke=add_product(client,'Coke',100,300,500)
    client.post('/sales/',data={'product_id':coke.id,'quantity':'10','unit_price':'500'})
    client.post('/expenses/',data={'description':'Mini Mart transport','category':'Transportation','amount':'2000'})
    add_business(client,user,'Tosin Fashion'); fashion=Business.query.filter_by(name='Tosin Fashion').one()
    shirt=add_product(client,'T-Shirt',30,4000,7000)
    client.post('/sales/',data={'product_id':shirt.id,'quantity':'2','unit_price':'7000'})
    client.post('/expenses/',data={'description':'Fashion packaging','category':'Packaging','amount':'3000'})
    db.session.expire_all()
    assert db.session.get(Product,coke.id).stock_quantity==90
    assert db.session.get(Product,shirt.id).stock_quantity==28
    assert Sale.query.count()==2 and SaleItem.query.count()==2
    client.get('/dashboard')  # consume success messages, as the normal redirect flow does
    for period in ('today','week','month','custom&from=2026-01-01&to=2026-12-31'):
        response=client.get('/reports?period='+period)
        assert response.status_code==200 and b'Coke' not in response.data and b'T-Shirt' in response.data
    for path in ('/products/','/sales/','/expenses/','/restocking/','/dashboard','/reports'):
        response=client.get(path)
        assert response.status_code==200 and b'Coke' not in response.data and b'Mini Mart transport' not in response.data
    assert client.get('/products/lookup?q=Coke').json['products']==[]
    assert client.get(f'/products/{coke.id}').status_code==404
    assert client.post('/sales/',data={'product_id':coke.id,'quantity':'1','unit_price':'500'}).status_code==404
    assert client.post('/restocking/receive',data={'product_id':coke.id,'quantity':'1','unit_cost':'300'}).status_code==404
    assert client.post(f'/products/{coke.id}/adjust',data={'quantity':'1','direction':'increase','reason':'Other'}).status_code==404
    sale=Sale.query.filter_by(business_id=shops[0].id).one()
    expense=Expense.query.filter_by(business_id=shops[0].id).one()
    assert client.post(f'/sales/{sale.id}/delete',data={'reason':'attack'}).status_code==404
    assert client.post(f'/expenses/{expense.id}/delete').status_code==404
    assert b'No matching products' in client.get('/products/?q=Coke&business_id='+str(shops[0].id)).data
    client.post('/restocking/receive',data={'product_id':shirt.id,'quantity':'2','unit_cost':'4000'})
    assert db.session.get(Product,coke.id).stock_quantity==90
    assert Restock.query.one().business_id==fashion.id
    assert StockMovement.query.filter_by(product_id=coke.id).count()==2
    client.post(f'/businesses/{shops[0].id}/switch')
    for path in ('/products/','/sales/','/expenses/','/restocking/','/dashboard','/reports'):
        response=client.get(path)
        assert response.status_code==200 and b'T-Shirt' not in response.data and b'Fashion packaging' not in response.data
    start=datetime.utcnow()-timedelta(days=1); end=datetime.utcnow()+timedelta(days=1)
    a=figures(shops[0],start,end); b=figures(fashion,start,end)
    assert (a['revenue'],a['cogs'],a['expenses'],a['net_profit'])==(5000,3000,2000,0)
    assert (b['revenue'],b['cogs'],b['expenses'],b['net_profit'])==(14000,8000,3000,3000)
    assert db.session.get(Product,coke.id).stock_quantity==90


@pytest.mark.parametrize('fallback', ['lifetime','basic'])
def test_downgrade_explicit_choice_locked_data_and_reupgrade(app,fallback):
    user,shops=seed('lifetime_plus' if fallback=='lifetime' else 'plus',2)
    client=app.test_client(); login(client,user)
    product=Product(business_id=shops[1].id,name='Preserved',stock_quantity=7,buying_price=2,selling_price=3)
    db.session.add(product)
    sub=RecurringSubscription.query.filter_by(user_id=user.id).one()
    sub.current_period_end=datetime.utcnow()-timedelta(seconds=1)
    if fallback=='basic':
        db.session.add(RecurringSubscription(user_id=user.id,business_id=shops[0].id,plan_code='basic',billing_interval='monthly',amount_kobo=300000,provider_plan_code='PLN_basic',status='active',current_period_start=datetime.utcnow()-timedelta(seconds=1),current_period_end=datetime.utcnow()+timedelta(days=30)))
    db.session.commit()
    assert selection_required(user)
    for path in ('/dashboard','/reports','/products/','/sales/','/expenses/','/restocking/','/profile/'):
        assert client.get(path).location.endswith('/businesses/')
    assert b'Choose the business' in client.get('/businesses/').data
    client.post(f'/businesses/{shops[1].id}/choose')
    assert not selection_required(user)
    assert client.get('/products/').status_code==200
    assert effective_access(user,business=shops[1]).can_write
    assert not effective_access(user,business=shops[0]).can_write
    client.post(f'/businesses/{shops[0].id}/switch')
    with client.session_transaction() as session: assert session['active_business_id']==shops[1].id
    assert client.get(f'/businesses/{shops[0].id}/edit').status_code==302
    assert Business.query.count()==2 and Product.query.one().stock_quantity==7
    db.session.add(RecurringSubscription(user_id=user.id,business_id=shops[1].id,plan_code='plus',billing_interval='monthly',amount_kobo=500000,provider_plan_code='PLN_plus',status='active',current_period_start=datetime.utcnow(),current_period_end=datetime.utcnow()+timedelta(days=30)))
    db.session.commit()
    assert not selection_required(user)
    assert client.post(f'/businesses/{shops[0].id}/switch').status_code==302
    assert client.get('/dashboard').status_code==200
    assert effective_access(user,business=shops[0]).can_write and effective_access(user,business=shops[1]).can_write
    assert Product.query.one().stock_quantity==7
    if fallback=='lifetime': assert db.session.get(AccountBilling,user.id).legacy_payment_id is not None
    latest=RecurringSubscription.query.filter_by(user_id=user.id,plan_code='plus').order_by(RecurringSubscription.id.desc()).first()
    latest.current_period_end=datetime.utcnow()-timedelta(seconds=1); db.session.commit()
    assert selection_required(user)  # a new Plus generation requires a fresh choice


def test_single_business_regression_and_flags_off(app):
    user,shops=seed('trial'); client=app.test_client(); login(client,user)
    assert not selection_required(user)
    for path in ('/dashboard','/reports','/products/','/sales/','/expenses/','/restocking/','/profile/'):
        assert client.get(path).status_code==200
    app.config['SUBSCRIPTIONS_ENABLED']=False
    shops[0].subscription_status='active'; db.session.commit()
    assert client.get('/businesses/new').status_code==404
    assert b'business-switcher' not in client.get('/dashboard').data


def test_concurrent_creation_cannot_exceed_limit(tmp_path):
    application=create_app({'TESTING':True,'SECRET_KEY':'concurrency',
        'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(tmp_path/'isolated.db'),
        'SUBSCRIPTIONS_ENABLED':True,'BILLING_PROVIDER_ENABLED':False})
    with application.app_context():
        db.create_all(); user,_=seed('plus'); uid=user.id
        tokens=[creation_token(user),creation_token(user)]
    barrier=Barrier(2)
    def worker(token):
        with application.app_context():
            user=db.session.get(User,uid); barrier.wait()
            try:
                create_business(user,'Concurrent',token); db.session.commit(); return True
            except ValueError:
                db.session.rollback(); return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,tokens))
    assert sorted(results)==[False,True]
    with application.app_context():
        assert Business.query.filter_by(user_id=uid).count()==2
        assert BillingEvent.query.filter_by(kind='business_created').count()==1
        db.session.remove(); db.drop_all()


@pytest.mark.parametrize('name', ['', ' '*3, 'x'*141])
def test_business_name_validation(app,name):
    user,_=seed(); client=app.test_client(); login(client,user)
    add_business(client,user,name)
    assert Business.query.count()==1


def test_csrf_protection_and_invalid_form_token(app):
    user,_=seed(); client=app.test_client(); login(client,user)
    assert client.post('/businesses/new',data={'name':'No token'}).status_code==302
    assert Business.query.count()==1
    app.config['WTF_CSRF_ENABLED']=True
    assert client.post('/businesses/new',data={'name':'Forged','creation_token':creation_token(user)}).status_code==400
    assert client.post('/businesses/1/switch').status_code==400
    assert client.post('/businesses/1/choose').status_code==400
    assert Business.query.count()==1


def test_read_only_expiry_does_not_unlock_second_business(app):
    user,shops=seed('plus',2); client=app.test_client(); login(client,user)
    sub=RecurringSubscription.query.one(); sub.current_period_end=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
    client.post(f'/businesses/{shops[1].id}/switch')
    assert client.get('/dashboard').status_code==200
    with client.session_transaction() as session: assert session['active_business_id']==shops[0].id
    assert not effective_access(user,business=shops[0]).can_write
    client.post('/products/new',data={'name':'Blocked','stock_quantity':1,'buying_price':1,'selling_price':2})
    assert Product.query.count()==0 and Business.query.count()==2


def test_foreign_primary_and_stale_active_fall_back_for_single_business(app):
    user,shops=seed('trial'); other,foreign=seed('trial',email='other@example.invalid')
    account=db.session.get(AccountBilling,user.id);account.primary_business_id=foreign[0].id;db.session.commit()
    client=app.test_client();login(client,user)
    with client.session_transaction() as session:session['active_business_id']=9999
    assert client.get('/dashboard').status_code==200
    assert effective_access(user,business=shops[0]).can_write
    with client.session_transaction() as session:assert session['active_business_id']==shops[0].id


def test_import_review_cannot_be_committed_into_other_business(app):
    user,shops=seed('plus',2);client=app.test_client();login(client,user)
    from app.products.routes import import_signer
    token=import_signer().dumps({'business':shops[0].id,'user':user.id,'nonce':'fixture', 'rows':[]})
    client.post(f'/businesses/{shops[1].id}/switch')
    assert client.post('/products/import',data={'confirm':'yes','preview':token}).status_code==403
    assert Product.query.count()==0


def test_failed_creation_rolls_back_business_and_audit(app,monkeypatch):
    user,_=seed('plus');client=app.test_client();login(client,user)
    def fail_commit(): raise RuntimeError('disposable commit failure')
    monkeypatch.setattr(db.session,'commit',fail_commit)
    with pytest.raises(RuntimeError):add_business(client,user,'Must roll back')
    assert Business.query.count()==1 and BillingEvent.query.count()==0
