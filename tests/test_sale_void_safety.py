"""Disposable SQLite functional coverage; not PostgreSQL concurrency proof."""
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Query
from app import db
from app.models import Product, Sale, StockMovement, Notification, EmailOutbox
from tests.test_business_workflows import client, owner, add


def recorded(client):
    owner(client)
    ids = [add(client, name, 10) for name in ('Water', 'Bread')]
    # Reverse item order deliberately to exercise deterministic restoration order.
    client.post('/sales/', data={'product_id': ids[::-1], 'quantity': ['3', '2'],
                               'unit_price': ['', '']})
    sale_id = Sale.query.one().id
    return ids, sale_id


def test_void_and_replay_preserve_history_and_notifications(client):
    ids, sale_id = recorded(client)
    notifications = [(n.id, n.event_key, n.read_at) for n in Notification.query.all()]
    outbox = [(n.id, n.event_key, n.status) for n in EmailOutbox.query.all()]
    assert client.post(f'/sales/{sale_id}/delete', data={'reason': 'Return'}).status_code == 302
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [10, 10]
    movements = StockMovement.query.filter_by(sale_id=sale_id, kind='void').order_by(StockMovement.id).all()
    assert [(m.product_id, m.quantity_change) for m in movements] == list(zip(ids, [2, 3]))
    timestamp = db.session.get(Sale, sale_id).voided_at
    assert timestamp is not None
    response = client.post(f'/sales/{sale_id}/delete', data={'reason': 'Again'}, follow_redirects=True)
    assert b'This sale is already voided.' in response.data
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [10, 10]
    assert StockMovement.query.filter_by(sale_id=sale_id, kind='void').count() == 2
    assert db.session.get(Sale, sale_id).voided_at == timestamp
    assert [(n.id, n.event_key, n.read_at) for n in Notification.query.all()] == notifications
    assert [(n.id, n.event_key, n.status) for n in EmailOutbox.query.all()] == outbox


@pytest.mark.parametrize('failure', ['second_update', 'commit', 'lock'])
def test_transaction_failure_restores_nothing(client, monkeypatch, failure):
    ids, sale_id = recorded(client)
    session = db.session()
    original_execute = session.execute
    calls = 0
    def execute(statement, *args, **kwargs):
        nonlocal calls
        if getattr(statement, 'is_update', False) and statement.table.name == 'product':
            calls += 1
            if failure == 'second_update' and calls == 2:
                raise RuntimeError('Synthetic update failure')
        return original_execute(statement, *args, **kwargs)
    def fail(*args, **kwargs):
        raise RuntimeError('Synthetic transaction failure')
    with monkeypatch.context() as patch:
        if failure == 'commit':
            patch.setattr(session, 'commit', fail)
        elif failure == 'lock':
            patch.setattr(Query, 'with_for_update', fail)
        else:
            patch.setattr(session, 'execute', execute)
        with pytest.raises(RuntimeError):
            client.post(f'/sales/{sale_id}/delete', data={'reason': 'Return'})
    db.session.expire_all()
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [8, 7]
    assert db.session.get(Sale, sale_id).voided_at is None
    assert db.session.get(Sale, sale_id).void_reason is None
    assert StockMovement.query.filter_by(sale_id=sale_id, kind='void').count() == 0


def test_cross_account_void_rejected(client):
    ids, sale_id = recorded(client)
    client.post('/auth/logout')
    owner(client, 'other@example.com')
    assert client.post(f'/sales/{sale_id}/delete', data={'reason': 'Forbidden'}).status_code == 404
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [8, 7]
    assert db.session.get(Sale, sale_id).voided_at is None
    assert StockMovement.query.filter_by(sale_id=sale_id, kind='void').count() == 0


def test_route_uses_scoped_postgresql_lock_and_refresh(client, monkeypatch):
    _, sale_id = recorded(client)
    original = Query.with_for_update
    captured = []
    def inspect(query, *args, **kwargs):
        locked = original(query, *args, **kwargs)
        captured.append((str(locked.statement.compile(dialect=postgresql.dialect())),
                         locked.load_options._populate_existing))
        return locked
    monkeypatch.setattr(Query, 'with_for_update', inspect)
    client.post(f'/sales/{sale_id}/delete', data={'reason': 'Return'})
    assert len(captured) == 1
    sql, refresh = captured[0]
    assert 'FOR UPDATE' in sql and 'sale.business_id =' in sql and 'sale.id =' in sql
    assert refresh
