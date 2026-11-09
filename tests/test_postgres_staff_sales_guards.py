"""Offline contracts only: no app import or database connection."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from tests.support.postgres_staff_guard import Rejected,SALES_CASES,V18
from tests.support import postgres_staff_sales as sales
from tests.support.postgres_staff_sales_races import sales_paths


@pytest.mark.parametrize('variant', ['same','owner-sale','owner-restock','revoke'])
def test_sales_race_paths_are_fixed_to_exact_subjects(variant):
    paths=sales_paths(variant,7,9)
    assert len(paths)==2 and all(isinstance(p,str) and not '?' in p for p in paths)
    assert '/staff/memberships/7/businesses/9/sales' in paths


@pytest.mark.parametrize('variant,member,business', [('foreign',1,2),('same',0,2),
    ('same',1,-1),('same',True,2),('same','1',2),('same',1,True),('../sales',1,2)])
def test_arbitrary_endpoints_and_invalid_identity_are_rejected(variant,member,business):
    with pytest.raises(ValueError):sales_paths(variant,member,business)


class Runtime:
    def __init__(self,auth,rows=None):
        self.auth=auth;self.rows={'user':[]} if rows is None else rows;self.calls=[]
        self.guards=SimpleNamespace(check_clean=lambda:self.calls.append('guard'))
    def check_source(self):self.calls.append('source')
    def preflight(self,phase):self.calls.append(('preflight',phase))
    def snapshot(self):self.calls.append('snapshot');return deepcopy(self.rows)
    def app(self):raise AssertionError('Read-only inspection imported application')


def auth(mode='sales-state'):
    return dict(mode=mode,cases=[] if mode=='sales-state' else list(SALES_CASES),
                continuation_namespace='sales-cont-1234abcd',sales_revision='staff-sales-pg-v1')


def test_state_inventory_never_constructs_application_and_prints_only_digest_metadata():
    rt=Runtime(auth());result=sales.inspect(rt)
    assert result['schema']==V18 and result['namespace_unused'] and result['rows']==0
    assert len(result['starting_state_sha256'])==64 and result['cases']==list(SALES_CASES)
    assert set(result)=={'schema','starting_state_sha256','continuation_namespace','namespace_unused','rows','cases'}


@pytest.mark.parametrize('value',['',None,'sales-cont-1','sales-cont-ABCDEF12','../../escape'])
def test_invalid_namespace_is_rejected_before_preflight(value):
    data=auth();data['continuation_namespace']=value;rt=Runtime(data)
    with pytest.raises(Rejected):sales.inspect(rt)
    assert not rt.calls


@pytest.mark.parametrize('mode',['manager','preflight','sales','unknown'])
def test_inventory_rejects_wrong_stage_before_any_preflight(mode):
    rt=Runtime(auth(mode))
    with pytest.raises(Rejected):sales.inspect(rt)
    assert not rt.calls


def test_reused_namespace_is_not_silently_retried():
    rt=Runtime(auth(),{'user':[{'id':1,'email':'sales-cont-1234abcd-sls01-same-owner@example.invalid'}]})
    with pytest.raises(Rejected,match='namespace-used'):sales.inspect(rt)


def states():
    label='sales-cont-1234abcd-sls01-same'
    before=dict(user=[dict(id=1,email='existing@example.invalid')],business=[],product=[],sale=[])
    after=deepcopy(before)
    after['user'] += [dict(id=2,email=label+'-owner@example.invalid'),dict(id=3,email=label+'-staff@example.invalid')]
    after['business']=[dict(id=10,user_id=2)]
    return before,after,label


def test_new_synthetic_scope_preserves_every_prior_record():
    before,after,label=states();sales.require_preserved(before,after,label)


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
    with pytest.raises(Rejected):sales.require_preserved(before,after,label)


def test_failed_seed_may_leave_no_new_records_but_cannot_change_existing():
    before,_,label=states();sales.require_preserved(before,deepcopy(before),label,allow_empty=True)
    changed=deepcopy(before);changed['user'][0]['email']='bad'
    with pytest.raises(Rejected):sales.require_preserved(before,changed,label,allow_empty=True)


def test_batch_rejects_changed_starting_state_before_application_or_race():
    data=auth('sales');data['starting_state_sha256']='0'*64;rt=Runtime(data)
    with patch.object(sales,'race',side_effect=AssertionError('race must not start')):
        with pytest.raises(Rejected,match='starting-state'):sales.run(rt,lambda *args:None)


@pytest.mark.parametrize('cases',[[],['SLS01'],['SLS03','SLS02','SLS01'],['SLS01','SLS02','foreign']])
def test_batch_requires_fixed_order_and_exact_three_families(cases):
    data=auth('sales');data['cases']=cases;rt=Runtime(data)
    with pytest.raises(Rejected):sales.run(rt,lambda *args:None)
    assert not rt.calls


def test_failure_stops_without_next_variant_cleanup_or_retry():
    data=auth('sales');rt=Runtime(data);data['starting_state_sha256']=sales.digest(rt.rows)
    attempts=[]
    def fail(rt,label,variant):attempts.append(variant);raise RuntimeError('synthetic failure')
    with patch.object(sales,'race',side_effect=fail):
        with pytest.raises(RuntimeError):sales.run(rt,lambda *args:None)
    assert attempts==['same']


@pytest.mark.parametrize('variant',['same','owner-sale','owner-restock','revoke'])
@pytest.mark.parametrize('fault',[None,'same-backend','observer-reused','no-blocking'])
def test_race_protocol_requires_independent_backends_and_observed_blocking(monkeypatch,variant,fault):
    import threading
    from tests.support import postgres_staff_sales_races as races
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
        return races.concurrent_sales_posts(object(),[Client(10,303),Client(10 if fault=='same-backend' else 20,200)],
            sales_paths(variant,7,9),[{},{}],Observer(),owner_id=5,membership_id=7,
            business_id=9,variant=variant,timeout=1.5,evidence=evidence)
    if fault:
        with pytest.raises(AssertionError):run()
    else:
        result=run()
        assert result==[303,200] and set(evidence['workers'])=={10,20} and evidence['observer']==30
    assert not hooks and not any(t.name.startswith('stockbridge-race-') for t in threading.enumerate())


def test_arbitrary_race_endpoint_is_rejected_before_hooks_or_workers():
    from tests.support.postgres_staff_sales_races import concurrent_sales_posts
    with pytest.raises(ValueError):
        concurrent_sales_posts(None,[None,None],['/admin','/sales/'],[{},{}],None,
            owner_id=5,membership_id=7,business_id=9,variant='same')


@pytest.mark.parametrize('mode',['sales-state','sales'])
@pytest.mark.parametrize('field,value',[
    ('sales_revision',None),('sales_revision','old'),
    ('continuation_namespace',None),('continuation_namespace','../foreign'),
    ('cases',['SLS01'])])
def test_sales_authorization_rejects_incomplete_or_changed_review(tmp_path,mode,field,value):
    from tests.test_postgres_staff_guards import approval
    from tests.support import postgres_staff_guard as guard
    data,files=approval(tmp_path,mode);data[field]=value
    with pytest.raises(Rejected):guard.authorize(data,mode,tmp_path,files,source_commit='b'*40)


@pytest.mark.parametrize('variant',['same','owner-sale','owner-restock','revoke'])
@pytest.mark.parametrize('damage',[None,'stock','receipt','actor','line'])
def test_race_results_require_atomic_ledger_and_actor(variant,damage):
    from contextlib import contextmanager
    data=dict(shop=1,product=2,staff=3,owner=4,member=5)
    sale=9;revoked=variant=='revoke';restock=variant=='owner-restock'
    class Connection:
        def execute(self,sql,args):
            if 'FROM public.sale WHERE' in sql:value=[] if revoked else [(sale,)]
            elif 'FROM public.product' in sql:value=[(99 if damage=='stock' else (1 if revoked or restock else 0),)]
            elif 'FROM public.transaction_submission' in sql:
                value=[] if revoked else [(3,'sale','sale:9',None if damage=='receipt' else 'done')]
                if restock:value.append((4,'restock','batch', 'done'))
            elif 'FROM public.audit_log' in sql:value=[] if revoked else [(99 if damage=='actor' else 3,sale)]
            elif 'FROM public.stock_movement' in sql:
                value=[] if revoked else [('sale',-1)]+([('restock',1)] if restock else [])
            elif 'FROM public.staff_membership' in sql:value=[('revoked' if revoked else 'active',)]
            elif 'FROM public.sale_item' in sql:value=[(2,1,99 if damage=='line' else 20,8)]
            else:raise AssertionError('Unexpected result query')
            return SimpleNamespace(fetchall=lambda:value,fetchone=lambda:value[0])
    @contextmanager
    def connection():yield Connection()
    rt=SimpleNamespace(connection=connection)
    statuses={'same':[303,200],'owner-sale':[303,200],'owner-restock':[303,302],'revoke':[302,403]}[variant]
    if damage and (not revoked or damage=='stock'):
        with pytest.raises(Rejected):sales.assert_result(rt,data,variant,statuses)
    else:sales.assert_result(rt,data,variant,statuses)
