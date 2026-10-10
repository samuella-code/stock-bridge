from unittest.mock import patch
from contextlib import nullcontext
import pytest
from app import db
from app.models import Product, StockMovement, Expense, AuditLog, Notification, EmailOutbox, TransactionSubmission
from tests.test_business_workflows import client, owner, add
from tests.operation_submission_helpers import with_operation_token


def setup(client, operation, direction='decrease'):
    owner(client)
    pid = add(client, 'Water', 10)
    path = f'/products/{pid}/adjust' if operation == 'adjustment' else '/expenses/'
    data = dict(quantity='5', direction=direction, reason='Damaged', date='', note='Test note') if operation == 'adjustment' else dict(description='Transport', amount='50.00', category='Transportation', spent_at='', note='Test note')
    return pid, path, with_operation_token(client, path, data)


@pytest.mark.parametrize('operation,direction', [('adjustment','increase'), ('adjustment','decrease'), ('expense','decrease')])
def test_replay_and_distinct_tokens(client, operation, direction):
    pid, path, data = setup(client, operation, direction)
    assert client.post(path, data=data).status_code == 302
    if operation == 'adjustment' and direction == 'decrease':
        assert Notification.query.filter_by(kind='low_stock').count() == 1
        assert EmailOutbox.query.filter_by(event_type='low_stock').count() == 1
    counts = (Notification.query.count(), EmailOutbox.query.count(), AuditLog.query.count())
    assert client.post(path, data=data).status_code == 302
    assert TransactionSubmission.query.count() == 1
    assert (Notification.query.count(), EmailOutbox.query.count(), AuditLog.query.count()) == counts
    if operation == 'adjustment':
        assert db.session.get(Product,pid).stock_quantity == (15 if direction == 'increase' else 5)
        assert StockMovement.query.filter_by(kind='adjustment').count() == 1
    else:
        assert Expense.query.count() == 1
        assert str(Expense.query.one().amount) == '50.00'
        assert b'50.00' in client.get('/expenses/').data
    changed = dict(data, note='Different')
    assert client.post(path, data=changed).status_code == 409
    data = with_operation_token(client, path, data)
    assert client.post(path, data=data).status_code == 302
    assert TransactionSubmission.query.count() == 2


@pytest.mark.parametrize('operation', ['adjustment','expense'])
def test_validation_and_atomic_rollback(client, operation, monkeypatch):
    pid,path,data=setup(client,operation)
    bad=dict(data, **({'quantity':'0'} if operation=='adjustment' else {'amount':'0'}))
    response=client.post(path,data=bad)
    assert response.status_code == 200 and data['submission_token'].encode() in response.data and b'Test note' in response.data
    assert TransactionSubmission.query.count()==0
    session=db.session()
    with monkeypatch.context() as m:
        def fail(): raise RuntimeError('Synthetic commit failure')
        m.setattr(session,'commit',fail)
        with pytest.raises(RuntimeError):client.post(path,data=data)
    assert TransactionSubmission.query.count()==Expense.query.count()==0
    assert StockMovement.query.filter_by(kind='adjustment').count()==0
    assert db.session.get(Product,pid).stock_quantity==10
    assert client.post(path,data=data).status_code==302


@pytest.mark.parametrize('operation', ['adjustment','expense'])
@pytest.mark.parametrize('kind',['missing','tampered','expired','wrong_operation'])
def test_invalid_tokens(client,operation,kind):
    pid,path,data=setup(client,operation)
    if kind=='missing':data['submission_token']=''
    if kind=='tampered':data['submission_token']+='x'
    if kind=='wrong_operation':
        data['submission_token']=with_operation_token(client,'/expenses/' if operation=='adjustment' else f'/products/{pid}/adjust',{})['submission_token']
    context=patch('itsdangerous.timed.time.time',return_value=9999999999) if kind=='expired' else nullcontext()
    with context:assert client.post(path,data=data).status_code==400
    assert TransactionSubmission.query.count()==Expense.query.count()==0
    assert db.session.get(Product,pid).stock_quantity==10

@pytest.mark.parametrize('operation', ['adjustment','expense'])
def test_signed_cross_user_and_business_tokens_rejected(client, operation):
    pid,path,data=setup(client,operation)
    from app.transaction_submissions import signer
    original=signer().loads(data['submission_token'])
    for field in ['user_id','business_id']:
        forged=dict(original);forged[field]+=100
        assert client.post(path,data=dict(data,submission_token=signer().dumps(forged))).status_code==400
    assert TransactionSubmission.query.count()==Expense.query.count()==0
    assert db.session.get(Product,pid).stock_quantity==10

@pytest.mark.parametrize('operation', ['adjustment','expense'])
def test_csrf_and_expired_entitlement_still_block(client, operation):
    pid,path,data=setup(client,operation)
    client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post(path,data=data).status_code==400
    client.application.config['WTF_CSRF_ENABLED']=False
    from app.models import Business
    business=Business.query.one();business.subscription_plan="basic";business.subscription_status="expired"
    db.session.commit()
    response=client.post(path,data=data)
    assert response.status_code in (302,403)
    assert TransactionSubmission.query.count()==Expense.query.count()==0
    assert db.session.get(Product,pid).stock_quantity==10


def test_adjustment_replay_after_stock_changes_and_negative_stock_guard(client):
    pid,path,data=setup(client,'adjustment')
    client.post(path,data=data)
    product=db.session.get(Product,pid);product.stock_quantity=1;db.session.commit()
    assert client.post(path,data=data).status_code==302
    assert db.session.get(Product,pid).stock_quantity==1
    retry=with_operation_token(client,path,data)
    assert client.post(path,data=retry).status_code==200
    assert TransactionSubmission.query.count()==1
    assert db.session.get(Product,pid).stock_quantity==1

@pytest.mark.parametrize('operation,field,value', [
    ('adjustment','quantity','4'), ('adjustment','direction','increase'),
    ('adjustment','reason','Returned'), ('adjustment','date','2026-01-01'),
    ('expense','amount','60'), ('expense','category','Rent'),
    ('expense','description','Other expense'), ('expense','spent_at','2026-01-01')])
def test_all_transaction_details_bound_to_intent(client,operation,field,value):
    pid,path,data=setup(client,operation)
    client.post(path,data=data)
    assert client.post(path,data=dict(data,**{field:value})).status_code==409
    assert TransactionSubmission.query.count()==1


def test_equivalent_amount_and_quantity_spellings_replay(client):
    pid,path,data=setup(client,'expense')
    client.post(path,data=data)
    assert client.post(path,data=dict(data,amount='5E1')).status_code==302
    assert Expense.query.count()==1
    path=f'/products/{pid}/adjust'
    adjustment=with_operation_token(client,path,dict(quantity='2',direction='increase',reason='Returned'))
    client.post(path,data=adjustment)
    assert client.post(path,data=dict(adjustment,quantity='02')).status_code==302
    assert db.session.get(Product,pid).stock_quantity==12
    assert StockMovement.query.filter_by(kind='adjustment').count()==1
