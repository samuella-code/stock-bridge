"""Only synthetic metadata/clients/connections; no app import or real DB."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from tests.support import postgres_staff_guard as g
from tests.support import postgres_staff_identity as identity
from tests.support import postgres_staff_races as race
from tests.test_ubuntu_postgres_preflight import evidence, snapshot


def env():
    return dict(STOCKBRIDGE_TEST_POSTGRES_ACK='disposable-local-only',
        STOCKBRIDGE_TEST_POSTGRES_MODE='ubuntu-isolated-cluster',
        STOCKBRIDGE_TEST_POSTGRES_UBUNTU_ACK='16/stockbridge-test:55432:loopback-only',
        STOCKBRIDGE_TEST_POSTGRES_RUN_ID=g.RUN,
        STOCKBRIDGE_TEST_POSTGRES_URL=f'postgresql+psycopg://{g.NAME}:synthetic@127.0.0.1:55432/{g.NAME}')


def approval(tmp_path,mode='staff'):
    p=tmp_path/'file.py';p.write_text('synthetic source\n');files={'helper':p}
    before,after,cases=g.MODES[mode]
    data=dict(schema=2,staff_integration_commit=g.INTEGRATION,scenario_revision=g.REVISION,starting_state_sha256='d'*64,approved=True,mode=mode,target=dict(g.TARGET),run_id=g.RUN,
        repository=str(tmp_path),baseline_sha256='c'*64,source_commit='b'*40,application_base_commit=g.BASE,
        schema_before=before,schema_after=after,cases=list(cases),database_oid=1000,role_oid=1001,
        package_provenance_approved=True,isolation_mode='application-guards-only',
        os_outbound_enforcement='unverified',application_isolation_risk_accepted=True,
        files={'helper':g.sha(p)},source_files={'file.py':g.sha(p)},relations={'empty':[],g.V17:['public.user'],g.V18:['public.user','public.staff_role']})
    return data,files


@pytest.mark.parametrize('mode',list(g.MODES))
def test_stage_authorization_matches_only_its_exact_selection(tmp_path,mode):
    auth,files=approval(tmp_path,mode)
    assert g.authorize(auth,mode,tmp_path,files,source_commit='b'*40) is auth
    for field in ('schema_before','schema_after','cases','source_commit','target','approved','mode','run_id'):
        bad=deepcopy(auth);bad.pop(field)
        with pytest.raises(g.Rejected):g.authorize(bad,mode,tmp_path,files,source_commit='b'*40)


@pytest.mark.parametrize('field,value',[
    ('schema',True),('approved','true'),('source_commit',g.BASE),('application_base_commit','0'*40),
    ('package_provenance_approved',False),('application_isolation_risk_accepted',False),
    ('isolation_mode','verified-os'),('os_outbound_enforcement','verified'),
    ('database_oid',True),('role_oid',0),('cases',['R01']),('schema_before',g.V17),
    ('repository','/foreign'),('files',{}),('relations',{}),('network_isolation_approved',True)])
def test_authorization_malformed_unverified_and_obsolete_flags_fail(tmp_path,field,value):
    auth,files=approval(tmp_path);auth[field]=value
    with pytest.raises(g.Rejected):g.authorize(auth,'staff',tmp_path,files,source_commit='b'*40)


def test_changed_helper_hash_fails_before_any_live_work(tmp_path):
    auth,files=approval(tmp_path);files['helper'].write_text('changed')
    with pytest.raises(g.Rejected):g.authorize(auth,'staff',tmp_path,files,source_commit='b'*40)


@pytest.mark.parametrize('key,value',[
    ('STOCKBRIDGE_TEST_POSTGRES_RUN_ID','9ca65feb59b0ca68'),('PGOPTIONS','unsafe'),('VERCEL','1'),
    ('STOCKBRIDGE_TEST_POSTGRES_ACK',''),('STOCKBRIDGE_TEST_POSTGRES_MODE','existing-local-service'),
    *[('STOCKBRIDGE_TEST_POSTGRES_URL',f'postgresql+psycopg://{g.NAME}:synthetic@{host}:{port}/{db}{query}')
      for host,port,db,query in [('localhost',55432,g.NAME,''),('127.0.0.1',5432,g.NAME,''),
        ('127.0.0.1',55432,'production',''),('neon.example',55432,g.NAME,''),
        ('127.0.0.1',55432,g.NAME,'?options=unsafe')]]])
def test_target_and_libpq_overrides_never_reach_connector(key,value):
    data=env();data[key]=value
    with pytest.raises(ValueError):g.target_url(data)


def test_no_authorization_skips_before_app_import_and_fake_ack_fails():
    for case in g.CASES:
        with pytest.raises(pytest.skip.Exception):g.authorized_case(case,0,{})
    for settings,maxfail in [({'STOCKBRIDGE_STAFF_PG_ACK':'wrong'},1),
        ({'STOCKBRIDGE_STAFF_PG_ACK':g.ACK},0),({'STOCKBRIDGE_STAFF_PG_ACK':g.ACK},1)]:
        with pytest.raises(g.Rejected):g.authorized_case('M00',maxfail,settings)


@pytest.mark.parametrize('phase',['empty',g.V17,g.V18])
def test_explicit_schema_stage_no_revision_spoofing(phase):
    data=snapshot()
    for key in ('database','user','session_user','owner'):data[key]=g.NAME
    tables=[] if phase=='empty' else ['public.user']
    data.update(tables=tables,all_relations=tables,sequences=[],revision=None if phase=='empty' else phase)
    assert identity.validate_database(data,phase,tables,(1000,1001))
    assert data['revision']==(None if phase=='empty' else phase)
    bad=deepcopy(data);bad['revision']='unknown'
    with pytest.raises(ValueError):identity.validate_database(bad,phase,tables,(1000,1001))


@pytest.mark.parametrize('key,value',[
    ('owner','foreign'),('database','foreign'),('port',5432),('memberships',['monitor']),
    ('owned_other_databases',['billing']),('schemas',['public','foreign']),('extensions',['plpgsql','dblink']),
    ('listen_addresses','*'),('database_oid',0),('role_oid',9999),('role',{'login':True}),
    ('tables',['public.foreign']),('all_relations',['public.foreign'])])
def test_staff_schema_identity_stays_fail_closed(key,value):
    data=snapshot()
    for name in ('database','user','session_user','owner'):data[name]=g.NAME
    data['sequences']=[]
    data[key]=value
    with pytest.raises(ValueError):identity.validate_database(data,'empty',[],(1000,1001))


@pytest.mark.parametrize('sql,match',[
    ('UPDATE "user" SET last_activity_at="user".last_activity_at WHERE "user".id = %(id_1)s',True),
    ('UPDATE product SET stock_quantity=1 WHERE id=1',False),
    ('SELECT * FROM "user" FOR UPDATE',False),('UPDATE user SET last_activity_at=now() WHERE user.id=1',False)])
def test_exact_owner_lock_selector(sql,match):assert race.owner_lock(sql) is match


def harness(monkeypatch, *, rollback=False, stuck=False, blocked_observer=False):
    import threading
    engine=SimpleNamespace(hooks={});cleanup=threading.Event()
    monkeypatch.setattr(race.event,'listen',lambda e,n,f:e.hooks.__setitem__(n,f))
    monkeypatch.setattr(race.event,'remove',lambda e,n,f:e.hooks.pop(n))
    drivers=[SimpleNamespace(info=SimpleNamespace(backend_pid=n),cancel_safe=MagicMock()) for n in (1,2)]
    class Client:
        def __init__(self,index):self.index=index
        def post(self,path,data):
            driver=drivers[self.index];conn=SimpleNamespace(connection=SimpleNamespace(driver_connection=driver))
            engine.hooks['checkout'](driver,None,None)
            if stuck and self.index==0:cleanup.wait(2)
            sql='UPDATE "user" SET last_activity_at="user".last_activity_at WHERE "user".id = %(id_1)s'
            engine.hooks['before_cursor_execute'](conn,None,sql,{'id_1':9},None,False)
            engine.hooks['after_cursor_execute'](conn,None,sql,{'id_1':9},None,False)
            if rollback and self.index==0:
                try:engine.hooks['after_cursor_execute'](conn,None,'INSERT INTO staff_audit_event (id) VALUES (1)',None,None,False)
                except ValueError:
                    engine.hooks['rollback'](conn);return SimpleNamespace(status_code=400)
            engine.hooks['commit'](conn)
            return SimpleNamespace(status_code=302)
    observer=SimpleNamespace(info=SimpleNamespace(backend_pid=3),cancel_safe=MagicMock())
    def execute(*unused):
        if blocked_observer:cleanup.wait(2)
        return SimpleNamespace(fetchone=lambda:([1],))
    observer.execute=execute
    return engine,[Client(0),Client(1)],observer,cleanup


@pytest.mark.parametrize('rollback',[False,True])
def test_owner_overlap_and_rollback_inspection_order(monkeypatch,rollback):
    engine,clients,observer,cleanup=harness(monkeypatch,rollback=rollback);checks=[]
    def baseline():checks.append('fresh-before-second-commit')
    result=race.concurrent_posts(engine,clients,['/staff/manage']*2,[{},{}],observer,owner_id=9,
        rollback=rollback,baseline_check=baseline if rollback else None,timeout=1)
    assert result==([400,302] if rollback else [302,302])
    assert checks==(['fresh-before-second-commit'] if rollback else []) and not engine.hooks


@pytest.mark.parametrize('kind',['worker','observer'])
def test_deadline_unconfirmed_shutdown_is_fatal(monkeypatch,kind):
    import threading,time
    engine,clients,observer,cleanup=harness(monkeypatch,stuck=kind=='worker',blocked_observer=kind=='observer')
    start=time.monotonic()
    try:
        with pytest.raises(pytest.exit.Exception):
            race.concurrent_posts(engine,clients,['/staff/manage']*2,[{},{}],observer,owner_id=9,timeout=.2)
        assert time.monotonic()-start<.6
    finally:
        cleanup.set()
        for thread in threading.enumerate():
            if thread.name.startswith('stockbridge-race-'):thread.join(1)


def test_scenario_source_has_exact_18_ids_and_default_guard_precedes_fixtures():
    import ast
    path=Path(__file__).with_name('test_postgres_staff_foundation.py')
    tree=ast.parse(path.read_text());test=next(n for n in tree.body if isinstance(n,ast.FunctionDef))
    assert isinstance(test.body[0],ast.Assign) and test.body[0].value.func.id=='authorized_case'
    assert isinstance(test.body[1],ast.ImportFrom) and test.body[1].module.endswith('postgres_staff_fixtures')
    assert len(g.CASES)==18 and len(set(g.CASES))==18
