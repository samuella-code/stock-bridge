import re
from unittest.mock import patch
import pytest
from app import db
from app.models import Product, Restock, StockMovement, Notification, EmailOutbox, TransactionSubmission
from tests.test_business_workflows import client, owner, add


def token(client):
    return re.search(rb'name="submission_token" value="([^"]+)"', client.get('/restocking/').data)[1].decode()


def seed(client):
    owner(client)
    return [add(client, n, 10) for n in ('Water', 'Bread')]


def payload(ids, key, qty='2', cost='120.50'):
    return dict(product_id=ids, quantity=[qty]*len(ids), unit_cost=[cost]*len(ids),
                submission_token=key, supplier='Supplier', note='Purchase note')


@pytest.mark.parametrize('multi', [False, True])
def test_restock_replay_preserves_batch_cost_and_events(client, multi):
    ids = seed(client)[:2 if multi else 1]; key = token(client); data = payload(ids, key)
    assert client.post('/restocking/receive', data=data).status_code == 302
    rows = Restock.query.all(); batch = rows[0].batch_id
    assert len(rows) == len(ids) and {r.batch_id for r in rows} == {batch}
    assert all(str(r.unit_cost) == '120.50' and r.supplier == 'Supplier' and r.note == 'Purchase note' for r in rows)
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [12]*len(ids)
    assert Notification.query.filter_by(kind='restock_recorded').count() == 1
    assert EmailOutbox.query.filter_by(event_type='restock_recorded').count() == 1
    # Later stock and price changes must not affect replay eligibility or history.
    for pid in ids:
        p = db.session.get(Product, pid); p.stock_quantity = 8; p.buying_price = 999
    db.session.commit()
    with patch('app.restocking.routes.uuid4', side_effect=AssertionError('Replay generated new batch')):
        assert client.post('/restocking/receive', data=data).status_code == 302
    assert Restock.query.count() == len(ids)
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [8]*len(ids)
    assert StockMovement.query.filter_by(kind='restock').count() == len(ids)
    assert Notification.query.filter_by(kind='restock_recorded').count() == 1
    assert EmailOutbox.query.filter_by(event_type='restock_recorded').count() == 1
    assert TransactionSubmission.query.one().result_reference == batch
    assert all(str(r.unit_cost) == '120.50' for r in Restock.query.all())


@pytest.mark.parametrize('field,value', [('quantity', ['3']), ('unit_cost', ['121.00']), ('supplier', 'Other'), ('note', 'Other')])
def test_changed_payload_conflict(client, field, value):
    ids = seed(client)[:1]; data = payload(ids, token(client))
    client.post('/restocking/receive', data=data)
    changed = dict(data); changed[field] = value
    assert client.post('/restocking/receive', data=changed).status_code == 409
    assert Restock.query.count() == 1 and db.session.get(Product, ids[0]).stock_quantity == 12


def test_identical_restock_with_new_token_and_normalized_replay(client):
    ids = seed(client)[:1]; data = payload(ids, token(client))
    client.post('/restocking/receive', data=data)
    normalized = dict(data, quantity=['02'], unit_cost=['120.5'])
    assert client.post('/restocking/receive', data=normalized).status_code == 302
    assert Restock.query.count() == 1
    assert client.post('/restocking/receive', data=payload(ids, token(client))).status_code == 302
    assert Restock.query.count() == 2 and db.session.get(Product, ids[0]).stock_quantity == 14


def test_validation_keeps_token_and_values_for_retry(client):
    ids = seed(client)[:1]; key = token(client)
    response = client.post('/restocking/receive', data=payload(ids, key, qty='0'))
    assert response.status_code == 200 and key.encode() in response.data
    assert b'restock-restore' in response.data and b'Purchase note' in response.data
    assert TransactionSubmission.query.count() == Restock.query.count() == 0
    assert client.post('/restocking/receive', data=payload(ids, key)).status_code == 302


@pytest.mark.parametrize('failure', ['commit', 'second_update'])
def test_failure_rolls_back_all_and_token_can_retry(client, monkeypatch, failure):
    ids = seed(client); key = token(client); session = db.session()
    original = session.execute; count = 0; notifications = Notification.query.count()
    def fail(): raise RuntimeError('Synthetic commit failure')
    def execute(stmt, *args, **kwargs):
        nonlocal count
        if getattr(stmt, 'is_update', False) and stmt.table.name == 'product':
            count += 1
            if count == 2: raise RuntimeError('Synthetic update failure')
        return original(stmt, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(session, 'commit', fail) if failure == 'commit' else m.setattr(session, 'execute', execute)
        with pytest.raises(RuntimeError): client.post('/restocking/receive', data=payload(ids, key))
    assert TransactionSubmission.query.count() == Restock.query.count() == 0
    assert StockMovement.query.filter_by(kind='restock').count() == 0
    assert Notification.query.count() == notifications and EmailOutbox.query.count() == 0
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [10, 10]
    assert client.post('/restocking/receive', data=payload(ids, key)).status_code == 302


@pytest.mark.parametrize('invalid', ['missing', 'expired', 'tampered'])
def test_bad_token_and_history_guidance(client, invalid):
    ids = seed(client)[:1]; key = token(client)
    if invalid == 'missing': key = ''
    elif invalid == 'tampered': key += 'x'
    else:
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=1): key = token(client)
    response = client.post('/restocking/receive', data=payload(ids, key))
    assert response.status_code == 400 and b'Check restocking history' in response.data
    assert Restock.query.count() == 0


def test_cross_user_rejected_and_csrf_preserved(client):
    ids = seed(client)[:1]; original = token(client)
    client.post('/auth/logout'); owner(client, 'other@example.com')
    assert client.post('/restocking/receive', data=payload(ids, original)).status_code == 400
    assert client.post('/restocking/receive', data=payload(ids, token(client))).status_code == 404
    assert Restock.query.count() == TransactionSubmission.query.count() == 0
    client.application.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/restocking/receive', data=payload(ids, token(client))).status_code == 400
