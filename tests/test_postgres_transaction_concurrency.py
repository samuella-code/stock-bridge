"""PREPARED ONLY. Default execution skips before app import or DB connection.

No migration/reset/cleanup is provided. Requires a separately approved migrated,
empty test database, manifest and explicit mutation acknowledgment.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import pytest
from tests.support.isolated_postgres import validated_test_url
from tests.support.postgres_preflight import preflight
from tests.support.postgres_concurrency import concurrent_posts

SCENARIOS=('sale_single','sale_multi','restock_single','restock_multi','adjust_increase','adjust_decrease','expense')

@pytest.fixture(scope='module')
def pg_app():
    if os.environ.get('STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK')!='approved-phase5d5b-synthetic-only':
        pytest.skip('PostgreSQL mutations not authorized; preparation only')
    env=dict(os.environ)
    url=validated_test_url(env)
    manifest_path=env.get('STOCKBRIDGE_TEST_POSTGRES_MANIFEST')
    if not manifest_path:pytest.fail('Approved migrated-instance manifest required',pytrace=False)
    manifest=json.loads(Path(manifest_path).read_text())
    assert manifest.get('run_id')==env['STOCKBRIDGE_TEST_POSTGRES_RUN_ID']
    assert manifest.get('database')==url.database and manifest.get('role')==url.username
    assert manifest.get('revision')=='0017_transaction_submission'
    assert manifest.get('approved_synthetic_mutations') is True
    identity=(manifest.get('database_oid'),manifest.get('role_oid'))
    assert all(type(value) is int and value>0 for value in identity)
    relations=manifest.get('relations')
    assert isinstance(relations,list) and relations and all(isinstance(r,str) and r.startswith('public.') for r in relations)
    preflight(env,phase='migrated',expected_tables=relations,expected_identity=identity)
    # No existing SQLite fixture, dotenv URL fallback, migration or reset calls.
    from app import create_app,db
    from unittest.mock import patch
    config=dict(TESTING=True,SECRET_KEY='isolated-concurrency-test-signing-only',
        SQLALCHEMY_DATABASE_URI=url,SQLALCHEMY_ENGINE_OPTIONS=dict(pool_size=4,max_overflow=0,
            pool_timeout=5,connect_args=dict(connect_timeout=5,options='-c statement_timeout=10000 -c lock_timeout=5000')),
        SUBSCRIPTIONS_ENABLED=False,BILLING_PROVIDER_ENABLED=False,
        INVENTORY_EMAILS_ENABLED=True,INVENTORY_EMAIL_BASE_URL='https://staging.example.invalid',
        INVENTORY_EMAIL_TRIGGER_URL='',INVENTORY_EMAIL_TRIGGER_SECRET='',INVENTORY_EMAIL_DISPATCH_SECRET='',
        BUSINESS_IMAGE_STORAGE='disabled',SMTP_HOST='',SMTP_PASSWORD='',PAYSTACK_SECRET_KEY='',PAYSTACK_PUBLIC_KEY='',
        GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='',APPLE_CLIENT_ID='',APPLE_PRIVATE_KEY='')
    def forbidden(*args,**kwargs):raise AssertionError('External integration attempted')
    with patch('requests.sessions.Session.request',side_effect=forbidden),patch('smtplib.SMTP',side_effect=forbidden),\
         patch('smtplib.SMTP_SSL',side_effect=forbidden),patch('boto3.client',side_effect=forbidden):
        app=create_app(config);app.config['WTF_CSRF_ENABLED']=True
        with app.app_context():
            # Fail closed rather than clear pre-existing rows. This is read-only.
            for table in db.metadata.sorted_tables:
                assert db.session.execute(table.select().limit(1)).first() is None, 'Test DB must initially contain no application rows'
            db.session.rollback()
        yield app,env,relations,identity
        with app.app_context():db.session.remove();db.engine.dispose()
        # Deliberately leave synthetic records; cleanup requires separate approval.


def field(response,name):
    match=re.search(('name="'+name+'" value="([^"]+)"').encode(),response.data)
    assert match is not None
    return match[1].decode()


def seeded(pg_app,scenario):
    app,env,relations,identity=pg_app
    preflight(env,phase='migrated',expected_tables=relations,expected_identity=identity) # Revalidate before seeding.
    from app import db
    from app.models import User,Business,Product
    unique=uuid4().hex;email=unique+'@example.invalid';password='synthetic-test-password-only'
    with app.app_context():
        user=User(full_name='Concurrency fixture',email=email,email_verified_at=datetime.utcnow());user.set_password(password)
        db.session.add(user);db.session.flush()
        business=Business(user_id=user.id,name='Synthetic '+unique,subscription_plan='lifetime',subscription_status='active')
        db.session.add(business);db.session.flush();bid=business.id
        products=[]
        for name in ('Water','Bread'):
            product=Product(business_id=bid,name=name,unit='unit',stock_quantity=10,opening_quantity=10,
                buying_price=100,selling_price=150,minimum_stock_level=5)
            db.session.add(product);db.session.flush();products.append(product.id)
        db.session.commit()
    clients=[app.test_client(),app.test_client()]
    for client in clients:
        csrf=field(client.get('/auth/login'),'csrf_token')
        assert client.post('/auth/login',data=dict(email=email,password=password,csrf_token=csrf)).status_code==302
    if scenario.startswith('sale'):form_path=path='/sales/';operation='sale'
    elif scenario.startswith('restock'):form_path='/restocking/';path='/restocking/receive';operation='restock'
    elif scenario.startswith('adjust'):form_path=f'/products/{products[0]}';path=form_path+'/adjust';operation='adjustment'
    else:form_path=path='/expenses/';operation='expense'
    pages=[c.get(form_path) for c in clients];key=field(pages[0],'submission_token')
    ids=products if scenario.endswith('multi') else products[:1]
    if operation=='sale':data=dict(product_id=ids,quantity=['5']*len(ids),unit_price=['150']*len(ids),payment_method='Cash')
    elif operation=='restock':data=dict(product_id=ids,quantity=['5']*len(ids),unit_cost=['100']*len(ids),supplier='Synthetic supplier',note='Fixture')
    elif operation=='adjustment':data=dict(quantity='5',direction='increase' if scenario.endswith('increase') else 'decrease',reason='Stock count correction',note='Fixture')
    else:data=dict(description='Synthetic transport',category='Transportation',amount='50',note='Fixture')
    payloads=[dict(data,submission_token=key,csrf_token=field(page,'csrf_token')) for page in pages]
    return clients,path,payloads,bid,ids,form_path


def snapshot(app,bid,ids):
    from app import db
    from app.models import Sale,SaleItem,Restock,Expense,StockMovement,TransactionSubmission,AuditLog,Notification,EmailOutbox,Product
    with app.app_context():
        result={model.__name__:model.query.filter_by(business_id=bid).count()
                for model in (Sale,Restock,Expense,StockMovement,TransactionSubmission,AuditLog,Notification,EmailOutbox)}
        result['SaleItem']=db.session.query(SaleItem).join(Sale).filter(Sale.business_id==bid).count()
        result['stock']=[db.session.get(Product,pid).stock_quantity for pid in ids]
        result['receipts']=[(r.result_reference,r.completed_at is not None) for r in TransactionSubmission.query.filter_by(business_id=bid)]
        result['sale_totals']=[str(r.total) for r in Sale.query.filter_by(business_id=bid)]
        result['expenses']=[str(r.amount) for r in Expense.query.filter_by(business_id=bid)]
        result['batches']=[r.batch_id for r in Restock.query.filter_by(business_id=bid)]
        result['movements']=[r.quantity_change for r in StockMovement.query.filter_by(business_id=bid).order_by(StockMovement.id)]
        return result


def run_race(pg_app,clients,path,payloads,**kwargs):
    app,env,_,_=pg_app;url=validated_test_url(env)
    import psycopg
    with psycopg.connect(host=url.host,port=url.port,dbname=url.database,user=url.username,password=url.password,
        connect_timeout=5,autocommit=True,options='-c default_transaction_read_only=on -c statement_timeout=3000') as observer:
        with app.app_context():engine=__import__('app').db.engine
        return concurrent_posts(engine,clients,path,payloads,observer,**kwargs)


@pytest.mark.parametrize('scenario',SCENARIOS)
@pytest.mark.parametrize('variant',['same','changed','different'])
def test_postgres_creation_races(pg_app,scenario,variant):
    clients,path,payloads,bid,ids,form=seeded(pg_app,scenario);app=pg_app[0]
    if variant=='changed':
        payloads[1]['quantity']=['4']*len(ids) if scenario.startswith(('sale','restock')) else '4'
        if scenario=='expense':payloads[1]['amount']='60'
    if variant=='different':payloads[1]['submission_token']=field(clients[1].get(form),'submission_token')
    statuses,observed=run_race(pg_app,clients,path,payloads,observe_lock=variant!='different')
    assert statuses==([302,409] if variant=='changed' else [302,302])
    if variant!='different':assert observed
    count=2 if variant=='different' else 1;state=snapshot(app,bid,ids)
    assert state['TransactionSubmission']==count and all(done for _,done in state['receipts'])
    decreasing=scenario.startswith('sale') or scenario=='adjust_decrease'
    assert state['stock']==([10-5*count]*len(ids) if decreasing else [10+5*count]*len(ids) if scenario!='expense' else [10])
    if scenario.startswith('sale'):
        assert state['Sale']==count and state['SaleItem']==count*len(ids)
        assert state['sale_totals']==[f'{750*len(ids)}.00']*count
        assert state['AuditLog']==count and state['Notification']==count+count*len(ids)
        assert state['EmailOutbox']==count*len(ids)
    elif scenario.startswith('restock'):
        assert state['Restock']==count*len(ids) and len(set(state['batches']))==count
        assert state['AuditLog']==count
        assert state['Notification']==count and state['EmailOutbox']==count
    elif scenario.startswith('adjust'):
        assert state['Notification']==(count if decreasing else 0)
        assert state['EmailOutbox']==(count if decreasing else 0)
    else:
        assert state['Expense']==count and state['AuditLog']==count
        assert state['expenses']==['50.00']*count
        assert state['Notification']==state['EmailOutbox']==0
    assert state['StockMovement']==(0 if scenario=='expense' else count*len(ids))
    # Replay after uncertain response uses original receipt, never a new result.
    before=snapshot(app,bid,ids)
    assert clients[0].post(path,data=payloads[0]).status_code==302
    assert snapshot(app,bid,ids)==before


@pytest.mark.parametrize('scenario',SCENARIOS)
def test_postgres_rollback_and_explicit_retry(pg_app,scenario):
    clients,path,payloads,bid,ids,_=seeded(pg_app,scenario);app=pg_app[0]
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from flask import has_request_context,request
    before=snapshot(app,bid,ids)
    def fail(session):
        if has_request_context() and request.path==path:raise RuntimeError('Synthetic precommit failure')
    event.listen(Session,'before_commit',fail)
    try:
        with pytest.raises(RuntimeError):clients[0].post(path,data=payloads[0])
    finally:event.remove(Session,'before_commit',fail)
    assert snapshot(app,bid,ids)==before
    assert clients[0].post(path,data=payloads[0]).status_code==302
    assert snapshot(app,bid,ids)['TransactionSubmission']==1


def test_postgres_concurrent_sale_void(pg_app):
    clients,path,payloads,bid,ids,_=seeded(pg_app,'sale_multi');app=pg_app[0]
    assert clients[0].post(path,data=payloads[0]).status_code==302
    from app import db
    from app.models import Sale
    with app.app_context():sid=Sale.query.filter_by(business_id=bid).one().id
    before=snapshot(app,bid,ids)
    void_payloads=[dict(csrf_token=p['csrf_token'],reason='Return '+str(i)) for i,p in enumerate(payloads)]
    statuses,observed=run_race(pg_app,clients,f'/sales/{sid}/delete',void_payloads,lock_kind='void')
    assert statuses==[302,302] and observed
    state=snapshot(app,bid,ids)
    assert state['stock']==[10,10] and state['StockMovement']==before['StockMovement']+2
    for name in ('TransactionSubmission','Notification','EmailOutbox','AuditLog'):assert state[name]==before[name]
    with app.app_context():
        sale=Sale.query.filter_by(id=sid,business_id=bid).one()
        assert sale.voided_at is not None and sale.void_reason=='Return 0'
    assert clients[1].post(f'/sales/{sid}/delete',data=void_payloads[1]).status_code==302
    assert snapshot(app,bid,ids)==state
