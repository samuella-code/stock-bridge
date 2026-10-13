"""Phase 7C-A checks run only against disposable in-memory fixtures."""
import io
import pytest
from werkzeug.datastructures import FileStorage
from app import db
from app.models import Product, StockMovement
from app.products.catalogue import grid_to_rows, read_spreadsheet
from tests.test_catalogue import client, xlsx, token, confirm
from tests.test_product_lists import paste, edit


def test_help_authentication(client):
    response=client.get('/help')
    assert b'Back to Dashboard' in response.data and b'>Sign In<' not in response.data
    assert b'aria-label="Guide topics"' in response.data
    client.post('/auth/logout')
    response=client.get('/help')
    assert response.status_code==200 and b'>Sign In<' in response.data
    assert b'Back to Dashboard' not in response.data


def test_headerless_excel():
    from openpyxl import Workbook
    wb=Workbook();ws=wb.active;ws.append(['Rice']);ws.append(['Bread'])
    out=io.BytesIO();wb.save(out);out.seek(0)
    rows=read_spreadsheet(FileStorage(stream=out,filename='names.xlsx'))
    assert [(r['name'],r['_row']) for r in rows]==[('Rice',1),('Bread',2)]


@pytest.mark.parametrize('header',['Product Name','Product','Item Name'])
def test_aliases(header):
    rows=grid_to_rows([[header,'Quantity','Buying Price','Selling Price'],['Rice',3,100,200]])
    assert rows[0]['name']=='Rice' and rows[0]['stock_quantity']=='3'
    assert rows[0]['buying_price']=='100' and rows[0]['selling_price']=='200'


@pytest.mark.parametrize('headers',[['Product','Item Name'],['Product Name','Product Name'],['Product','Price'],['Product','Quantity','Opening Quantity'],['Price']])
def test_ambiguous_headers(headers):
    with pytest.raises(ValueError): grid_to_rows([headers,['Rice']*len(headers)])


def test_defaults_warnings_and_methods(client):
    response=paste(client,'Rice')
    assert b'Needs Review' in response.data and b'Opening Quantity is zero' in response.data
    assert b'Selling Price is zero' in response.data and b'Default: unit' in response.data
    assert b'value="0"' in response.data
    entry=client.get('/products/import').data
    for label in [b'Spreadsheet',b'Paste List',b'Text File',b'Download example CSV template']:
        assert label in entry
    example=client.get('/products/import/template')
    assert example.status_code==200 and b'Product Name' in example.data


def test_duplicate_requires_explicit_action(client):
    client.post('/products/new',data={'name':'Rice','stock_quantity':'8'})
    response=paste(client,'Rice')
    assert b'Choose an action' in response.data and b'Needs Review' in response.data
    assert confirm(client,token(response)).status_code==400
    failed=edit(client,response,[{'name':'Rice'}])
    assert failed.status_code==400 and b'choose an action' in failed.data
    assert Product.query.count()==1
    assert edit(client,failed,[{'name':'Rice','action':'new'}]).status_code==302
    assert Product.query.count()==2 and StockMovement.query.count()==2


def test_invalid_inline_labels(client):
    response=paste(client,'Product Name\tQuantity\tBuying Price\tSelling Price\nRice\t-1\tbad\tNaN')
    assert b'Invalid' in response.data and b'Opening Quantity must' in response.data
    assert b'Cost Price must' in response.data and b'Selling Price must' in response.data
    assert b'2147483647' not in response.data and b'aria-invalid=true' in response.data
    assert Product.query.count()==0


def test_zeros_are_warnings_not_blocked(client):
    response=paste(client,'Rice')
    assert confirm(client,token(response)).status_code==302
    assert Product.query.one().stock_quantity==0


def test_compact_help_and_single_panel_styles():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    css=(root/'app/static/css/legal.css').read_text()
    assert '.help-public .policy-navigation .policy-links{display:flex;flex-direction:row;flex-wrap:wrap' in css
    assert ':focus-visible' in css
    entry=(root/'app/templates/products/import.html').read_text()
    assert entry.count('name="import-method"')==3 and entry.count('type="radio" checked')==1
    assert 'aria-label="Choose import method"' in entry


@pytest.mark.parametrize('header',['Product','Item Name'])
def test_pasted_aliases(client,header):
    response=paste(client,f'{header}\tQuantity\tBuying Price\tSelling Price\nRice\t3\t100\t200')
    assert response.status_code==200 and b'1 Ready' in response.data


def test_pasted_duplicate_alias_rejected(client):
    assert paste(client,'Product\tItem Name\nRice\tRice').status_code==400


def test_editing_to_existing_name_requires_new_review(client):
    client.post('/products/new',data={'name':'Rice'})
    response=paste(client,'Bread')
    changed=edit(client,response,[{'name':'Rice','action':'new'}])
    assert changed.status_code==400 and b'choose an action' in changed.data
    assert Product.query.count()==1
    assert edit(client,changed,[{'name':'Rice','action':'new'}]).status_code==302
