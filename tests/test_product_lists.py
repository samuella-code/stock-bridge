"""Product-list parser and shared signed import confirmation regression tests."""
import io
from decimal import Decimal
import pytest
from app import db
from app.models import Product, StockMovement, AuditLog
from app.products.catalogue import HEADERS, MAX_UPLOAD
from app.products.product_lists import parse_product_list
from tests.test_catalogue import client, token, confirm, upload, xlsx


def paste(client, text):
    return client.post('/products/import', data={'method':'paste', 'product_list':text})


def edit(client, response, rows):
    data = {'confirm':'yes', 'edited':'yes', 'preview':token(response)}
    for index, row in enumerate(rows):
        for field, value in row.items():
            data[f'rows.{index}.{field}'] = value
    return client.post('/products/import', data=data)


@pytest.mark.parametrize('separator', [' - ', ',', '\t'])
def test_structured_lists(client, separator):
    response = paste(client, separator.join(['Coca Cola 50cl','24','350','500']))
    assert response.status_code == 200 and Product.query.count() == 0
    assert confirm(client, token(response)).status_code == 302
    p=Product.query.one()
    assert (p.opening_quantity,p.stock_quantity,p.buying_price,p.selling_price)==(24,24,Decimal('350'),Decimal('500'))
    assert StockMovement.query.one().quantity_change == 24


def test_names_only_and_empty_lines(client):
    response=paste(client,'Coke\n\nFanta\nSprite\nBread\nIndomie\n')
    assert Product.query.count()==0
    assert confirm(client,token(response)).status_code==302
    assert Product.query.count()==5
    assert all(p.stock_quantity==0 and p.buying_price==0 and p.selling_price==0 and p.unit=='unit' for p in Product.query.all())
    assert StockMovement.query.count()==5


def test_copied_table_headers(client):
    response=paste(client,'Product Name\tQuantity\tCost Price\tSelling Price\tSKU\nMilk\t12\t250\t350\t0012')
    assert confirm(client, token(response)).status_code==302
    assert Product.query.one().sku=='0012' and Product.query.one().stock_quantity==12


@pytest.mark.parametrize('name',['T-shirt XL','Hand-made - Blue - Shirt','Shoes, Blue','奶茶 🧋','<script>alert(1)</script>'])
def test_unusual_names_and_ambiguity(client,name):
    response=paste(client,name)
    assert b'<script>alert(1)</script>' not in response.data
    assert confirm(client,token(response)).status_code==302
    assert Product.query.one().name==name


def test_hyphen_name_structured():
    row=parse_product_list('Hand-made - Blue - Shirt - 24 - 350 - 500')[0]
    assert row['name']=='Hand-made - Blue - Shirt' and row['stock_quantity']=='24'
    row=parse_product_list('Bread - White - Large - Sliced')[0]
    assert row['name']=='Bread - White - Large - Sliced' and '_warning' in row


def test_malformed_line_correctable(client):
    response=paste(client,'Bread - 10 - bad')
    assert b'Ambiguous separators' in response.data
    result=edit(client,response,[{'name':'Bread','stock_quantity':'10','buying_price':'50','selling_price':'70'}])
    assert result.status_code==302 and Product.query.one().stock_quantity==10


def test_txt_uses_same_parser(client):
    response=client.post('/products/import',data={'method':'text','text_file':(io.BytesIO(b'\xef\xbb\xbfMilk - 3 - 25 - 35\nBread'),'notes.TXT')})
    assert response.status_code==200
    assert confirm(client,token(response)).status_code==302
    assert Product.query.count()==2 and StockMovement.query.count()==2


@pytest.mark.parametrize('blob,filename',[(b'hello\x00world','list.txt'),(b'\xff\xfeinvalid','list.txt'),(b'Milk','list.pdf'),(b'x'*(MAX_UPLOAD+1),'list.txt')], ids=['binary','encoding','extension','size'])
def test_txt_rejects_unsupported_binary_encoding_size(client,blob,filename):
    response=client.post('/products/import',data={'method':'text','text_file':(io.BytesIO(blob),filename)})
    assert response.status_code in (400,413) and Product.query.count()==0


@pytest.mark.parametrize('text',['','\n \n','\n'*1001,'x'*(MAX_UPLOAD+1),'Product Name\tUnknown\nMilk\t3'], ids=['empty','blank','line-limit','size','header'])
def test_text_limits_and_headers(client,text):
    assert paste(client,text).status_code in (400,413) and Product.query.count()==0


def test_duplicate_lines_can_remove_and_edit(client):
    response=paste(client,'Coke\nFanta\nCoke')
    assert b'Duplicate product row' in response.data
    result=edit(client,response,[{'name':'Coke'},{'name':'Fanta'},{'action':'skip'}])
    assert result.status_code==302 and Product.query.count()==2


def test_existing_product_choice_and_business_isolation(client):
    client.post('/products/new',data={'name':'Coke','sku':'COKE','stock_quantity':'8'})
    other=Product(business_id=2,name='Bread',sku='OTHER',stock_quantity=9,opening_quantity=9,buying_price=1,selling_price=2)
    db.session.add(other);db.session.commit()
    response=paste(client,'coke\nBread')
    assert b'Possible existing product' in response.data and b'Use Existing Product: Coke' in response.data
    assert b'Use Existing Product: Bread' not in response.data
    product=Product.query.filter_by(business_id=1).one()
    result=edit(client,response,[{'action':f'existing:{product.id}'},{'name':'Bread'}])
    assert result.status_code==302 and Product.query.count()==3
    assert db.session.get(Product,product.id).stock_quantity==8
    bad=paste(client,'Another')
    assert edit(client,bad,[{'action':f'existing:{other.id}'}]).status_code==403


def test_existing_name_create_as_new_and_sku_conflict(client):
    client.post('/products/new',data={'name':'Milk','sku':'MILK'})
    response=paste(client,'Product Name\tSKU\nMilk\tMILK')
    assert b'Duplicate SKU' in response.data
    failed=edit(client,response,[{'name':'Milk','sku':'milk'}])
    assert failed.status_code==400 and Product.query.count()==1
    assert edit(client,failed,[{'name':'Milk','sku':'NEW'}]).status_code==302
    assert Product.query.count()==2


@pytest.mark.parametrize('field,value',[('stock_quantity','-1'),('stock_quantity','1.5'),('buying_price','-3'),('selling_price','NaN'),('selling_price','1.999'),('minimum_stock_level','-1')])
def test_edited_values_revalidated(client,field,value):
    response=paste(client,'Milk')
    assert edit(client,response,[{'name':'Milk',field:value}]).status_code==400
    assert Product.query.count()==0


def test_skip_all_replay_cannot_create_later(client):
    response=paste(client,'Milk')
    assert edit(client,response,[{'action':'skip'}]).status_code==302
    assert AuditLog.query.filter_by(action='PRODUCT_IMPORT').count()==1
    assert edit(client,response,[{'name':'Milk'}]).status_code==302
    assert Product.query.count()==0


def test_edit_security_and_replay(client):
    response=paste(client,'Milk')
    preview=token(response)
    assert confirm(client,preview+'changed').status_code==400
    assert edit(client,response,[{'name':'Edited','stock_quantity':'7'}]).status_code==302
    assert edit(client,response,[{'name':'Replay','stock_quantity':'100'}]).status_code==302
    assert Product.query.count()==1 and StockMovement.query.one().quantity_change==7
    client.post('/auth/logout')
    assert paste(client,'Anonymous').status_code==302
    client.post('/auth/login',data={'email':'two@example.com','password':'password123'})
    assert confirm(client,preview).status_code==403


def test_large_paste(client):
    response=paste(client,'\n'.join(f'Product {i}' for i in range(1000)))
    assert response.status_code==200
    assert confirm(client,token(response)).status_code==302
    assert Product.query.count()==1000 and StockMovement.query.count()==1000


def test_edited_import_rollback(client):
    from sqlalchemy import event
    response=paste(client,'Milk\nBread')
    def fail(*args):raise RuntimeError('injected failure')
    event.listen(StockMovement,'before_insert',fail)
    try:
        with pytest.raises(RuntimeError):edit(client,response,[{'name':'Milk'},{'name':'Bread'}])
    finally:event.remove(StockMovement,'before_insert',fail)
    assert Product.query.count()==0 and StockMovement.query.count()==0 and AuditLog.query.count()==0


@pytest.mark.parametrize('source',[upload,xlsx])
def test_spreadsheet_review_can_edit(client,source):
    response=source(client,[{'name':'Spreadsheet','stock_quantity':'4'}])
    assert edit(client,response,[{'name':'Edited spreadsheet','stock_quantity':'12','buying_price':'100','selling_price':'150'}]).status_code==302
    assert Product.query.one().stock_quantity==12 and StockMovement.query.one().quantity_change==12


def test_paste_preview_expiry(client,monkeypatch):
    response=paste(client,'Milk')
    from itsdangerous import TimestampSigner
    original=TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner,'get_timestamp',lambda self:original(self)+1300)
    assert edit(client,response,[{'name':'Milk'}]).status_code==400 and Product.query.count()==0


def test_case_insensitive_duplicate_lines(client):
    response=paste(client,'Coke\ncoke')
    assert b'Duplicate product row' in response.data
    assert confirm(client,token(response)).status_code==400
    assert Product.query.count()==0


def test_explicit_invalid_header_values(client):
    response=paste(client,'Product Name\tQuantity\tSelling Price\nMilk\t-2\tNaN')
    assert b'need attention' in response.data
    assert confirm(client,token(response)).status_code==400 and Product.query.count()==0


def test_request_csrf_and_unknown_action(client):
    client.application.config['WTF_CSRF_ENABLED']=True
    assert paste(client,'Blocked').status_code==400
    client.application.config['WTF_CSRF_ENABLED']=False
    response=paste(client,'Milk')
    assert edit(client,response,[{'action':'overwrite'}]).status_code==400
    assert Product.query.count()==0


def test_corrections_keep_original_deadline_and_summary(client,monkeypatch):
    response=paste(client,'Milk\nBread')
    corrected=edit(client,response,[{'action':'skip'},{'name':'Bread','stock_quantity':'-1'}])
    assert corrected.status_code==400
    import time
    original=time.time
    monkeypatch.setattr(time,'time',lambda:original()+1201)
    assert edit(client,corrected,[{'name':'Bread','stock_quantity':'3'}]).status_code==400
    assert Product.query.count()==0


def test_corrected_summary_preserves_skips(client):
    response=paste(client,'Milk\nBread')
    corrected=edit(client,response,[{'action':'skip'},{'name':'Bread','stock_quantity':'-1'}])
    done=edit(client,corrected,[{'name':'Bread','stock_quantity':'3'}])
    assert done.status_code==302
    assert b'1 rows skipped' in client.get('/products/').data
