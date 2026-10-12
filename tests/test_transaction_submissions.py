"""Local functional tests, not PostgreSQL concurrency evidence."""
import pytest
from unittest.mock import patch
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import Product, TransactionSubmission
from app.transaction_submissions import (issue_token, validate_token, payload_hash, claim,
    complete, SubmissionError, SubmissionConflict)
from tests.test_business_workflows import client, owner, add


def context(client):
    bid = owner(client)
    from app.models import Business
    return Business.query.get(bid).user_id, bid


def test_replay_changed_payload_and_identical_new_transaction(client):
    uid, bid = context(client)
    token = issue_token(uid, bid, 'sale')
    row, replay = claim(token, uid, bid, 'sale', {'amount': '10.00'})
    assert not replay
    complete(row, 'sale:123')
    db.session.commit()
    row, replay = claim(token, uid, bid, 'sale', {'amount': '10.00'})
    assert replay and row.result_reference == 'sale:123'
    db.session.rollback()
    with pytest.raises(SubmissionConflict):
        claim(token, uid, bid, 'sale', {'amount': '11.00'})
    db.session.rollback()
    second, replay = claim(issue_token(uid, bid, 'sale'), uid, bid, 'sale', {'amount': '10.00'})
    assert not replay
    complete(second, 'sale:124')
    db.session.commit()
    assert TransactionSubmission.query.count() == 2


@pytest.mark.parametrize('bad', ['tampered', 'expired', 'operation', 'user', 'business'])
def test_token_rejection(client, bad):
    uid, bid = context(client)
    token = issue_token(uid, bid, 'sale')
    args = [token, uid, bid, 'sale']
    if bad == 'tampered': args[0] = token + 'x'
    if bad == 'operation': args[3] = 'expense'
    if bad == 'user': args[1] += 999
    if bad == 'business': args[2] += 999
    if bad == 'expired':
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=1):
            args[0] = issue_token(uid, bid, 'sale')
    with pytest.raises(SubmissionError): validate_token(*args)
    assert TransactionSubmission.query.count() == 0


def test_atomic_rollback_and_unfinished_commit_rejected(client):
    uid, bid = context(client)
    pid = add(client, 'Water', 10)
    token = issue_token(uid, bid, 'adjustment')
    receipt, _ = claim(token, uid, bid, 'adjustment', {'quantity': 2})
    db.session.get(Product, pid).stock_quantity += 2
    with pytest.raises(SubmissionError): db.session.commit()
    db.session.rollback()
    assert TransactionSubmission.query.count() == 0
    assert db.session.get(Product, pid).stock_quantity == 10
    receipt, _ = claim(token, uid, bid, 'adjustment', {'quantity': 2})
    db.session.get(Product, pid).stock_quantity += 2
    complete(receipt, 'movement:12')
    db.session.rollback()
    assert TransactionSubmission.query.count() == 0
    assert db.session.get(Product, pid).stock_quantity == 10


def test_unique_scope_constraint(client):
    uid, bid = context(client)
    values = dict(user_id=uid, business_id=bid, operation='expense', key='a'*64, payload_hash='b'*64)
    db.session.add_all([TransactionSubmission(**values), TransactionSubmission(**values)])
    with pytest.raises(IntegrityError): db.session.flush()
    db.session.rollback()
    assert TransactionSubmission.query.count() == 0


def test_canonical_payload(client):
    assert payload_hash({'b': 2, 'a': '10.00'}) == payload_hash({'a': '10.00', 'b': 2})
    with pytest.raises(SubmissionError): payload_hash({'money': 1.2})


def test_database_failure_never_retries(client, monkeypatch):
    uid, bid = context(client)
    token = issue_token(uid, bid, 'restock')
    session = db.session()
    original = session.execute
    calls = []
    def fail(statement, *args, **kwargs):
        if getattr(statement, 'is_insert', False):
            calls.append(statement)
            raise RuntimeError('Synthetic SQL failure')
        return original(statement, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(session, 'execute', fail)
        with pytest.raises(RuntimeError): claim(token, uid, bid, 'restock', {})
    db.session.rollback()
    assert len(calls) == 1 and TransactionSubmission.query.count() == 0


def test_committed_mutation_and_receipt_replay_atomically(client):
    uid, bid = context(client)
    pid = add(client, 'Stock', 10)
    token = issue_token(uid, bid, 'adjustment')
    row, replay = claim(token, uid, bid, 'adjustment', {'quantity': 2})
    db.session.get(Product, pid).stock_quantity += 2
    complete(row, 'movement:99')
    db.session.commit()
    row, replay = claim(token, uid, bid, 'adjustment', {'quantity': 2})
    assert replay and row.result_reference == 'movement:99'
    assert db.session.get(Product, pid).stock_quantity == 12
    assert TransactionSubmission.query.count() == 1
    db.session.rollback()


def test_signed_token_cannot_move_to_another_owned_business(client):
    from app.models import Business
    uid, bid = context(client)
    other = Business(user_id=uid, name='Other', subscription_status='active')
    db.session.add(other); db.session.commit()
    token = issue_token(uid, bid, 'expense')
    with pytest.raises(SubmissionError): validate_token(token, uid, other.id, 'expense')


def test_additive_migration_schema_compiles_for_postgresql():
    import importlib.util
    from sqlalchemy import MetaData, Table, Column, Integer
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    spec = importlib.util.spec_from_file_location('submission_migration', 'migrations/versions/0017_transaction_submission.py')
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    metadata = MetaData()
    Table('business', metadata, Column('id', Integer, primary_key=True))
    Table('user', metadata, Column('id', Integer, primary_key=True))
    captured = []
    class Operations:
        def create_table(self, name, *columns):
            captured.append(Table(name, metadata, *columns))
    with patch.object(migration, 'op', Operations()): migration.upgrade()
    assert migration.down_revision == '0016_inventory_email_outbox'
    assert len(captured) == 1
    sql = str(CreateTable(captured[0]).compile(dialect=postgresql.dialect()))
    assert 'uq_transaction_submission_scope' in sql and 'FOREIGN KEY' in sql
    with pytest.raises(RuntimeError): migration.downgrade()


@pytest.mark.parametrize('operation', ['sale', 'restock', 'expense'])
def test_insert_returning_ignores_unknown_rowcount(client, monkeypatch, operation):
    uid, bid = context(client)
    token = issue_token(uid, bid, operation)
    session = db.session()
    execute = session.execute

    class UnknownRowcount:
        @property
        def rowcount(self):
            raise AssertionError('INSERT rowcount must not determine claim ownership')

        def __init__(self, result):
            self.result = result

        def scalar_one_or_none(self):
            return self.result.scalar_one_or_none()

    def wrapped(statement, *args, **kwargs):
        result = execute(statement, *args, **kwargs)
        if getattr(statement, 'is_insert', False):
            assert statement._returning
            return UnknownRowcount(result)
        return result

    monkeypatch.setattr(session, 'execute', wrapped)
    receipt, replay = claim(token, uid, bid, operation, {'quantity': 1})
    assert not replay
    complete(receipt, 'original:1')
    db.session.commit()
    receipt, replay = claim(token, uid, bid, operation, {'quantity': 1})
    assert replay and receipt.result_reference == 'original:1'
    db.session.rollback()
    with pytest.raises(SubmissionConflict):
        claim(token, uid, bid, operation, {'quantity': 2})
    db.session.rollback()


def test_incomplete_receipt_has_distinct_classification(client):
    from app.transaction_submissions import SubmissionIncomplete, payload_hash
    uid, bid = context(client)
    token = issue_token(uid, bid, 'expense')
    binding = validate_token(token, uid, bid, 'expense')
    # Synthetic pre-existing incomplete receipt, not a normal application commit.
    db.session.add(TransactionSubmission(**binding, payload_hash=payload_hash({'amount': '1'})))
    db.session.commit()
    with pytest.raises(SubmissionIncomplete):
        claim(token, uid, bid, 'expense', {'amount': '1'})
    db.session.rollback()
    with pytest.raises(SubmissionConflict) as caught:
        claim(token, uid, bid, 'expense', {'amount': '2'})
    assert not isinstance(caught.value, SubmissionIncomplete)
    db.session.rollback()


@pytest.mark.parametrize('operation,path,module', [
    ('sale', '/sales/', 'app.sales.routes'),
    ('restock', '/restocking/receive', 'app.restocking.routes'),
    ('expense', '/expenses/', 'app.expenses.routes'),
])
def test_incomplete_claim_is_not_reported_as_changed_payload(client, operation, path, module):
    from app.transaction_submissions import SubmissionIncomplete
    from app.models import Sale, Restock, Expense, StockMovement
    uid, bid = context(client)
    pid = add(client, 'Synthetic product', 10)
    data = dict(submission_token=issue_token(uid, bid, operation),
                product_id=str(pid), quantity='1', unit_price='1.00', unit_cost='1.00',
                description='Synthetic expense', amount='1.00', category='Transportation')
    with patch(module + '.claim', side_effect=SubmissionIncomplete('Incomplete receipt')):
        response = client.post(path, data=data)
    assert response.status_code == 409
    assert b'no confirmed completed result' in response.data
    assert b'already used with different contents' not in response.data
    assert Sale.query.count() == Restock.query.count() == Expense.query.count() == 0
    assert TransactionSubmission.query.count() == 0
    assert StockMovement.query.filter(StockMovement.kind != 'opening').count() == 0
    assert db.session.get(Product, pid).stock_quantity == 10
