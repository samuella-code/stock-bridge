import re
from unittest.mock import patch
import pytest
from app import db
from app.models import Product, Sale, SaleItem, StockMovement, Notification, EmailOutbox, TransactionSubmission
from tests.test_business_workflows import client, owner, add


def token(client):
    return re.search(rb'name="submission_token" value="([^"]+)"', client.get('/sales/').data)[1].decode()


def seed(client):
    owner(client)
    return [add(client, n, 10) for n in ('Water', 'Bread')]


def data(ids, key, qty='2'):
    return dict(product_id=ids, quantity=[qty]*len(ids), unit_price=['']*len(ids), submission_token=key)


@pytest.mark.parametrize('multi', [False, True])
def test_success_replay_and_changed_payload(client, multi):
    ids = seed(client)[:2 if multi else 1]
    key = token(client)
    payload = data(ids, key)
    assert client.post('/sales/', data=payload).status_code == 302
    total = Sale.query.one().total
    notifications = Notification.query.count(); outbox = EmailOutbox.query.count()
    # Defaults change after original commit: replay must use original receipt.
    for pid in ids: db.session.get(Product, pid).selling_price = 999
    db.session.commit()
    assert client.post('/sales/', data=payload).status_code == 302
    assert Sale.query.count() == 1 and Sale.query.one().total == total
    assert SaleItem.query.count() == len(ids)
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [8]*len(ids)
    assert StockMovement.query.filter_by(kind='sale').count() == len(ids)
    assert Notification.query.count() == notifications and EmailOutbox.query.count() == outbox
    assert TransactionSubmission.query.count() == 1
    assert client.post('/sales/', data=data(ids, key, '3')).status_code == 409
    assert Sale.query.count() == 1
    # Explicit price equal to original default is different intent.
    changed = dict(payload, unit_price=['350.00']*len(ids))
    assert client.post('/sales/', data=changed).status_code == 409


def test_different_tokens_allow_identical_sales(client):
    ids = seed(client)[:1]
    for _ in range(2): assert client.post('/sales/', data=data(ids, token(client))).status_code == 302
    assert Sale.query.count() == 2 and db.session.get(Product, ids[0]).stock_quantity == 6


def test_failed_stock_validation_keeps_token_for_retry(client):
    ids = seed(client)[:1]; key = token(client)
    response = client.post('/sales/', data=data(ids, key, '11'))
    assert response.status_code == 200 and key.encode() in response.data and b'sale-restore' in response.data
    assert Sale.query.count() == TransactionSubmission.query.count() == 0
    assert client.post('/sales/', data=data(ids, key)).status_code == 302


def test_commit_failure_rolls_back_everything_and_retry(client, monkeypatch):
    ids = seed(client); key = token(client)
    notification_count = Notification.query.count()
    session = db.session()
    def fail(): raise RuntimeError('Synthetic commit failure')
    with monkeypatch.context() as m:
        m.setattr(session, 'commit', fail)
        with pytest.raises(RuntimeError): client.post('/sales/', data=data(ids, key, '5'))
    assert Sale.query.count() == TransactionSubmission.query.count() == SaleItem.query.count() == 0
    assert EmailOutbox.query.count() == 0 and Notification.query.count() == notification_count
    assert [db.session.get(Product, pid).stock_quantity for pid in ids] == [10, 10]
    assert client.post('/sales/', data=data(ids, key, '5')).status_code == 302
    count = Notification.query.count(); jobs = EmailOutbox.query.count()
    assert jobs == 2  # one low-stock job per product, committed with the sale
    client.post('/sales/', data=data(ids, key, '5'))
    assert Notification.query.count() == count and EmailOutbox.query.count() == jobs


@pytest.mark.parametrize('kind', ['missing', 'expired', 'foreign'])
def test_invalid_token_rejected(client, kind):
    ids = seed(client)[:1]; key = token(client)
    if kind == 'missing': key = ''
    elif kind == 'expired':
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=1): key = token(client)
    else:
        from app.transaction_submissions import issue_token
        from app.models import Business
        b = Business.query.first()
        key = issue_token(b.user_id, b.id, 'expense')
    response = client.post('/sales/', data=data(ids, key))
    assert response.status_code == 400 and b'Check sales history' in response.data
    assert Sale.query.count() == 0


def test_csrf_preserved(client):
    ids = seed(client)[:1]; key = token(client)
    client.application.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/sales/', data=data(ids, key)).status_code == 400
    assert Sale.query.count() == 0


def test_cross_user_and_product_authorization(client):
    ids = seed(client)[:1]; old = token(client)
    client.post('/auth/logout')
    owner(client, 'second@example.com')
    assert client.post('/sales/', data=data(ids, old)).status_code == 400
    assert client.post('/sales/', data=data(ids, token(client))).status_code == 404
    assert TransactionSubmission.query.count() == Sale.query.count() == 0
    assert db.session.get(Product, ids[0]).stock_quantity == 10


def test_validation_keeps_token_and_values(client):
    ids = seed(client)[:1]; key = token(client)
    payload = dict(data(ids, key, '0'), note='Customer note', sold_at='2026-10-01', payment_method='POS')
    response = client.post('/sales/', data=payload)
    assert response.status_code == 200
    assert key.encode() in response.data and b'Customer note' in response.data
    assert b'2026-10-01' in response.data and b'sale-restore' in response.data
    assert TransactionSubmission.query.count() == 0


def test_replay_after_selling_all_stock(client):
    ids = seed(client)[:1]; key = token(client)
    payload = data(ids, key, '10')
    assert client.post('/sales/', data=payload).status_code == 302
    assert db.session.get(Product, ids[0]).stock_quantity == 0
    assert client.post('/sales/', data=payload).status_code == 302
    assert Sale.query.count() == 1 and EmailOutbox.query.filter_by(event_type='out_of_stock').count() == 1
    assert db.session.get(Product, ids[0]).stock_quantity == 0
