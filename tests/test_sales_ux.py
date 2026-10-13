from app import db
from app.models import Product, Sale, TransactionSubmission
from tests.test_business_workflows import client, owner, add
from tests.sales_submission_helpers import with_sale_token


def test_sales_controls_and_date_guidance(client):
    owner(client)
    add(client,'Rice',5)
    html=client.get('/sales/').data
    for text in [b'Browse Products',b'Shows up to 15',b'No products selected',b'0 products selected',b'Leave Date blank',b'UTC',b'submission_token']:
        assert text in html
    restock=client.get('/restocking/').data
    assert b'sale-product-picker' not in restock and b'basket-empty' in restock
    assert b'restock-review' in restock


def test_actions_hide_void_form_and_preserve_reason(client):
    owner(client);pid=add(client,'Rice',5)
    payload=with_sale_token(client,{'product_id':str(pid),'quantity':'1','unit_price':''})
    client.post('/sales/',data=payload)
    sale=Sale.query.one()
    html=client.get('/sales/').data
    assert b'View Details' in html and b'Confirm Void Sale' in html
    assert f'<form hidden id="void-sale-{sale.id}"'.encode() in html
    assert b'name="reason" required maxlength="300"' in html
    assert client.post(f'/sales/{sale.id}/delete',data={'reason':''}).status_code==302
    assert db.session.get(Sale,sale.id).voided_at is None


def test_stock_changes_after_selection_remain_server_checked(client):
    owner(client);pid=add(client,'Rice',5)
    payload=with_sale_token(client,{'product_id':str(pid),'quantity':'3','unit_price':''})
    db.session.get(Product,pid).stock_quantity=2;db.session.commit()
    response=client.post('/sales/',data=payload)
    assert response.status_code==200 and b'Only 2 units of Rice' in response.data
    assert Sale.query.count()==0 and TransactionSubmission.query.count()==0
    assert db.session.get(Product,pid).stock_quantity==2


def test_picker_query_remains_bounded_and_scoped(client):
    owner(client)
    for i in range(16):add(client,f'Rice {i}',5)
    response=client.get('/products/lookup?q=')
    assert response.status_code==200 and len(response.json['products'])==15
    assert response.headers['Cache-Control']=='no-store, private'


def test_sales_styles_are_scoped_and_touch_accessible():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    css=(root/'app/static/css/sales.css').read_text()
    assert '#sale-form' in css and '.sale-actions' in css
    assert 'min-height:44px' in css and 'min-width:0' in css and ':focus-visible' in css
    template=(root/'app/templates/sales/index.html').read_text()
    assert "filename='css/sales.css',v='sales-ux-2'" in template
    assert 'aria-controls="void-sale-' in template
