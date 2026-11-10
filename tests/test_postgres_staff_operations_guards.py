"""Offline contracts only: no app import or database connection."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.support.postgres_staff_guard import Rejected,OPERATIONS_CASES,V18
from tests.support import postgres_staff_operations as operations
from tests.support.postgres_staff_operations_races import operations_paths


@pytest.mark.parametrize('variant', ['create','edit','stale','expense','revoke-product','revoke-expense'])
def test_operations_race_paths_are_fixed_to_exact_subjects(variant):
    paths=operations_paths(variant,7,9,11)
    assert len(paths)==2 and all(isinstance(p,str) and not '?' in p for p in paths)
    assert any(p.startswith('/staff/memberships/7/businesses/9/') for p in paths)


@pytest.mark.parametrize('variant,member,business', [('foreign',1,2),('create',0,2),
    ('create',1,-1),('create',True,2),('create','1',2),('create',1,True),('../operations',1,2)])
def test_arbitrary_endpoints_and_invalid_identity_are_rejected(variant,member,business):
    with pytest.raises(ValueError):operations_paths(variant,member,business,11)


class Runtime:
    def __init__(self,auth,rows=None):
        self.auth=auth;self.rows={'user':[]} if rows is None else rows;self.calls=[]
        self.guards=SimpleNamespace(check_clean=lambda:self.calls.append('guard'))
    def check_source(self):self.calls.append('source')
    def preflight(self,phase):self.calls.append(('preflight',phase))
    def snapshot(self):self.calls.append('snapshot');return deepcopy(self.rows)
    def app(self):raise AssertionError('Read-only inspection imported application')


def auth(mode='operations-state'):
    return dict(mode=mode,cases=[] if mode=='operations-state' else list(OPERATIONS_CASES),
                continuation_namespace='ops-cont-1234abcd',operations_revision='staff-operations-pg-v1')


def test_state_inventory_never_constructs_application_and_prints_only_digest_metadata():
    rt=Runtime(auth());result=operations.inspect(rt)
    assert result['schema']==V18 and result['namespace_unused'] and result['rows']==0
    assert len(result['starting_state_sha256'])==64 and result['cases']==list(OPERATIONS_CASES)
    assert set(result)=={'schema','starting_state_sha256','continuation_namespace','namespace_unused','rows','cases'}


@pytest.mark.parametrize('value',['',None,'ops-cont-1','ops-cont-ABCDEF12','../../escape'])
def test_invalid_namespace_is_rejected_before_preflight(value):
    data=auth();data['continuation_namespace']=value;rt=Runtime(data)
    with pytest.raises(Rejected):operations.inspect(rt)
    assert not rt.calls


@pytest.mark.parametrize('mode',['manager','preflight','operations','unknown'])
def test_inventory_rejects_wrong_stage_before_any_preflight(mode):
    rt=Runtime(auth(mode))
    with pytest.raises(Rejected):operations.inspect(rt)
    assert not rt.calls


def test_reused_namespace_is_not_silently_retried():
    rt=Runtime(auth(),{'user':[{'id':1,'email':'ops-cont-1234abcd-stk01-same-owner@example.invalid'}]})
    with pytest.raises(Rejected,match='namespace-used'):operations.inspect(rt)


def states():
    label='ops-cont-1234abcd-stk01-same'
    before=dict(user=[dict(id=1,email='existing@example.invalid')],business=[],product=[],sale=[])
    after=deepcopy(before)
    after['user'] += [dict(id=2,email=label+'-owner@example.invalid'),dict(id=3,email=label+'-staff@example.invalid')]
    after['business']=[dict(id=10,user_id=2,name=label+' shop')]
    return before,after,label


def test_new_synthetic_scope_preserves_every_prior_record():
    before,after,label=states();operations.require_preserved(before,after,label)


@pytest.mark.parametrize('damage',['old-change','old-delete','foreign-business','foreign-product','table','foreign-user','duplicate-old'])
def test_preservation_rejects_old_mutations_and_new_foreign_records(damage):
    before,after,label=states()
    if damage=='old-change':after['user'][0]['email']='changed@example.invalid'
    if damage=='old-delete':after['user'].pop(0)
    if damage=='foreign-business':after['business'][0]['user_id']=1
    if damage=='foreign-product':after['product']=[dict(id=5,business_id=99)]
    if damage=='table':after['unexpected']=[]
    if damage=='foreign-user':after['user'].append(dict(id=9,email='foreign@example.invalid'))
    if damage=='duplicate-old':after['user'].append(dict(before['user'][0]))
    with pytest.raises(Rejected):operations.require_preserved(before,after,label)


def test_failed_seed_may_leave_no_new_records_but_cannot_change_existing():
    before,_,label=states();operations.require_preserved(before,deepcopy(before),label,allow_empty=True)
    changed=deepcopy(before);changed['user'][0]['email']='bad'
    with pytest.raises(Rejected):operations.require_preserved(before,changed,label,allow_empty=True)


def test_batch_rejects_changed_starting_state_before_application_or_race():
    data=auth('operations');data['starting_state_sha256']='0'*64;rt=Runtime(data)
    with patch.object(operations,'race',side_effect=AssertionError('race must not start')):
        with pytest.raises(Rejected,match='starting-state'):operations.run(rt,lambda *args:None)


@pytest.mark.parametrize('cases',[[],['OPS01'],['OPS04','OPS03','OPS02','OPS01'],['OPS01','OPS02','foreign']])
def test_batch_requires_fixed_order_and_exact_three_families(cases):
    data=auth('operations');data['cases']=cases;rt=Runtime(data)
    with pytest.raises(Rejected):operations.run(rt,lambda *args:None)
    assert not rt.calls


def test_failure_stops_without_next_variant_cleanup_or_retry():
    data=auth('operations');rt=Runtime(data);data['starting_state_sha256']=operations.digest(rt.rows)
    attempts=[]
    def fail(rt,label,variant):attempts.append(variant);raise RuntimeError('synthetic failure')
    with patch.object(operations,'race',side_effect=fail):
        with pytest.raises(RuntimeError):operations.run(rt,lambda *args:None)
    assert attempts==['create']


@pytest.mark.parametrize('variant',['create','edit','stale','expense','revoke-product','revoke-expense'])
@pytest.mark.parametrize('fault',[None,'same-backend','observer-reused','no-blocking'])
def test_race_protocol_requires_independent_backends_and_observed_blocking(monkeypatch,variant,fault):
    import threading
    from tests.support import postgres_staff_operations_races as races
    hooks={};lock=threading.Lock()
    monkeypatch.setattr(races.event,'listen',lambda engine,name,fn:hooks.__setitem__(name,fn))
    monkeypatch.setattr(races.event,'remove',lambda engine,name,fn:hooks.pop(name))
    class Client:
        def __init__(self,pid,status):self.pid=pid;self.status=status
        def post(self,path,data):
            driver=SimpleNamespace(info=SimpleNamespace(backend_pid=self.pid))
            conn=SimpleNamespace(connection=SimpleNamespace(driver_connection=driver))
            sql='UPDATE "user" SET last_activity_at="user".last_activity_at WHERE "user".id = %(id)s'
            hooks['before_cursor_execute'](conn,None,sql,{'id':5},None,False)
            with lock:hooks['after_cursor_execute'](conn,None,sql,{'id':5},None,False)
            return SimpleNamespace(status_code=self.status)
    class Observer:
        info=SimpleNamespace(backend_pid=10 if fault=='observer-reused' else 30)
        def execute(self,sql,values):
            assert sql=='SELECT pg_blocking_pids(%s)' and values==(20,)
            return SimpleNamespace(fetchone=lambda:([] if fault=='no-blocking' else [10],))
    evidence={}
    def run():
        return races.concurrent_operations_posts(object(),[Client(10,303),Client(10 if fault=='same-backend' else 20,200)],
            operations_paths(variant,7,9,11),[{},{}],Observer(),owner_id=5,membership_id=7,
            business_id=9,product_id=11,variant=variant,timeout=1.5,evidence=evidence)
    if fault:
        with pytest.raises(AssertionError):run()
    else:
        result=run()
        assert result==[303,200] and set(evidence['workers'])=={10,20} and evidence['observer']==30
    assert not hooks and not any(t.name.startswith('stockbridge-race-') for t in threading.enumerate())


def test_arbitrary_race_endpoint_is_rejected_before_hooks_or_workers():
    from tests.support.postgres_staff_operations_races import concurrent_operations_posts
    with pytest.raises(ValueError):
        concurrent_operations_posts(None,[None,None],['/admin','/operations/'],[{},{}],None,
            owner_id=5,membership_id=7,business_id=9,product_id=11,variant='create')


@pytest.mark.parametrize('mode',['operations-state','operations'])
@pytest.mark.parametrize('field,value',[
    ('operations_revision',None),('operations_revision','old'),
    ('continuation_namespace',None),('continuation_namespace','../foreign'),
    ('cases',['OPS01'])])
def test_operations_authorization_rejects_incomplete_or_changed_review(tmp_path,mode,field,value):
    from tests.test_postgres_staff_guards import approval
    from tests.support import postgres_staff_guard as guard
    data,files=approval(tmp_path,mode);data[field]=value
    with pytest.raises(Rejected):guard.authorize(data,mode,tmp_path,files,source_commit='b'*40)





def result_ledger(variant):
    from decimal import Decimal as D
    revoked=variant.startswith('revoke-');expense=variant=='expense';create=variant=='create'
    products=[(2,1,1,D('8'),D('30') if variant in ('edit','stale') else D('20'))]
    if create:products.append((8,2,2,D('12.50'),D('30')))
    operation='expense' if expense else 'product_create' if create else 'product_edit'
    action='EXPENSE_CREATED' if expense else 'PRODUCT_CREATED' if create else 'PRODUCT_UPDATED'
    reference='expense:9' if expense else 'product:8' if create else 'product:2'
    return dict(products=products,receipts=[] if revoked else [(3,operation,reference,'done')],
        audits=[] if revoked else [(3,action)],expenses=[(9,D('3'))] if expense else [],
        moves=[('opening',2)] if create else [],sales=[],membership='revoked' if revoked else 'active')


def statuses(variant):return [302,403] if variant.startswith('revoke-') else [303,409] if variant=='stale' else [303,200]


@pytest.mark.parametrize('variant',['create','edit','stale','expense','revoke-product','revoke-expense'])
def test_complete_results_require_exact_ledger_and_current_authority(variant):
    operations.validate_result(dict(product=2,staff=3),variant,statuses(variant),result_ledger(variant))


@pytest.mark.parametrize('variant',['create','edit','stale','expense'])
@pytest.mark.parametrize('damage',['cost','stock','receipt','completion','actor','binding','movement','sale','membership'])
def test_wrong_ledger_cannot_pass(variant,damage):
    rows=result_ledger(variant)
    if damage in ('cost','stock'):
        product=list(rows['products'][0]);product[3 if damage=='cost' else 1]=999;rows['products'][0]=tuple(product)
    if damage=='receipt':rows['receipts']=[]
    if damage=='completion':rows['receipts'][0]=(*rows['receipts'][0][:3],None)
    if damage=='actor':rows['audits'][0]=(99,rows['audits'][0][1])
    if damage=='binding':rows['receipts'][0]=(3,rows['receipts'][0][1],'product:999','done')
    if damage=='movement':rows['moves'].append(('opening',1))
    if damage=='sale':rows['sales']=[(8,)]
    if damage=='membership':rows['membership']='revoked'
    with pytest.raises(Rejected):operations.validate_result(dict(product=2,staff=3),variant,statuses(variant),rows)


@pytest.mark.parametrize('variant',['revoke-product','revoke-expense'])
@pytest.mark.parametrize('damage',['cost','price','receipt','expense','movement','membership'])
def test_revoked_writes_must_leave_no_operational_mutation(variant,damage):
    rows=result_ledger(variant)
    if damage in ('cost','price'):
        row=list(rows['products'][0]);row[3 if damage=='cost' else 4]=999;rows['products'][0]=tuple(row)
    if damage=='receipt':rows['receipts']=[(3,'expense','expense:9','done')]
    if damage=='expense':rows['expenses']=[(9,3)]
    if damage=='movement':rows['moves']=[('opening',1)]
    if damage=='membership':rows['membership']='active'
    with pytest.raises(Rejected):operations.validate_result(dict(product=2,staff=3),variant,statuses(variant),rows)


@pytest.mark.parametrize('damage',[None,'foreign-expense','extra-business','old-expense-change'])
def test_report_scope_allows_only_its_two_new_businesses_and_preserves_old_expenses(damage):
    label='ops-cont-1234abcd-ops04-report'
    before=dict(user=[dict(id=1,email='existing@example.invalid')],business=[],expense=[dict(id=1,business_id=99,amount=4)])
    after=deepcopy(before)
    after['user'] += [dict(id=2,email=label+'-owner@example.invalid'),dict(id=3,email=label+'-staff@example.invalid')]
    after['business']=[dict(id=10,user_id=2,name=label+' shop'),dict(id=11,user_id=2,name=label+' unassigned')]
    after['expense'] += [dict(id=2,business_id=10,amount=3),dict(id=3,business_id=11,amount=999)]
    if damage=='foreign-expense':after['expense'][-1]['business_id']=99
    if damage=='extra-business':after['business'].append(dict(id=12,user_id=2,name='unexpected'))
    if damage=='old-expense-change':after['expense'][0]['amount']=8
    if damage:
        with pytest.raises(Rejected):operations.require_preserved(before,after,label)
    else:operations.require_preserved(before,after,label)
