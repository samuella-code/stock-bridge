"""Offline contracts only: no app import or database connection."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.support.postgres_staff_guard import Rejected,STOCK_CASES,V18
from tests.support import postgres_staff_stock as stock
from tests.support.postgres_staff_stock_races import stock_paths


@pytest.mark.parametrize('variant', ['same','owner-sale','owner-restock','revoke'])
def test_stock_race_paths_are_fixed_to_exact_subjects(variant):
    paths=stock_paths(variant,7,9)
    assert len(paths)==2 and all(isinstance(p,str) and not '?' in p for p in paths)
    assert '/staff/memberships/7/businesses/9/restocking' in paths


@pytest.mark.parametrize('variant,member,business', [('foreign',1,2),('same',0,2),
    ('same',1,-1),('same',True,2),('same','1',2),('same',1,True),('../stock',1,2)])
def test_arbitrary_endpoints_and_invalid_identity_are_rejected(variant,member,business):
    with pytest.raises(ValueError):stock_paths(variant,member,business)


class Runtime:
    def __init__(self,auth,rows=None):
        self.auth=auth;self.rows={'user':[]} if rows is None else rows;self.calls=[]
        self.guards=SimpleNamespace(check_clean=lambda:self.calls.append('guard'))
    def check_source(self):self.calls.append('source')
    def preflight(self,phase):self.calls.append(('preflight',phase))
    def snapshot(self):self.calls.append('snapshot');return deepcopy(self.rows)
    def app(self):raise AssertionError('Read-only inspection imported application')


def auth(mode='stock-state'):
    return dict(mode=mode,cases=[] if mode=='stock-state' else list(STOCK_CASES),
                continuation_namespace='stock-cont-1234abcd',stock_revision='staff-stock-pg-v1')


def test_state_inventory_never_constructs_application_and_prints_only_digest_metadata():
    rt=Runtime(auth());result=stock.inspect(rt)
    assert result['schema']==V18 and result['namespace_unused'] and result['rows']==0
    assert len(result['starting_state_sha256'])==64 and result['cases']==list(STOCK_CASES)
    assert set(result)=={'schema','starting_state_sha256','continuation_namespace','namespace_unused','rows','cases'}


@pytest.mark.parametrize('value',['',None,'stock-cont-1','stock-cont-ABCDEF12','../../escape'])
def test_invalid_namespace_is_rejected_before_preflight(value):
    data=auth();data['continuation_namespace']=value;rt=Runtime(data)
    with pytest.raises(Rejected):stock.inspect(rt)
    assert not rt.calls


@pytest.mark.parametrize('mode',['manager','preflight','stock','unknown'])
def test_inventory_rejects_wrong_stage_before_any_preflight(mode):
    rt=Runtime(auth(mode))
    with pytest.raises(Rejected):stock.inspect(rt)
    assert not rt.calls


def test_reused_namespace_is_not_silently_retried():
    rt=Runtime(auth(),{'user':[{'id':1,'email':'stock-cont-1234abcd-stk01-same-owner@example.invalid'}]})
    with pytest.raises(Rejected,match='namespace-used'):stock.inspect(rt)


def states():
    label='stock-cont-1234abcd-stk01-same'
    before=dict(user=[dict(id=1,email='existing@example.invalid')],business=[],product=[],sale=[])
    after=deepcopy(before)
    after['user'] += [dict(id=2,email=label+'-owner@example.invalid'),dict(id=3,email=label+'-staff@example.invalid')]
    after['business']=[dict(id=10,user_id=2)]
    return before,after,label


def test_new_synthetic_scope_preserves_every_prior_record():
    before,after,label=states();stock.require_preserved(before,after,label)


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
    with pytest.raises(Rejected):stock.require_preserved(before,after,label)


def test_failed_seed_may_leave_no_new_records_but_cannot_change_existing():
    before,_,label=states();stock.require_preserved(before,deepcopy(before),label,allow_empty=True)
    changed=deepcopy(before);changed['user'][0]['email']='bad'
    with pytest.raises(Rejected):stock.require_preserved(before,changed,label,allow_empty=True)


def test_batch_rejects_changed_starting_state_before_application_or_race():
    data=auth('stock');data['starting_state_sha256']='0'*64;rt=Runtime(data)
    with patch.object(stock,'race',side_effect=AssertionError('race must not start')):
        with pytest.raises(Rejected,match='starting-state'):stock.run(rt,lambda *args:None)


@pytest.mark.parametrize('cases',[[],['STK01'],['STK03','STK02','STK01'],['STK01','STK02','foreign']])
def test_batch_requires_fixed_order_and_exact_three_families(cases):
    data=auth('stock');data['cases']=cases;rt=Runtime(data)
    with pytest.raises(Rejected):stock.run(rt,lambda *args:None)
    assert not rt.calls


def test_failure_stops_without_next_variant_cleanup_or_retry():
    data=auth('stock');rt=Runtime(data);data['starting_state_sha256']=stock.digest(rt.rows)
    attempts=[]
    def fail(rt,label,variant):attempts.append(variant);raise RuntimeError('synthetic failure')
    with patch.object(stock,'race',side_effect=fail):
        with pytest.raises(RuntimeError):stock.run(rt,lambda *args:None)
    assert attempts==['same']


@pytest.mark.parametrize('variant',['same','owner-sale','owner-restock','revoke'])
@pytest.mark.parametrize('fault',[None,'same-backend','observer-reused','no-blocking'])
def test_race_protocol_requires_independent_backends_and_observed_blocking(monkeypatch,variant,fault):
    import threading
    from tests.support import postgres_staff_stock_races as races
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
        return races.concurrent_stock_posts(object(),[Client(10,303),Client(10 if fault=='same-backend' else 20,200)],
            stock_paths(variant,7,9),[{},{}],Observer(),owner_id=5,membership_id=7,
            business_id=9,variant=variant,timeout=1.5,evidence=evidence)
    if fault:
        with pytest.raises(AssertionError):run()
    else:
        result=run()
        assert result==[303,200] and set(evidence['workers'])=={10,20} and evidence['observer']==30
    assert not hooks and not any(t.name.startswith('stockbridge-race-') for t in threading.enumerate())


def test_arbitrary_race_endpoint_is_rejected_before_hooks_or_workers():
    from tests.support.postgres_staff_stock_races import concurrent_stock_posts
    with pytest.raises(ValueError):
        concurrent_stock_posts(None,[None,None],['/admin','/stock/'],[{},{}],None,
            owner_id=5,membership_id=7,business_id=9,variant='same')


@pytest.mark.parametrize('mode',['stock-state','stock'])
@pytest.mark.parametrize('field,value',[
    ('stock_revision',None),('stock_revision','old'),
    ('continuation_namespace',None),('continuation_namespace','../foreign'),
    ('cases',['STK01'])])
def test_stock_authorization_rejects_incomplete_or_changed_review(tmp_path,mode,field,value):
    from tests.test_postgres_staff_guards import approval
    from tests.support import postgres_staff_guard as guard
    data,files=approval(tmp_path,mode);data[field]=value
    with pytest.raises(Rejected):guard.authorize(data,mode,tmp_path,files,source_commit='b'*40)




def result_rows(variant):
    from decimal import Decimal
    revoked=variant=='revoke';owner_restock=variant=='owner-restock';owner_sale=variant=='owner-sale'
    rests=[] if revoked else [(10,2,Decimal('12.50'),'staff-batch')]
    if owner_restock:rests.append((11,1,Decimal('8.00'),'owner-batch'))
    receipts=[] if revoked else [(3,'restock','staff-batch','done')]
    if owner_restock:receipts.append((4,'restock','owner-batch','done'))
    if owner_sale:receipts.append((4,'sale','sale:20','done'))
    audits=[] if revoked else [(3,)]+([(4,)] if owner_restock else [])
    moves=[] if revoked else [('restock',2)]+([('restock',1)] if owner_restock else [('sale',-1)] if owner_sale else [])
    return dict(rests=rests,stock=(1 if revoked else 4 if owner_restock else 2 if owner_sale else 3,
                                 Decimal('8.00') if revoked or owner_restock else Decimal('12.50')),
                receipts=receipts,audits=audits,moves=moves,sales=[(20,)] if owner_sale else [],
                member=[('revoked' if revoked else 'active',)],items=[(2,1,20,Decimal('12.50'))],sale_audit=[(4,20)])


def check_result(variant,rows):
    from contextlib import contextmanager
    class Connection:
        def execute(self,sql,args):
            if 'FROM public.restock' in sql:value=rows['rests']
            elif 'FROM public.product' in sql:value=[rows['stock']]
            elif 'FROM public.transaction_submission' in sql:value=rows['receipts']
            elif 'FROM public.audit_log' in sql:value=rows['sale_audit'] if 'SALE_CREATED' in sql else rows['audits']
            elif 'FROM public.stock_movement' in sql:value=rows['moves']
            elif 'FROM public.sale WHERE' in sql:value=rows['sales']
            elif 'FROM public.staff_membership' in sql:value=rows['member']
            elif 'FROM public.sale_item' in sql:value=rows['items']
            else:raise AssertionError('Unexpected result query')
            return SimpleNamespace(fetchall=lambda:value,fetchone=lambda:value[0])
    @contextmanager
    def connection():yield Connection()
    statuses={'same':[303,200],'owner-sale':[303,302],'owner-restock':[303,302],'revoke':[302,403]}[variant]
    stock.assert_result(SimpleNamespace(connection=connection),dict(shop=1,product=2,staff=3,owner=4,member=5),variant,statuses)


@pytest.mark.parametrize('variant',['same','owner-sale','owner-restock','revoke'])
def test_restock_race_requires_expected_complete_ledger(variant):
    check_result(variant,result_rows(variant))


@pytest.mark.parametrize('variant',['same','owner-sale','owner-restock'])
@pytest.mark.parametrize('damage',['stock','cost','receipt','completion','actor','line','movement','result-binding'])
def test_incomplete_or_incorrect_restock_results_never_pass(variant,damage):
    rows=result_rows(variant)
    if damage=='stock':rows['stock']=(999,rows['stock'][1])
    if damage=='cost':rows['stock']=(rows['stock'][0],999)
    if damage=='receipt':rows['receipts'].pop()
    if damage=='completion':rows['receipts'][0]=(3,'restock','staff-batch',None)
    if damage=='actor':rows['audits'][0]=(99,)
    if damage=='line':rows['rests'][0]=(10,2,999,'staff-batch')
    if damage=='movement':rows['moves'].append(('restock',2))
    if damage=='result-binding':rows['receipts'][0]=(3,'restock','foreign-batch','done')
    with pytest.raises(Rejected):check_result(variant,rows)


@pytest.mark.parametrize('damage',['stock','cost','membership','restock','sale','movement','receipt'])
def test_revoked_employee_must_leave_no_operational_mutations(damage):
    rows=result_rows('revoke')
    if damage=='stock':rows['stock']=(2,8)
    if damage=='cost':rows['stock']=(1,12.5)
    if damage=='membership':rows['member']=[('active',)]
    if damage=='restock':rows['rests']=[(10,1,8,'batch')]
    if damage=='sale':rows['sales']=[(20,)]
    if damage=='movement':rows['moves']=[('restock',1)]
    if damage=='receipt':rows['receipts']=[(3,'restock','batch','done')]
    with pytest.raises(Rejected):check_result('revoke',rows)


@pytest.mark.parametrize('damage',['cost','sale-actor','sale-receipt'])
def test_competing_owner_sale_uses_new_cost_and_actual_owner_actor(damage):
    rows=result_rows('owner-sale')
    if damage=='cost':rows['items']=[(2,1,20,8)]
    if damage=='sale-actor':rows['sale_audit']=[(3,20)]
    if damage=='sale-receipt':rows['receipts'][1]=(3,'sale','sale:20','done')
    with pytest.raises(Rejected):check_result('owner-sale',rows)


@pytest.mark.parametrize('damage',['shared-batch','owner-receipt'])
def test_competing_owner_restock_keeps_independent_receipt_identity(damage):
    rows=result_rows('owner-restock')
    if damage=='shared-batch':rows['rests'][1]=(11,1,8,'staff-batch')
    if damage=='owner-receipt':rows['receipts'][1]=(3,'restock','owner-batch','done')
    with pytest.raises(Rejected):check_result('owner-restock',rows)
