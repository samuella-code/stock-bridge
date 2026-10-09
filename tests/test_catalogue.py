import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
import re
import pytest
from openpyxl import Workbook
from werkzeug.datastructures import MultiDict
from app import create_app, db
from app.models import User, Business, Product, StockMovement, Sale, SaleItem, Restock, AuditLog
from app.products.catalogue import HEADERS, read_spreadsheet
from app.main.routes import figures

@pytest.fixture
def client():
    app=create_app({'TESTING':True,'SECRET_KEY':'catalogue-tests','SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:'})
    with app.app_context():
        db.create_all()
        for n in ('one','two'):
            user=User(full_name=n,email=f'{n}@example.com',email_verified_at=datetime.utcnow())
            user.set_password('password123');db.session.add(user);db.session.flush()
            db.session.add(Business(user_id=user.id,name=n,subscription_plan='lifetime',subscription_status='active'))
        db.session.commit()
        c=app.test_client();c.post('/auth/login',data={'email':'one@example.com','password':'password123'})
        yield c
        db.session.remove();db.drop_all()

def quick(client, rows, **kwargs):
    data=MultiDict()
    for row in rows:
        for field in HEADERS.values():data.add(field,str(row.get(field,'')))
    return client.post('/products/quick-add',data=data,**kwargs)

def upload(client,rows,filename='products.csv',headers=None):
    buffer=io.StringIO();writer=csv.writer(buffer);headers=headers or list(HEADERS)
    writer.writerow(headers)
    for row in rows:writer.writerow([row.get(HEADERS[h],'') for h in headers])
    return client.post('/products/import',data={'spreadsheet':(io.BytesIO(buffer.getvalue().encode()),filename)},content_type='multipart/form-data')

def token(response):
    match=re.search(rb'name="preview" value="([^"]+)"',response.data)
    assert match, response.data[-1000:]
    return match.group(1).decode()

def confirm(client,preview):return client.post('/products/import',data={'confirm':'yes','preview':preview})

def test_save_another_and_opening_zero(client):
    response=client.post('/products/new',data={'name':'Zero','sku':'ZERO','after_save':'another'})
    assert response.location.endswith('/products/new')
    assert db.session.query(Product).one().stock_quantity==0
    assert StockMovement.query.one().quantity_change==0
    response=client.post('/products/new',data={'name':'Full','stock_quantity':'20','after_save':'detail'})
    assert response.location.endswith(f"/products/{Product.query.filter_by(name='Full').one().id}")
    assert Product.query.filter_by(name='Full').one().opening_quantity==20
    assert b'Save &amp; Add Another' in client.get('/products/new').data

def test_three_entry_methods_and_no_scanner_ui(client):
    from html.parser import HTMLParser
    class Choices(HTMLParser):
        links=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='a' and attrs.get('class')=='entry-choice':
                self.links.append(attrs['href'])
    parser=Choices();parser.feed(client.get('/products/').data.decode())
    assert parser.links==['/products/new','/products/quick-add','/products/import']
    client.post('/products/new',data={'name':'Visible','sku':'VISIBLE'})
    pid=Product.query.one().id
    for url in ['/products/','/products/new',f'/products/{pid}',f'/products/{pid}/edit',
                '/products/quick-add','/products/import','/sales/','/restocking/']:
        html=client.get(url).data.lower()
        assert b'barcode' not in html and b'scan' not in html
        assert b'getusermedia' not in html
    template=client.get('/products/import/template').data.decode('utf-8-sig')
    assert 'SKU' in template and 'Barcode' not in template
    assert client.get('/products/scan').status_code==404
    assert client.get('/products/recognize').status_code==404
    assert client.get('/static/js/barcode.js').status_code==404

def test_retained_barcode_is_never_read_or_overwritten(client):
    client.post('/products/new',data={'name':'Legacy','sku':'OLD','barcode':'ignored','stock_quantity':10})
    product=Product.query.one();assert product.barcode is None
    product.barcode='retained-private-identifier';db.session.commit()
    client.post(f'/products/{product.id}/edit',data={'name':'Updated','sku':'OLD','barcode':'overwrite'})
    db.session.refresh(product)
    assert product.barcode=='retained-private-identifier' and product.stock_quantity==10
    result=client.get('/products/lookup?q=OLD').json['products'][0]
    assert result['name']=='Updated' and 'barcode' not in result
    assert client.get('/products/lookup?q=retained-private-identifier').json['products']==[]
    search=client.get('/products/?q=retained-private-identifier').data
    assert b'No matching products' in search and b'<strong>Updated</strong>' not in search
    assert client.get('/products/lookup?barcode=retained-private-identifier').status_code==400

@pytest.mark.parametrize('extension',['csv','xlsx'])
def test_legacy_spreadsheet_column_ignored(client,extension):
    headers=list(HEADERS)+['Barcode']
    rows=[['First','','unit',10,250,350,5,'FIRST','','same'],
          ['Second','','unit',20,250,350,5,'SECOND','',12345]]
    if extension=='csv':
        buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(headers);writer.writerows(rows)
        file=io.BytesIO(buffer.getvalue().encode())
    else:
        workbook=Workbook();sheet=workbook.active;sheet.append(headers)
        for row in rows:sheet.append(row)
        file=io.BytesIO();workbook.save(file);file.seek(0)
    response=client.post('/products/import',data={'spreadsheet':(file,f'legacy.{extension}')})
    assert response.status_code==200 and b'Barcode' not in response.data
    assert confirm(client,token(response)).status_code==302
    assert Product.query.count()==2 and all(p.barcode is None for p in Product.query.all())
    assert [p.stock_quantity for p in Product.query.order_by(Product.id)]==[10,20]

def test_legacy_signed_preview_does_not_write_identifier(client):
    from app.products.routes import import_signer
    preview=import_signer().dumps({'business':1,'user':1,'nonce':'legacy-preview',
        'rows':[{'name':'From prior preview','stock_quantity':7,'barcode':'ignore-me','sku':'PREVIEW'}]})
    assert confirm(client,preview).status_code==302
    product=Product.query.one()
    assert product.barcode is None and product.stock_quantity==7 and product.sku=='PREVIEW'

def test_quick_add_atomic_and_validation(client):
    quick(client,[{'name':'Coke','stock_quantity':100},{'name':'Fanta','stock_quantity':80}])
    assert Product.query.count()==2
    assert [p.stock_quantity for p in Product.query.order_by(Product.id)]==[100,80]
    assert StockMovement.query.count()==2
    response=quick(client,[{'name':'Bread'},{'name':'Bad','stock_quantity':-1}])
    assert b'whole number' in response.data
    assert Product.query.count()==2
    quick(client,[{'name':'A','sku':'abc'},{'name':'B','sku':'abc'}])
    assert Product.query.count()==2
    quick(client,[{'name':'Same'},{'name':'Same'}])
    assert Product.query.count()==2

def test_quick_add_capacity_and_legacy_fields(client):
    data=MultiDict()
    for number in range(100):
        for field in HEADERS.values():
            data.add(field,str({'name':f'Quick {number}','sku':f'Q{number}','stock_quantity':number}.get(field,'')))
        data.add('barcode','ignored-duplicate')
    assert client.post('/products/quick-add',data=data).status_code==302
    assert Product.query.count()==100 and StockMovement.query.count()==100
    assert all(p.barcode is None for p in Product.query.all())
    response=quick(client,[{'name':f'Extra {i}'} for i in range(101)])
    assert b'1 to 100' in response.data and Product.query.count()==100

@pytest.mark.parametrize('field,value', [('sku','sku-1')])
def test_import_duplicates_in_file_and_database(client,field,value):
    client.post('/products/new',data={'name':'Existing',field:value})
    response=upload(client,[{'name':'New',field:value}])
    assert b'Duplicate' in response.data and b'Confirm Import' not in response.data
    response=upload(client,[{'name':'A',field:'other'},{'name':'B',field:'other'}])
    assert b'Duplicate' in response.data and Product.query.count()==1

def test_csv_preview_confirm_and_replay(client):
    response=upload(client,[{'name':'Coke','stock_quantity':20,'sku':'coke','buying_price':250,'selling_price':350}])
    assert Product.query.count()==0
    preview=token(response);assert confirm(client,preview).status_code==302
    assert Product.query.one().sku=='COKE'
    assert StockMovement.query.one().quantity_change==20
    assert confirm(client,preview).status_code==302
    assert Product.query.count()==1
    assert AuditLog.query.filter_by(action='PRODUCT_IMPORT').count()==1

def test_confirmation_revalidates_and_signed_tenant_binding(client):
    preview=token(upload(client,[{'name':'New','sku':'SAME'}]))
    client.post('/products/new',data={'name':'Existing','sku':'SAME'})
    assert confirm(client,preview).status_code==400
    assert Product.query.count()==1
    assert confirm(client,preview+'tampered').status_code==400
    client.post('/auth/logout');client.post('/auth/login',data={'email':'two@example.com','password':'password123'})
    assert confirm(client,preview).status_code==403
    response=upload(client,[{'name':'Other','sku':'SAME'}])
    assert confirm(client,token(response)).status_code==302
    assert Product.query.count()==2

def test_import_preview_expiry(client,monkeypatch):
    preview=token(upload(client,[{'name':'Expire'}]))
    from itsdangerous import TimestampSigner
    original=TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner,'get_timestamp',lambda self:original(self)+1300)
    assert confirm(client,preview).status_code==400
    assert Product.query.count()==0

@pytest.mark.parametrize('row', [{'name':'','stock_quantity':1},{'name':'Bad','stock_quantity':'-1'}, {'name':'Bad','stock_quantity':'1.5'},
    {'name':'Bad','selling_price':'NaN'}, {'name':'Bad','buying_price':'-2'}, {'name':'Bad','selling_price':'2.999'},
    {'name':'Bad','stock_quantity':'2147483648'}, {'name':'Bad','sku':'x'*61}])
def test_invalid_import_row(client,row):
    response=upload(client,[row])
    assert b'need attention' in response.data and b'Confirm Import' not in response.data
    assert Product.query.count()==0

def xlsx(client, rows):
    workbook=Workbook();sheet=workbook.active;sheet.append(list(HEADERS))
    for row in rows:sheet.append([row.get(f,'') for f in HEADERS.values()])
    buffer=io.BytesIO();workbook.save(buffer);buffer.seek(0)
    return client.post('/products/import',data={'spreadsheet':(buffer,'products.xlsx')})

def test_xlsx_import_and_formula_rejection(client):
    response=xlsx(client,[{'name':'Excel','stock_quantity':10,'sku':'001234','selling_price':350}])
    assert confirm(client,token(response)).status_code==302
    assert Product.query.one().stock_quantity==10
    assert xlsx(client,[{'name':'=1+1'}]).status_code==400
    assert xlsx(client,[{'name':'Number Code','sku':12345}]).status_code==400
    assert Product.query.count()==1

@pytest.mark.parametrize('filename,blob', [('products.xls',b'old format'),('products.xlsx',b'not zip'),
    ('products.csv',b'Product Name\n=HYPERLINK("bad")'),('products.csv',b'Unknown\nThing'),
    ('products.csv',b'Product Name,Product Name\nA,A'),('products.csv',b'x'* (2*1024*1024+1))])
def test_bad_uploads(client,filename,blob):
    response=client.post('/products/import',data={'spreadsheet':(io.BytesIO(blob),filename)})
    assert response.status_code in (400,413) and Product.query.count()==0

def test_zip_bomb_size_limit(client):
    import zipfile
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('xl/worksheets/sheet1.xml','x'*(11*1024*1024))
    buffer.seek(0)
    response=client.post('/products/import',data={'spreadsheet':(buffer,'bomb.xlsx')})
    assert response.status_code==400 and b'too large' in response.data

def test_import_row_limit_and_actual_row_numbers(client):
    assert upload(client,[{'name':f'P{i}'} for i in range(1001)]).status_code==400
    response=client.post('/products/import',data={'spreadsheet':(io.BytesIO(b'Product Name,Opening Quantity\nGood,1\n,\nBad,no\n'),'rows.csv')})
    assert b'Row 4' in response.data

def test_search_filters_and_business_isolation(client):
    quick(client,[{'name':'Coca-Cola','category':'Drinks','sku':'COKE','stock_quantity':4,'minimum_stock_level':5},
                  {'name':'Bread','category':'Food','stock_quantity':0}, {'name':'Milk','category':'Drinks','stock_quantity':30}])
    for term in ('Coca','COKE'):
        results=client.get('/products/lookup',query_string={'q':term}).json['products']
        assert len(results)==1 and results[0]['name']=='Coca-Cola'
    assert b'Bread' not in client.get('/products/?category=Drinks').data
    assert b'Coca-Cola' in client.get('/products/?stock=low').data
    assert b'Milk' not in client.get('/products/?stock=low').data
    assert b'Bread' in client.get('/products/?stock=out').data
    assert b'Coca-Cola' not in client.get('/products/?stock=out').data
    assert client.get('/products/lookup?q=%25').json['products']==[]
    pid=Product.query.filter_by(name='Coca-Cola').one().id
    client.post('/auth/logout');client.post('/auth/login',data={'email':'two@example.com','password':'password123'})
    assert client.get('/products/lookup?q=COKE').json['products']==[]
    for suffix in ('','/edit'):
        assert client.get(f'/products/{pid}{suffix}').status_code==404
    assert client.post('/sales/',data={'product_id':pid,'quantity':1,'unit_price':350}).status_code==404
    assert client.post('/restocking/receive',data={'product_id':pid,'quantity':1,'unit_cost':250}).status_code==404
    assert client.post(f'/products/{pid}/adjust',data={'quantity':1,'direction':'increase','reason':'Returned'}).status_code==404

def test_large_catalogue_is_bounded(client):
    db.session.bulk_save_objects([Product(business_id=1,name=f'Product {i:04d}',sku=f'S{i}',stock_quantity=10) for i in range(5000)])
    db.session.commit()
    response=client.get('/products/?sort=name&page=2&q=Product')
    assert b'Product 0020' in response.data and b'Product 0040' not in response.data
    assert b'q=Product' in response.data
    lookup=client.get('/products/lookup?q=Product');assert len(lookup.json['products'])==15
    assert len(client.get('/sales/').data)<25000
    restock=client.get('/restocking/');assert len(restock.data)<60000
    assert b'Next insights' in restock.data
    assert len(client.get('/products/lookup?q=S4999').json['products'])==1

def test_supermarket_end_to_end(client):
    rows=[{'name':n,'stock_quantity':q,'sku':c,'buying_price':cost,'selling_price':price} for n,q,c,cost,price in
          [('Coca-Cola',100,'111',250,350),('Fanta',80,'222',250,350),('Bread',30,'333',900,1100),('Indomie',200,'444',300,400)]]
    quick(client,rows)
    client.post('/products/new',data={'name':'Additional Product','sku':'555','stock_quantity':10})
    ids={p.name:p.id for p in Product.query.all()}
    # IDs are obtained via the same tenant-scoped SKU lookup used by a searchable basket.
    coke=client.get('/products/lookup?q=111').json['products'][0]['id']
    client.post('/sales/',data={'product_id':[coke,ids['Bread'],ids['Indomie']],'quantity':[2,1,3],'unit_price':['','','']})
    assert Sale.query.count()==1 and SaleItem.query.count()==3
    assert Sale.query.one().total==Decimal('3000')
    client.post('/restocking/receive',data={'product_id':[coke,ids['Bread'],ids['Fanta']], 'quantity':[50,20,40], 'unit_cost':[280,950,270]})
    assert Restock.query.count()==3 and len({r.batch_id for r in Restock.query})==1
    client.post(f'/products/{coke}/adjust',data={'quantity':1,'direction':'decrease','reason':'Damaged'})
    expected={'Coca-Cola':147,'Bread':49,'Indomie':197,'Fanta':120}
    for name,qty in expected.items():
        p=db.session.get(Product,ids[name]);assert p.stock_quantity==qty
        assert sum(m.quantity_change for m in StockMovement.query.filter_by(product_id=p.id))==qty
    assert SaleItem.query.filter_by(product_id=coke).one().unit_cost==250
    assert Restock.query.filter_by(product_id=coke).one().unit_cost==280
    client.post('/expenses/',data={'description':'Delivery','amount':1500,'category':'Transportation'})
    start=datetime.combine(datetime.utcnow().date(),datetime.min.time());metrics=figures(db.session.get(Business,1),start,start+timedelta(days=1))
    assert metrics['revenue']==3000 and metrics['cogs']==2300 and metrics['gross_profit']==700 and metrics['net_profit']==-800
    assert b'3,000' in client.get('/dashboard').data and b'3,000' in client.get('/reports').data
    assert b'Damaged' in client.get(f'/products/{coke}').data
    # Editing details must not replace current stock or historic snapshots.
    client.post(f'/products/{coke}/edit',data={'name':'Coca-Cola','buying_price':999,'selling_price':999,'stock_quantity':999})
    assert db.session.get(Product,coke).stock_quantity==147
    assert SaleItem.query.filter_by(product_id=coke).one().unit_price==350

@pytest.mark.parametrize('operation',['sale','restock','quick','import'])
def test_mid_operation_failure_rolls_back(client,monkeypatch,operation):
    quick(client,[{'name':'First','stock_quantity':10},{'name':'Second','stock_quantity':10}])
    ids=[p.id for p in Product.query.order_by(Product.id)]
    preview=token(upload(client,[{'name':'Third'},{'name':'Fourth'}])) if operation=='import' else None
    from sqlalchemy import event
    count=[0]
    def fail(mapper,connection,target):
        count[0]+=1
        if count[0]==2:raise RuntimeError('injected database failure')
    model=SaleItem if operation=='sale' else Restock if operation=='restock' else Product
    event.listen(model,'before_insert',fail)
    try:
        with pytest.raises(RuntimeError):
            if operation=='sale':client.post('/sales/',data={'product_id':ids,'quantity':[1,1],'unit_price':['','']})
            elif operation=='restock':client.post('/restocking/receive',data={'product_id':ids,'quantity':[1,1],'unit_cost':[250,250]})
            elif operation=='quick':quick(client,[{'name':'Third'},{'name':'Fourth'}])
            else:confirm(client,preview)
    finally:event.remove(model,'before_insert',fail)
    db.session.expire_all()
    assert Product.query.count()==2
    assert [p.stock_quantity for p in Product.query.order_by(Product.id)]==[10,10]
    assert Sale.query.count()==0 and SaleItem.query.count()==0 and Restock.query.count()==0
    assert StockMovement.query.count()==2

def test_oversell_and_archived_sku_uniqueness(client):
    quick(client,[{'name':'One','stock_quantity':1,'sku':'123'},{'name':'Two','stock_quantity':10}])
    ids=[p.id for p in Product.query.order_by(Product.id)]
    client.post('/sales/',data={'product_id':ids,'quantity':[2,1],'unit_price':['','']})
    assert Sale.query.count()==0 and Product.query.first().stock_quantity==1
    client.post(f'/products/{ids[0]}/adjust',data={'quantity':1,'direction':'decrease','reason':'Damaged'})
    client.post(f'/products/{ids[0]}/delete')
    assert client.get('/products/lookup?q=123').json['products']==[]
    client.post('/products/new',data={'name':'Duplicate','sku':'123'})
    assert Product.query.count()==2
    assert b'One' in client.get('/products/?stock=archived').data

def test_archived_filter_and_lookup(client):
    client.post('/products/new',data={'name':'ArchivedUnique'})
    pid=Product.query.one().id
    client.post(f'/products/{pid}/delete')
    assert b'ArchivedUnique' in client.get('/products/?stock=archived').data
    assert b'ArchivedUnique' not in client.get('/products/').data
    assert client.get('/products/lookup?q=ArchivedUnique').json['products']==[]
    assert b'Archived' in client.get(f'/products/{pid}').data

@pytest.mark.parametrize('url',['/products/quick-add','/products/import','/sales/','/restocking/'])
def test_new_pages_render(client,url):
    assert client.get(url).status_code==200

@pytest.mark.parametrize('operation,value',[('sale','1.111'),('sale','1e99'),('restock','1.111'),('restock','1e99')])
def test_transaction_price_bounds(client,operation,value):
    client.post('/products/new',data={'name':'P','stock_quantity':10})
    pid=Product.query.one().id
    if operation=='sale':client.post('/sales/',data={'product_id':pid,'quantity':1,'unit_price':value})
    else:client.post('/restocking/receive',data={'product_id':pid,'quantity':1,'unit_cost':value})
    assert Product.query.one().stock_quantity==10
    assert Sale.query.count()==0 and Restock.query.count()==0

def test_import_csrf_protection_and_xss_escaping(client):
    client.application.config['WTF_CSRF_ENABLED']=True
    response=client.post('/products/import',data={'spreadsheet':(io.BytesIO(b'Product Name\nSafe'),'p.csv')})
    assert response.status_code==400 and Product.query.count()==0
    client.application.config['WTF_CSRF_ENABLED']=False
    response=upload(client,[{'name':'<script>alert(1)</script>'}])
    assert b'&lt;script&gt;' in response.data
    assert b'<script>alert(1)</script>' not in response.data

def test_product_catalogue_migration_preserves_existing_data(tmp_path):
    from flask_migrate import upgrade
    from sqlalchemy import text,inspect
    app=create_app({'TESTING':True,'SECRET_KEY':'migration','SQLALCHEMY_DATABASE_URI':f"sqlite:///{tmp_path/'catalogue.db'}"})
    with app.app_context():
        upgrade(revision='0011_payment_receipt_email')
        db.session.execute(text("INSERT INTO user (id,full_name,email,password_hash,created_at,role,admin_enabled,admin_auth_version) VALUES (1,'Admin','admin@example.com','unchanged',CURRENT_TIMESTAMP,'admin',1,7)"))
        db.session.execute(text("INSERT INTO business (id,user_id,name,created_at,subscription_plan,subscription_status,trial_started_at,trial_ends_at) VALUES (1,1,'Original',CURRENT_TIMESTAMP,'lifetime','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        db.session.execute(text("INSERT INTO product (id,business_id,name,sku,buying_price,selling_price,stock_quantity,minimum_stock_level,supplier_lead_time,safety_stock,created_at,updated_at,unit,active,opening_quantity) VALUES (1,1,'Existing','SKU',250,350,20,5,2,0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'bottle',1,20)"))
        db.session.execute(text("INSERT INTO payment (id,business_id,customer_email,reference,provider,product,amount_kobo,currency,status,created_at) VALUES (1,1,'admin@example.com','old','paystack','lifetime',300000,'NGN','success',CURRENT_TIMESTAMP)"))
        db.session.commit();upgrade(revision="0012_product_catalogue")
        assert Product.query.one().stock_quantity==20 and Product.query.one().barcode is None
        assert User.query.one().password_hash=='unchanged' and User.query.one().admin_auth_version==7
        assert db.session.execute(text('SELECT status FROM payment')).scalar()=='success'
        assert 'uq_product_business_barcode' in {c['name'] for c in inspect(db.engine).get_unique_constraints('product')}
        assert {'ix_product_business_active_name','ix_product_business_active_category'} <= {i['name'] for i in inspect(db.engine).get_indexes('product')}

def test_thousand_product_import(client):
    preview=token(upload(client,[{'name':f'Import {i}','sku':f'S{i}','stock_quantity':i} for i in range(1000)]))
    assert confirm(client,preview).status_code==302
    assert Product.query.count()==1000 and StockMovement.query.count()==1000
    assert Product.query.filter_by(sku='S999').one().stock_quantity==999

@pytest.mark.parametrize('environment',[None,'preview','development','production'])
def test_vercel_build_migration_guard(monkeypatch,environment):
    from scripts import vercel_build
    if environment:monkeypatch.setenv('VERCEL_ENV',environment)
    else:monkeypatch.delenv('VERCEL_ENV',raising=False)
    # Isolate the policy from the developer/CI environment.
    for key in ('VERCEL_PROJECT_ID', 'VERCEL_TARGET_ENV', vercel_build.SKIP_SETTING):
        monkeypatch.delenv(key, raising=False)
    calls=[];monkeypatch.setattr(vercel_build.subprocess,'call',lambda args:calls.append(args) or 0)
    assert vercel_build.main()==(1 if environment=='production' else 0)
    assert calls==[]
    if environment=='production':
        # A positively identified non-staging production build still upgrades.
        monkeypatch.setenv('VERCEL_PROJECT_ID', 'prj_TestProduction123')
        assert vercel_build.main()==0
        assert len(calls)==1
        assert calls[0]==[vercel_build.sys.executable, '-m', 'flask', '--app', 'app:create_app', 'db', 'upgrade']
