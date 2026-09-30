import json
from datetime import datetime
from urllib.error import HTTPError, URLError
import pytest
from app import create_app, db
from app.models import User, Business, Product, StockMovement, Sale, Restock
from app.products import recognition as service

CODE = '5449000000996'

@pytest.fixture
def client():
    app = create_app({'TESTING':True, 'SECRET_KEY':'recognition-tests',
        'SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:', 'BARCODE_EXTERNAL_LOOKUP_ENABLED':True})
    with app.app_context():
        db.create_all()
        for name in ('one','two'):
            user = User(full_name=name, email=f'{name}@example.com', email_verified_at=datetime.utcnow())
            user.set_password('password123');db.session.add(user);db.session.flush()
            db.session.add(Business(user_id=user.id,name=name,subscription_plan='lifetime',subscription_status='active'))
        db.session.commit()
        c = app.test_client();c.post('/auth/login',data={'email':'one@example.com','password':'password123'})
        yield c
        db.session.remove();db.drop_all()

@pytest.fixture
def provider(monkeypatch):
    calls = []
    state = {'payload':{'product':{'code':CODE, 'product_name':'Coca-Cola', 'brands':'Coca-Cola',
        'quantity':'330 ml', 'categories_tags':['en:sodas'], 'price':900, 'stock':999}},
        'host':'world.openfoodfacts.org'}
    class Response:
        status = 200
        @property
        def url(self):return 'https://' + state['host'] + '/api/v3/product/' + CODE
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):
            raw = state.get('raw',json.dumps(state['payload']).encode())
            return raw[:size]
    class Opener:
        def open(self,request,timeout):
            calls.append(request)
            assert timeout == 3
            if state.get('error'):raise state['error']
            return Response()
    monkeypatch.setattr(service,'build_opener',lambda *args:Opener())
    return calls,state

def lookup(client,code=CODE):return client.get('/products/recognize',query_string={'barcode':code})

@pytest.mark.parametrize('active',[True,False])
def test_existing_and_archived_never_call_external(client,provider,active):
    product=Product(business_id=Business.query.filter_by(name='one').one().id,name='My Coke',barcode=CODE,active=active)
    db.session.add(product);db.session.commit()
    result=lookup(client).json
    assert result['status']=='existing' and result['products'][0]['name']=='My Coke'
    assert result['products'][0]['active']==active
    assert provider[0]==[]
    response=client.get('/products/new',query_string={'barcode':CODE})
    assert response.status_code==302 and response.location.endswith(f'/products/{product.id}')
    assert provider[0]==[]

def test_recognition_prefill_review_privacy_and_explicit_save(client,provider):
    response=lookup(client)
    reference=response.json['reference']
    assert reference['product_name']=='Coca-Cola' and reference['category']=='Drinks'
    assert reference['brand']=='Coca-Cola' and reference['variant']=='330 ml'
    assert not {'price','cost','stock','buying_price','selling_price','stock_quantity','minimum_stock_level'} & reference.keys()
    assert response.headers['Cache-Control']=='no-store, private'
    request=provider[0][0]
    assert CODE in request.full_url and 'product_type=all' in request.full_url
    assert all(value not in request.full_url+str(request.headers) for value in ['one@example.com','password123','user_id','business_id'])
    html=client.get('/products/new',query_string={'barcode':CODE}).data
    assert b'value="Coca-Cola"' in html and b'value="Drinks"' in html
    assert b'Brand: Coca-Cola; Pack size: 330 ml' in html
    for name in ('buying_price','selling_price','stock_quantity'):
        assert f'name="{name}"'.encode() in html
    assert b'value="900"' not in html and b'value="999"' not in html
    assert b'Open Database License' in html and b'We found this product' in html
    assert len(provider[0])==1 # Add page reuses reference cache.
    assert Product.query.count()==StockMovement.query.count()==0
    response=client.post('/products/new',data={'name':'My corrected Coke','barcode':CODE,
        'stock_quantity':'20','buying_price':'250','selling_price':'350','minimum_stock_level':'5',
        'unit':'Can','after_save':'scan'})
    assert response.status_code==302 and response.location.endswith('/products/scan')
    product=Product.query.one()
    assert product.name=='My corrected Coke' and product.opening_quantity==20 and product.stock_quantity==20
    assert str(product.buying_price)=='250.00' and StockMovement.query.one().quantity_change==20
    client.post('/products/new',data={'name':'Duplicate','barcode':CODE})
    assert Product.query.count()==1

def test_other_business_is_not_a_reference_source(client,provider):
    db.session.add(Product(business_id=Business.query.filter_by(name='two').one().id,
        name='Private expensive item',barcode=CODE,buying_price=987,stock_quantity=42));db.session.commit()
    result=lookup(client).json
    assert result['status']=='recognized' and not result['products'] and len(provider[0])==1
    assert 'Private' not in json.dumps(result) and '987' not in json.dumps(result)
    client.post('/products/new',data={'name':'My Coke','barcode':CODE})
    assert Product.query.count()==2

@pytest.mark.parametrize('failure',['unknown','timeout','rate','malformed','large','mismatch','nonobject','missing-name','network'])
def test_graceful_failure_no_records(client,provider,failure):
    calls,state=provider
    if failure=='unknown':state['payload']={'status':'failure'}
    if failure=='timeout':state['error']=TimeoutError('private secret exception')
    if failure=='rate':state['error']=HTTPError('private',429,'private secret',{},None)
    if failure=='malformed':state['raw']=b'{broken'
    if failure=='large':state['raw']=b'x'*(service.MAX_RESPONSE+1)
    if failure=='mismatch':state['payload']['product']['code']='3017620422003'
    if failure=='nonobject':state['payload']=['unexpected']
    if failure=='missing-name':state['payload']['product']['product_name']=''
    if failure=='network':state['error']=URLError('private secret exception')
    result=lookup(client).json
    assert result['status'] in {'unknown','unavailable','rate_limited'} and 'reference' not in result
    html=client.get('/products/new',query_string={'barcode':CODE}).data
    assert b'add it manually' in html and CODE.encode() in html and b'private secret' not in html
    assert Product.query.count()==StockMovement.query.count()==0

def test_no_barcode_and_custom_barcode_still_supported(client,provider):
    for code in ('','LOCAL-123','001234','0000000000001'):
        if code:assert lookup(client,code).json['status']=='unsupported'
        response=client.post('/products/new',data={'name':code or 'Handmade','barcode':code})
        assert response.status_code==302
    assert len(provider[0])==0 and Product.query.count()==4

def test_cache_expiry_and_rate_budget(client,provider,monkeypatch):
    clock=[1000.0];monkeypatch.setattr(service,'monotonic',lambda:clock[0])
    lookup(client);lookup(client);assert len(provider[0])==1
    clock[0]+=3601;lookup(client);assert len(provider[0])==2
    state=service.current_app.extensions['barcode_recognition']
    state.cache.clear()
    for _ in range(5):lookup(client);state.cache.clear()
    assert lookup(client).json['status']=='rate_limited'
    clock[0]+=61
    assert lookup(client).json['status']=='recognized'

def test_disabled_and_unauthenticated_no_external(client,provider):
    service.current_app.config['BARCODE_EXTERNAL_LOOKUP_ENABLED']=False
    assert lookup(client).json['status']=='disabled' and not provider[0]
    client.post('/auth/logout')
    assert lookup(client).status_code==302 and not provider[0]

@pytest.mark.parametrize('host,source',[('world.openbeautyfacts.org','Open Beauty Facts'),('world.openproductsfacts.org','Open Products Facts')])
def test_universal_source_and_no_messy_categories(client,provider,host,source):
    provider[1]['host']=host;provider[1]['payload']['product']['categories_tags']=['xx:very-specific-provider-tag']
    result=lookup(client).json['reference']
    assert result['provider']==source and result['category']=='' and result['source_url'].startswith('https://'+host)

def test_no_silent_sales_or_restock_inventory_creation(client,provider):
    assert client.get('/products/lookup',query_string={'barcode':CODE}).json['products']==[]
    assert not provider[0]
    for url in ('/sales/','/restocking/'):
        assert b'data-create="/products/new"' in client.get(url).data
    assert Product.query.count()==Sale.query.count()==Restock.query.count()==0

def test_redirect_allowlist_and_invalid_requests(client,provider):
    handler=service.SafeRedirect()
    from urllib.request import Request
    for target in ('http://world.openfoodfacts.org/x','https://evil.example/x','https://world.openfoodfacts.org:8443/x','https://user@world.openfoodfacts.org/x'):
        with pytest.raises(ValueError):handler.redirect_request(Request('https://world.openfoodfacts.org'),None,302,'',{},target)
    for code in ('x'*81,'bad\nbarcode'):
        assert lookup(client,code).status_code==400
    assert not provider[0]
