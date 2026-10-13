from datetime import datetime
from decimal import Decimal
from pathlib import Path
from app import db
from app.models import Restock, Product, Business
from app.restocking.history import receipt_history
from tests.test_business_workflows import client, owner, add


def test_receipt_grouping_and_legacy_isolation(client):
    owner(client);pid=add(client,'Rice',5);product=db.session.get(Product,pid)
    other=Business(user_id=db.session.get(Business,product.business_id).user_id,name='Other')
    db.session.add(other);db.session.flush()
    for bid, batch, cost in [(product.business_id,'same',Decimal('100')),(product.business_id,'same',Decimal('150')),
                             (product.business_id,None,Decimal('50')),(product.business_id,'',Decimal('60')),
                             (other.id,'same',Decimal('999'))]:
        db.session.add(Restock(business_id=bid,product_id=pid,quantity=2,unit_cost=cost,batch_id=batch,received_at=datetime(2026,10,1)))
    db.session.commit()
    history=receipt_history(product.business_id,1)
    assert history.total==3
    group=next(r for r in history.items if r['reference']=='batch:same')
    assert group['item_count']==2 and group['total']==Decimal('500')
    assert [r.unit_cost for r in group['items']]==[Decimal('100'),Decimal('150')]
    assert sum(r['item_count'] for r in history.items)==4
    assert Restock.query.count()==5


def test_batch_not_split_across_pages(client):
    owner(client);pid=add(client,'Rice',5);bid=db.session.get(Product,pid).business_id
    for i in range(21):
        for _ in range(2):db.session.add(Restock(business_id=bid,product_id=pid,quantity=1,unit_cost=3,batch_id=f'batch-{i}'))
    db.session.commit()
    first,second=receipt_history(bid,1),receipt_history(bid,2)
    assert first.total==21 and len(first.items)==20 and len(second.items)==1
    assert all(r['item_count']==2 and r['total']==6 for r in first.items+second.items)
    assert {r['reference'] for r in first.items}.isdisjoint(r['reference'] for r in second.items)


def test_picker_and_sales_action_accessibility(client):
    owner(client);add(client,'Rice',5)
    for url in ['/sales/','/restocking/']:
        html=client.get(url).data
        assert b'<details class="basket-browse">' in html
        assert b'<details class="basket-browse" open' not in html
        assert b'Search products by name' in html
    restock=client.get('/restocking/').data
    assert b'Review: 0 products' in restock and b'Expected stock is an estimate' in restock
    source=(Path(__file__).resolve().parents[1]/'app/templates/sales/index.html').read_text()
    assert '>⋯</summary>' in source and '>⋯ Actions</summary>' not in source
    assert 'aria-label="Actions for sale' in source
