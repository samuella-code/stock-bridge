"""Synthetic connections/requests only: never open a PostgreSQL connection."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from tests.support import postgres_concurrency as race
from tests.support import ubuntu_evidence_helper as helper
from tests.support.postgres_connections import connection_policy
from tests.support.ubuntu_os_evidence import EvidenceError
from tests.test_ubuntu_postgres_preflight import configuration, evidence


def test_explicit_helper_session_bounds():
    assert helper.session_policy() == (2, 120)
    assert helper.session_policy(30, 900, 'approved-bounded-phase9a-evidence') == (60, 900)
    for args in [(0,120,None),(65,120,None),(30,900,None),(1,1801,None),(True,120,None)]:
        with pytest.raises(EvidenceError): helper.session_policy(*args)


def test_helper_pins_restart_and_listener_identity():
    from copy import deepcopy
    first=evidence();signature=helper.instance_signature(first)
    assert signature == helper.instance_signature(deepcopy(first))
    for key in ('postmaster_started', 'port'):
        other=deepcopy(first);other[key]+=1
        assert signature != helper.instance_signature(other)
    other=deepcopy(first);other['process']['start_ticks']+=1
    assert signature != helper.instance_signature(other)


def test_worker_observer_scram_and_windows_compatibility():
    for observe in (False,True):
        policy=connection_policy(configuration(),observer=observe)
        assert policy['require_auth']=='scram-sha-256'
        assert 'search_path=pg_catalog,public' in policy['options']
        assert ('default_transaction_read_only=on' in policy['options']) is observe
    from tests.test_postgres_existing_service_preflight import configuration as windows_configuration
    windows=connection_policy(windows_configuration())
    assert 'require_auth' not in windows
    assert windows==dict(connect_timeout=5,options='-c statement_timeout=10000 -c lock_timeout=5000')


@pytest.mark.parametrize('sql,kind,match',[
    ('SELECT * FROM product WHERE id=%s FOR UPDATE','product',True),
    ('UPDATE product SET stock_quantity=stock_quantity+1','product',True),
    ('SELECT * FROM product WHERE id=%s','product',False),
    ('SELECT * FROM sale WHERE id=%s FOR UPDATE','void',True),
    ('INSERT INTO transaction_submission (id) VALUES (%s)','receipt',True),
])
def test_sql_targets(sql,kind,match):
    assert race.target_statement(sql,kind) is match


class SyntheticEngine:
    def __init__(self): self.hooks={}


class SyntheticDriver:
    def __init__(self,pid):
        self.info=SimpleNamespace(backend_pid=pid)
        self.cancelled=threading.Event()
    def cancel_safe(self,timeout): self.cancelled.set()


def harness(monkeypatch, *, block=False, stuck=False, observer_block=False, kind='receipt'):
    engine=SyntheticEngine();drivers=[SyntheticDriver(1),SyntheticDriver(2)]
    monkeypatch.setattr(race.event,'listen',lambda engine,name,fn:engine.hooks.__setitem__(name,fn))
    monkeypatch.setattr(race.event,'remove',lambda engine,name,fn:engine.hooks.pop(name))
    paths=[];cleanup=threading.Event()
    class Client:
        def __init__(self,i): self.i=i
        def post(self,path,data):
            paths.append(path);driver=drivers[self.i]
            engine.hooks['checkout'](driver,None,None)
            if block and self.i==0:
                (cleanup if stuck else driver.cancelled).wait(3)
                return SimpleNamespace(status_code=302)
            conn=SimpleNamespace(connection=SimpleNamespace(driver_connection=driver))
            sql=('SELECT * FROM product WHERE id=1 FOR UPDATE' if kind=='product' else 'INSERT INTO transaction_submission (id) VALUES (1)')
            engine.hooks['before_cursor_execute'](conn,None,sql,None,None,None)
            engine.hooks['after_cursor_execute'](conn,None,sql,None,None,None)
            return SimpleNamespace(status_code=302)
    observer=MagicMock()
    def observe(*args):
        if observer_block: observer.cancelled.wait(3)
        return SimpleNamespace(fetchone=lambda:([1],))
    observer.cancelled=threading.Event()
    observer.execute.side_effect=observe
    observer.cancel_safe.side_effect=lambda **kwargs:observer.cancelled.set()
    return engine,[Client(0),Client(1)],observer,drivers,paths,cleanup


@pytest.mark.parametrize('paths',['/sales/',('/sales/','/restocking/receive')])
def test_same_and_mixed_paths(monkeypatch,paths):
    engine,clients,observer,drivers,seen,_=harness(monkeypatch)
    statuses,observed=race.concurrent_posts(engine,clients,paths,[{},{}],observer)
    assert statuses==[302,302] and observed
    assert seen==(['/sales/','/sales/'] if isinstance(paths,str) else list(paths))
    assert engine.hooks=={}


def test_early_connection_cancel_and_overall_deadline(monkeypatch):
    engine,clients,observer,drivers,_,_=harness(monkeypatch,block=True)
    start=time.monotonic()
    with pytest.raises(race.RaceTimeout):
        race.concurrent_posts(engine,clients,'/sales/',[{},{}],observer,scenario_timeout=.45)
    assert drivers[0].cancelled.is_set() and engine.hooks=={}
    assert time.monotonic()-start < .8


def test_blocked_observer_cancelled(monkeypatch):
    engine,clients,observer,drivers,_,_=harness(monkeypatch,observer_block=True)
    with pytest.raises(race.RaceTimeout):
        race.concurrent_posts(engine,clients,'/sales/',[{},{}],observer,scenario_timeout=.45)
    assert observer.cancel_safe.called and engine.hooks=={}


def test_unconfirmed_shutdown_stops_pytest_run(monkeypatch):
    engine,clients,observer,drivers,_,cleanup=harness(monkeypatch,block=True,stuck=True)
    try:
        with pytest.raises(pytest.exit.Exception):
            race.concurrent_posts(engine,clients,'/sales/',[{},{}],observer,scenario_timeout=.3)
    finally:
        cleanup.set()
        # Synthetic worker only; permit it to exit before this unit test returns.
        for thread in threading.enumerate():
            if thread.name.startswith('stockbridge-race-'): thread.join(1)


def test_mutation_gate_before_preflight(monkeypatch):
    from tests import test_postgres_transaction_concurrency as suite
    monkeypatch.delenv('STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK',raising=False)
    check=MagicMock();monkeypatch.setattr(suite,'preflight',check)
    with pytest.raises(pytest.skip.Exception): next(suite.pg_app.__wrapped__())
    check.assert_not_called()


@pytest.mark.parametrize('paths',['/sales/',('/sales/','/restocking/receive')])
def test_product_lock_coordination_with_synthetic_connections(monkeypatch,paths):
    engine,clients,observer,drivers,seen,_=harness(monkeypatch,kind='product')
    statuses,observed=race.concurrent_posts(engine,clients,paths,[{},{}],observer,lock_kind='product')
    assert statuses==[302,302] and observed
    assert observer.execute.call_args.args[1]==(2,)


def test_ubuntu_old_libpq_rejected_before_connection(monkeypatch):
    from tests.support.isolated_postgres import UnsafeTestDatabase
    monkeypatch.setattr('psycopg.pq.version',lambda:150000)
    with pytest.raises(UnsafeTestDatabase):connection_policy(configuration())


def test_authorized_ack_still_requires_manifest_before_preflight(monkeypatch):
    from tests import test_postgres_transaction_concurrency as suite
    env=configuration();env['STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK']='approved-phase5d5b-synthetic-only'
    monkeypatch.setattr(suite.os,'environ',env)
    check=MagicMock();monkeypatch.setattr(suite,'preflight',check)
    with pytest.raises(pytest.fail.Exception):next(suite.pg_app.__wrapped__())
    check.assert_not_called()


def mock_operator_session(monkeypatch, snapshots, *, peer=1000):
    """Exercise the server loop without binding sockets or reading protected files."""
    from unittest.mock import MagicMock
    import stat
    import sys
    server=MagicMock();conn=MagicMock()
    conn.__enter__.return_value=conn
    server.accept.return_value=(conn,None)
    path=MagicMock();path.exists.return_value=False;path.is_symlink.return_value=False
    path.lstat.return_value=SimpleNamespace(st_ino=12,st_uid=110,st_mode=stat.S_IFSOCK)
    monkeypatch.setattr(helper,'SOCKET',path)
    monkeypatch.setattr(helper,'sys',SimpleNamespace(platform='linux',flags=SimpleNamespace(isolated=True),executable=sys.executable))
    monkeypatch.setattr(helper,'pwd',SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_uid=110)))
    monkeypatch.setattr(helper,'os',SimpleNamespace(geteuid=lambda:110,chmod=MagicMock()))
    monkeypatch.setattr(helper,'socket',SimpleNamespace(AF_UNIX=1,SOCK_STREAM=1,socket=lambda *a:server))
    monkeypatch.setattr(helper,'secure_path',lambda *a:None)
    monkeypatch.setattr(helper,'secure_parent',lambda:None)
    monkeypatch.setattr(helper,'peer_uid',lambda conn:peer)
    monkeypatch.setattr(helper,'receive',lambda conn,deadline=None:dict(nonce='a'*64,backend_pid=None))
    monkeypatch.setattr(helper.collector,'collect',MagicMock(side_effect=snapshots))
    return server,conn,path


def test_extended_helper_serves_exact_finite_budget(monkeypatch):
    server,conn,path=mock_operator_session(monkeypatch,[evidence() for _ in range(4)])
    helper.serve(1000,preflights=2,seconds=120,acknowledgment='approved-bounded-phase9a-evidence')
    assert server.accept.call_count==4 and conn.sendall.call_count==4
    assert server.close.called and path.unlink.called


@pytest.mark.parametrize('failure',['peer','restart','expiry'])
def test_operator_session_fails_closed(monkeypatch,failure):
    from copy import deepcopy
    first=evidence();second=deepcopy(first);second['postmaster_started']+=1
    server,conn,path=mock_operator_session(monkeypatch,[first,second],peer=999 if failure=='peer' else 1000)
    if failure=='expiry':
        monkeypatch.setattr(helper,'time',SimpleNamespace(monotonic=MagicMock(side_effect=[0,121])))
    with pytest.raises(EvidenceError):helper.serve(1000)
    assert server.close.called and path.unlink.called
    assert conn.sendall.call_count==(1 if failure=='restart' else 0)


def test_receive_slow_chunks_cannot_extend_absolute_deadline(monkeypatch):
    conn=MagicMock();conn.recv.return_value=b' '
    monkeypatch.setattr(helper,'time',SimpleNamespace(monotonic=MagicMock(side_effect=[1,2,6])))
    with pytest.raises(EvidenceError):helper.receive(conn,deadline=5)
    assert conn.recv.call_count==2
    assert [call.args[0] for call in conn.settimeout.call_args_list]==[4,3]


def test_client_receive_preserves_collection_wait_with_absolute_cap(monkeypatch):
    conn=MagicMock();conn.recv.return_value=b'{}\n'
    monkeypatch.setattr(helper,'time',SimpleNamespace(monotonic=lambda:1))
    assert helper.receive(conn,deadline=35,read_timeout=35)=={}
    conn.settimeout.assert_called_with(34)


def test_collector_subprocess_uses_remaining_budget(monkeypatch):
    from tests.support import ubuntu_os_evidence as collector
    run=MagicMock(return_value=SimpleNamespace(returncode=0,stdout='ok',stderr=''))
    monkeypatch.setattr(collector.subprocess,'run',run)
    assert collector.command(['/usr/bin/ss','-H','-ltnp'],timeout=.1)=='ok'
    assert run.call_args.kwargs['timeout']==.1
    with pytest.raises(EvidenceError):collector.command(['/usr/bin/ss'],timeout=0)
    assert run.call_count==1


def test_run_guard_requires_fail_fast_when_mutations_authorized(monkeypatch):
    from tests import test_postgres_transaction_concurrency as suite
    monkeypatch.setenv('STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK','approved-phase5d5b-synthetic-only')
    request=SimpleNamespace(config=SimpleNamespace(getoption=lambda name:0))
    with pytest.raises(pytest.exit.Exception):suite.mutation_run_guard.__wrapped__(request)
    request.config.getoption=lambda name:1
    suite.mutation_run_guard.__wrapped__(request)


def test_post_scenario_identity_checks_without_writes(monkeypatch):
    from tests import test_postgres_transaction_concurrency as suite
    data=(object(),{},['public.alembic_version'],(10,11))
    check=MagicMock();monkeypatch.setattr(suite,'preflight',check)
    request=SimpleNamespace(getfixturevalue=lambda name:data)
    generator=suite.post_scenario_identity.__wrapped__(request)
    next(generator);check.assert_not_called()
    with pytest.raises(StopIteration):next(generator)
    check.assert_called_once_with({},phase='migrated',expected_tables=['public.alembic_version'],expected_identity=(10,11))


def test_post_scenario_never_inspects_after_surviving_worker(monkeypatch):
    from tests import test_postgres_transaction_concurrency as suite
    data=(object(),{},[],(10,11));check=MagicMock();monkeypatch.setattr(suite,'preflight',check)
    generator=suite.post_scenario_identity.__wrapped__(SimpleNamespace(getfixturevalue=lambda name:data))
    next(generator)
    with monkeypatch.context() as local:
        local.setattr(threading,'enumerate',lambda:[SimpleNamespace(name='stockbridge-race-A',is_alive=lambda:True)])
        with pytest.raises(pytest.exit.Exception):next(generator)
    check.assert_not_called()


@pytest.mark.parametrize('stage',[
    'accept', 'receive', 'evidence-collection', 'instance-signature',
    'backend-inspection', 'response-serialization', 'response-transmission',
])
@pytest.mark.parametrize('ordinal',[1,2])
def test_helper_diagnostics_identify_request_and_stage(monkeypatch,stage,ordinal):
    server,conn,path=mock_operator_session(monkeypatch,[evidence(),evidence()])
    secret='synthetic-private-exception-message'
    error=TimeoutError(secret)
    backend=dict(pid=5001,parent_pid=5000,uid=110,executable=helper.collector.EXECUTABLE)
    monkeypatch.setattr(helper.collector,'process_identity',MagicMock(return_value=backend))
    monkeypatch.setattr(helper,'receive',MagicMock(side_effect=[
        dict(nonce='a'*64,backend_pid=5001),dict(nonce='b'*64,backend_pid=5001)]))
    def fail_on_selected(original):
        calls=0
        def wrapped(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==ordinal: raise error
            return original(*args,**kwargs)
        return wrapped
    if stage=='accept': server.accept.side_effect=fail_on_selected(lambda:(conn,None))
    elif stage=='receive':
        monkeypatch.setattr(helper,'receive',fail_on_selected(lambda *a,**k:dict(nonce='a'*64,backend_pid=5001)))
    elif stage=='evidence-collection':
        monkeypatch.setattr(helper.collector,'collect',fail_on_selected(lambda **k:evidence()))
    elif stage=='instance-signature':monkeypatch.setattr(helper,'instance_signature',fail_on_selected(helper.instance_signature))
    elif stage=='backend-inspection':monkeypatch.setattr(helper.collector,'process_identity',fail_on_selected(lambda pid:backend))
    elif stage=='response-serialization':monkeypatch.setattr(helper.json,'dumps',fail_on_selected(helper.json.dumps))
    else:conn.sendall.side_effect=fail_on_selected(lambda payload:None)
    with pytest.raises(EvidenceError) as caught:helper.serve(1000)
    message=str(caught.value)
    assert message==f'PostgreSQL preflight rejected: Ubuntu privileged helper failed: stage={stage} request={ordinal} category=timeout'
    assert secret not in message and 'a'*64 not in message and 'b'*64 not in message
    assert server.close.called and path.unlink.called
    assert conn.sendall.call_count==(ordinal if stage=='response-transmission' else ordinal-1)


@pytest.mark.parametrize('operation',['construct','bind','chmod','listen'])
def test_helper_socket_setup_failures_are_sanitized(monkeypatch,operation):
    server,conn,path=mock_operator_session(monkeypatch,[evidence()])
    failure=PermissionError('private-path-and-credentials')
    if operation=='construct':helper.socket.socket=MagicMock(side_effect=failure)
    elif operation=='chmod':helper.os.chmod.side_effect=failure
    else:getattr(server,operation).side_effect=failure
    with pytest.raises(EvidenceError) as caught:helper.serve(1000)
    assert str(caught.value)=='PostgreSQL preflight rejected: Ubuntu privileged helper failed: stage=socket-setup category=permission-denied'
    assert not conn.sendall.called
    if operation!='construct':assert server.close.called
    if operation in ('chmod','listen'):assert path.unlink.called


@pytest.mark.parametrize('error,category',[
    (TimeoutError('private'),'timeout'),(PermissionError('private'),'permission-denied'),
    (BrokenPipeError('private'),'peer-disconnected'),(ConnectionResetError('private'),'peer-disconnected'),
    (ConnectionAbortedError('private'),'peer-disconnected'),(KeyError('private'),'missing-evidence'),
    (ValueError('private'),'inspection-failed'),(TypeError('private'),'inspection-failed'),
    (OSError('private'),'inspection-failed'),(EvidenceError('private'),'inspection-failed'),
])
def test_helper_diagnostic_categories_are_allowlisted(error,category):
    assert helper.diagnostic_category(error)==category


def test_helper_verifies_second_request_backend_and_serializes_response(monkeypatch):
    import json
    server,conn,path=mock_operator_session(monkeypatch,[evidence(),evidence()])
    monkeypatch.setattr(helper,'receive',MagicMock(side_effect=[
        dict(nonce='a'*64,backend_pid=None),dict(nonce='b'*64,backend_pid=5001)]))
    backend=dict(pid=5001,parent_pid=5000,uid=110,executable=helper.collector.EXECUTABLE,start_ticks=123)
    inspect=MagicMock(return_value=backend);monkeypatch.setattr(helper.collector,'process_identity',inspect)
    helper.serve(1000)
    inspect.assert_called_once_with(5001)
    responses=[json.loads(call.args[0]) for call in conn.sendall.call_args_list]
    assert responses[0]['backend'] is None and responses[1]['backend']==backend
    assert responses[1]['nonce']=='b'*64
    assert server.accept.call_count==2 and server.close.called and path.unlink.called


@pytest.mark.parametrize('key,value',[('parent_pid',999),('uid',999),('executable','/unexpected')])
def test_helper_backend_mismatch_remains_rejected(monkeypatch,key,value):
    server,conn,path=mock_operator_session(monkeypatch,[evidence(),evidence()])
    monkeypatch.setattr(helper,'receive',MagicMock(side_effect=[dict(nonce='a'*64,backend_pid=None),dict(nonce='b'*64,backend_pid=5001)]))
    backend=dict(pid=5001,parent_pid=5000,uid=110,executable=helper.collector.EXECUTABLE)
    backend[key]=value
    monkeypatch.setattr(helper.collector,'process_identity',MagicMock(return_value=backend))
    with pytest.raises(EvidenceError,match='^PostgreSQL preflight rejected: Ubuntu privileged helper backend outside isolated cluster$'):helper.serve(1000)
    assert conn.sendall.call_count==1 and path.unlink.called


def test_helper_missing_signature_evidence_is_classified(monkeypatch):
    server,conn,path=mock_operator_session(monkeypatch,[{}])
    with pytest.raises(EvidenceError,match='stage=instance-signature request=1 category=missing-evidence'):helper.serve(1000)
    assert not conn.sendall.called and path.unlink.called


@pytest.mark.parametrize('fail_fourth',[False,True])
def test_helper_request_ordinals_reset_for_each_preflight_pair(monkeypatch,fail_fourth):
    import inspect
    server,conn,path=mock_operator_session(monkeypatch,[evidence() for _ in range(4)])
    ordinals=[]
    def receive(connection,deadline=None):
        # Observe the server's assigned ordinal at the mocked request boundary.
        frame=inspect.currentframe()
        try: ordinals.append(frame.f_back.f_locals['request_ordinal'])
        finally: del frame
        if fail_fourth and len(ordinals)==4: raise TimeoutError('synthetic-private-message')
        return dict(nonce='a'*64,backend_pid=None)
    monkeypatch.setattr(helper,'receive',receive)
    if fail_fourth:
        with pytest.raises(EvidenceError) as caught:
            helper.serve(1000,preflights=2,acknowledgment='approved-bounded-phase9a-evidence')
        assert str(caught.value)=='PostgreSQL preflight rejected: Ubuntu privileged helper failed: stage=receive request=2 category=timeout'
    else:
        helper.serve(1000,preflights=2,acknowledgment='approved-bounded-phase9a-evidence')
    assert ordinals==[1,2,1,2]
    assert conn.sendall.call_count==(3 if fail_fourth else 4)
    assert server.close.called and path.unlink.called


@pytest.mark.parametrize('operation',['receive','collection','backend'])
def test_existing_evidence_rejection_propagates_unchanged(monkeypatch,operation):
    server,conn,path=mock_operator_session(monkeypatch,[evidence(),evidence()])
    original=EvidenceError('PostgreSQL preflight rejected: Ubuntu synthetic security rejection')
    if operation=='receive':monkeypatch.setattr(helper,'receive',MagicMock(side_effect=original))
    elif operation=='collection':monkeypatch.setattr(helper.collector,'collect',MagicMock(side_effect=original))
    else:
        monkeypatch.setattr(helper,'receive',lambda *a,**k:dict(nonce='a'*64,backend_pid=5001))
        monkeypatch.setattr(helper.collector,'process_identity',MagicMock(side_effect=original))
    with pytest.raises(EvidenceError) as caught:helper.serve(1000)
    assert caught.value is original
    assert str(caught.value)=='PostgreSQL preflight rejected: Ubuntu synthetic security rejection'
    assert not conn.sendall.called and server.close.called and path.unlink.called
