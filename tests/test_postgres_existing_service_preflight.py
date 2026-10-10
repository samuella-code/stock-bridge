"""Pure snapshots and mocks only: never contact PostgreSQL or Windows."""
from copy import deepcopy
from unittest.mock import patch
import pytest
from tests.test_isolated_postgres_guard import configured
from tests.support.isolated_postgres import validated_test_url, UnsafeTestDatabase
from tests.support.postgres_preflight import validate_os, validate_database, preflight


def configuration():
    env=configured()
    env.update(STOCKBRIDGE_TEST_POSTGRES_MODE='existing-local-service',
        STOCKBRIDGE_TEST_POSTGRES_SERVICE_ACK='postgresql-x64-17:stockbridge_disposable_test:stockbridge_test_user',
        STOCKBRIDGE_TEST_POSTGRES_URL='postgresql+psycopg://stockbridge_test_user:synthetic@127.0.0.1:5432/stockbridge_disposable_test')
    return env


def os_snapshot():
    return dict(service_name='postgresql-x64-17',service_state='Running',service_pid=3524,
        service_path='"C:\\Program Files\\PostgreSQL\\17\\bin\\pg_ctl.exe" runservice -D "C:\\Program Files\\PostgreSQL\\17\\data"',
        service_executable='C:\\Program Files\\PostgreSQL\\17\\bin\\pg_ctl.exe',listener_pid=7516,
        listener_parent_pid=3524,listener_name='postgres.exe',
        listener_executable='C:\\Program Files\\PostgreSQL\\17\\bin\\postgres.exe',listener_addresses=['127.0.0.1','::1'])


def db_snapshot():
    return dict(database='stockbridge_disposable_test',user='stockbridge_test_user',session_user='stockbridge_test_user',
        owner='stockbridge_test_user',address='127.0.0.1',port=5432,version=170011,
        role=dict(superuser=False,createdb=False,createrole=False,replication=False,bypassrls=False,login=True),
        memberships=[],owned_other_databases=[],schemas=['public'],extensions=['plpgsql'],tables=[],revision=None,
        data_directory='C:\\Program Files\\PostgreSQL\\17\\data',listen_addresses='localhost',
        hba_rules=[dict(type='host',database=['all'],user=['all'],address='127.0.0.1',netmask='255.255.255.255',auth_method='scram-sha-256',error=None)])


def test_existing_mode_static_approval_and_runtime_verification():
    assert validated_test_url(configuration()).port==5432
    assert validate_database(db_snapshot(),validate_os(os_snapshot()))

@pytest.mark.parametrize('key,value', [
    ('STOCKBRIDGE_TEST_POSTGRES_MODE','anything'),('STOCKBRIDGE_TEST_POSTGRES_SERVICE_ACK',''),
    ('STOCKBRIDGE_TEST_POSTGRES_ACK',''),
    ('STOCKBRIDGE_TEST_POSTGRES_URL','postgresql+psycopg://stockbridge_test_user:synthetic@production.neon.tech:5432/stockbridge_disposable_test'),
    ('STOCKBRIDGE_TEST_POSTGRES_URL','postgresql+psycopg://stockbridge_test_user:synthetic@127.0.0.1:5432/stockbridge-staging'),
    ('STOCKBRIDGE_TEST_POSTGRES_URL','postgresql+psycopg://postgres:synthetic@127.0.0.1:5432/stockbridge_disposable_test'),
    ('STOCKBRIDGE_TEST_POSTGRES_URL','postgresql+psycopg://stockbridge_test_user:synthetic@127.0.0.1:5432/stockbridge_disposable_test?host=staging'),
    ('PGHOST','staging'),('VERCEL_PROJECT_ID','synthetic')])
def test_static_rejections(key,value):
    env=configuration();env[key]=value
    with pytest.raises(UnsafeTestDatabase):validated_test_url(env)

@pytest.mark.parametrize('key,value', [
    ('listener_executable',''),('listener_name','ssh.exe'),('listener_parent_pid',1),
    ('service_path','unknown'),('listener_addresses',['0.0.0.0','::']),('service_state','Stopped')])
def test_os_identity_rejected(key,value):
    data=os_snapshot();data[key]=value
    with pytest.raises(UnsafeTestDatabase):validate_os(data)

@pytest.mark.parametrize('key,value', [
    ('database','production'),('session_user','postgres'),('owner','postgres'),('version',160000),
    ('address','192.168.1.1'),('port',55432),('memberships',['pg_read_all_data']),
    ('owned_other_databases',['another']),('schemas',['public','other']),('extensions',['plpgsql','dblink']),
    ('tables',['public.user']),('revision','0017_transaction_submission'),('listen_addresses','*'),
    ('hba_rules',[]),('data_directory','another')])
def test_metadata_rejections(key,value):
    data=db_snapshot();data[key]=value
    with pytest.raises(UnsafeTestDatabase):validate_database(data,validate_os(os_snapshot()))

@pytest.mark.parametrize('key',['superuser','createdb','createrole','replication','bypassrls'])
def test_effective_privilege_rejections(key):
    data=db_snapshot();data['role'][key]=True
    with pytest.raises(UnsafeTestDatabase):validate_database(data,validate_os(os_snapshot()))


def test_trust_authentication_rejected():
    data=db_snapshot();data['hba_rules'][0]['auth_method']='trust'
    with pytest.raises(UnsafeTestDatabase):validate_database(data,validate_os(os_snapshot()))


def test_os_failure_precedes_connection():
    with patch('tests.support.postgres_preflight.windows_evidence',return_value={}), patch('psycopg.connect') as connect:
        with pytest.raises(UnsafeTestDatabase):preflight(configuration())
        connect.assert_not_called()


def test_migrated_phase_requires_exact_revision_and_relations():
    data=db_snapshot();data.update(tables=['public.alembic_version'],revision='0017_transaction_submission')
    directory=validate_os(os_snapshot())
    assert validate_database(data,directory,'migrated',['public.alembic_version'])
    data['tables'].append('public.unexpected')
    with pytest.raises(UnsafeTestDatabase):validate_database(data,directory,'migrated',['public.alembic_version'])


def firewall_snapshot():
    data=os_snapshot();data['listener_addresses']=['0.0.0.0','::']
    data['firewall_profiles']=[dict(Name=name,Enabled=True,DefaultInboundAction='Block') for name in ('Domain','Private','Public')]
    data['inbound_allow_rules']=[]
    return data


def test_wildcard_listener_requires_complete_firewall_evidence():
    data=firewall_snapshot()
    directory=validate_os(data)
    snapshot=db_snapshot();snapshot['listen_addresses']='*'
    assert validate_database(snapshot,directory,network_verified=True)
    with pytest.raises(UnsafeTestDatabase):validate_database(snapshot,directory)

@pytest.mark.parametrize('change',['disabled','default_allow','broad_rule','unknown_ports','unknown_protocol','incomplete_profiles'])
def test_fail_closed_firewall_review(change):
    data=firewall_snapshot()
    if change=='disabled':data['firewall_profiles'][0]['Enabled']=False
    if change=='default_allow':data['firewall_profiles'][0]['DefaultInboundAction']='Allow'
    if change=='incomplete_profiles':data['firewall_profiles'].pop()
    if change in ('broad_rule','unknown_ports','unknown_protocol'):
        rule=dict(protocol='TCP',ports=['5432'],program='Any',remote=['Any'])
        if change=='unknown_ports':rule['ports']=['RPC']
        if change=='unknown_protocol':rule['protocol']='unknown'
        data['inbound_allow_rules']=[rule]
    with pytest.raises(UnsafeTestDatabase):validate_os(data)


def test_ipv6_loopback_rule_does_not_expand_exposure():
    data=firewall_snapshot();data['inbound_allow_rules']=[dict(protocol='TCP',ports=['5432'],program='Any',remote=['::1','127.0.0.1'])]
    assert validate_os(data)


def test_second_matching_trust_hba_rule_rejected():
    data=db_snapshot();rule=deepcopy(data['hba_rules'][0]);rule.update(type='hostnossl',auth_method='trust');data['hba_rules'].append(rule)
    with pytest.raises(UnsafeTestDatabase):validate_database(data,validate_os(os_snapshot()))


def test_preparation_suite_skips_before_preflight(monkeypatch):
    from tests import test_postgres_transaction_concurrency as module
    monkeypatch.delenv('STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK',raising=False)
    with patch.object(module,'preflight') as check:
        generator=module.pg_app.__wrapped__()
        with pytest.raises(pytest.skip.Exception):next(generator)
        check.assert_not_called()


def test_manifest_oid_binding():
    snapshot=db_snapshot();snapshot.update(database_oid=100,role_oid=101)
    directory=validate_os(os_snapshot())
    assert validate_database(snapshot,directory,expected_identity=(100,101))
    with pytest.raises(UnsafeTestDatabase):validate_database(snapshot,directory,expected_identity=(102,101))


@pytest.mark.parametrize('failure,expected', [
    ('timeout','timed out'),('missing','PowerShell executable unavailable'),
    ('denied','execution denied'),('os','could not be started'),
    ('decode','output decoding failed'),('json','invalid JSON'),
    ('nonobject','non-object'),('raw','without structured diagnostics'),
    ('unknown','without recognized diagnostics'),
])
def test_windows_collector_safe_diagnostics(failure,expected):
    import subprocess
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    secret='synthetic-private-token-do-not-log'
    exceptions={
        'timeout':subprocess.TimeoutExpired(secret,10,output=secret,stderr=secret),
        'missing':FileNotFoundError(secret),'denied':PermissionError(secret),
        'os':OSError(secret),'decode':UnicodeError(secret),
    }
    outputs={'json':(0,secret),'nonobject':(0,'[]'),'raw':(1,secret),
             'unknown':(1,'{"inspection_error":"'+secret+'"}')}
    kwargs={'side_effect':exceptions[failure]} if failure in exceptions else {
        'return_value':SimpleNamespace(returncode=outputs[failure][0],stdout=outputs[failure][1],stderr=secret)}
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.subprocess,'run',**kwargs):
        with pytest.raises(UnsafeTestDatabase,match=expected) as error:module.windows_evidence()
        assert secret not in str(error.value)


@pytest.mark.parametrize('stage',sorted(__import__('tests.support.postgres_preflight',fromlist=['WINDOWS_FAILURE_CODES']).WINDOWS_FAILURE_CODES))
def test_windows_collector_stage_codes(stage):
    import json
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    response=SimpleNamespace(returncode=1,stdout=json.dumps({'inspection_error':stage}),stderr='private-output')
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.subprocess,'run',return_value=response):
        with pytest.raises(UnsafeTestDatabase,match=stage) as error:module.windows_evidence()
        assert 'private-output' not in str(error.value)


def test_missing_collector_rejected_before_execution():
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.Path,'is_file',return_value=False),patch.object(module.subprocess,'run') as run:
        with pytest.raises(UnsafeTestDatabase,match='collector file missing'):module.windows_evidence()
        run.assert_not_called()


def test_successful_collector_preserves_evidence():
    import json
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    evidence=os_snapshot()
    response=SimpleNamespace(returncode=0,stdout=json.dumps(evidence),stderr='ignored-private-output')
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.subprocess,'run',return_value=response):
        assert module.windows_evidence()==evidence



def test_collector_uses_fixed_bounded_timeout():
    import json
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    response=SimpleNamespace(returncode=0,stdout=json.dumps(os_snapshot()),stderr='')
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.subprocess,'run',return_value=response) as run:
        module.windows_evidence()
        assert run.call_args.kwargs['timeout']==60
        assert run.call_args.kwargs['check'] is False


def test_timeout_prevents_database_connection():
    import subprocess
    from types import SimpleNamespace
    from tests.support import postgres_preflight as module
    with patch.object(module,'os',SimpleNamespace(name='nt')),patch.object(module.subprocess,'run',side_effect=subprocess.TimeoutExpired('private',60,output='private',stderr='private')),patch('psycopg.connect') as connect:
        with pytest.raises(UnsafeTestDatabase,match='timed out after 60 seconds') as error:module.preflight(configuration())
        assert 'private' not in str(error.value)
        connect.assert_not_called()


@pytest.mark.parametrize('rule',[
    dict(protocol='UDP',program='Any',ports=['5432'],remote=[]),
    dict(protocol='TCP',program=r'C:\Windows\unrelated.exe',ports=['5432'],remote=[]),
])
def test_irrelevant_rules_need_no_remote_address_query(rule):
    evidence=firewall_snapshot();evidence['inbound_allow_rules']=[rule]
    assert validate_os(evidence)


def test_relevant_rule_still_requires_remote_evidence():
    evidence=firewall_snapshot();evidence['inbound_allow_rules']=[dict(protocol='TCP',program='Any',ports=['5432'],remote=[])]
    with pytest.raises(UnsafeTestDatabase,match='remote address unavailable'):validate_os(evidence)


PACKAGE_SID = 'S-1-15-2-1-2-3-4-5-6-7'


def package_snapshot():
    evidence=firewall_snapshot()
    evidence['listener_package_identity']=dict(method='win32-process-and-token',
        status='unpackaged-non-appcontainer',pid=evidence['listener_pid'])
    evidence['inbound_allow_rules']=[dict(protocol='Any',ports=['Any'],program='Any',
        remote=['Any'],package=PACKAGE_SID)]
    return evidence


def test_specific_package_excludes_verified_unpacked_listener():
    assert validate_os(package_snapshot())


@pytest.mark.parametrize('package',[None,'','Any','S-1-15-2-1','S-1-5-18','*',
    ['S-1-15-2-1-2-3-4-5-6-7'],'S-1-15-2-4294967296-2-3-4-5-6-7'])
def test_package_field_is_not_automatic_exclusion(package):
    evidence=package_snapshot();evidence['inbound_allow_rules'][0]['package']=package
    with pytest.raises(UnsafeTestDatabase):validate_os(evidence)


@pytest.mark.parametrize('identity',[None,{},dict(method='manual',status='unpackaged-non-appcontainer',pid=7516),
    dict(method='win32-process-and-token',status='packaged',pid=7516),
    dict(method='win32-process-and-token',status='appcontainer',pid=7516),
    dict(method='win32-process-and-token',status='unpackaged-non-appcontainer',pid=3524),
    dict(method='win32-process-and-token',status='unpackaged-non-appcontainer',pid='7516')])
def test_package_requires_independent_listener_evidence(identity):
    evidence=package_snapshot();evidence['listener_package_identity']=identity
    with pytest.raises(UnsafeTestDatabase,match='not independently verified'):validate_os(evidence)


def test_package_exclusion_does_not_hide_another_unrestricted_rule():
    evidence=package_snapshot()
    rule=deepcopy(evidence['inbound_allow_rules'][0]);rule.pop('package')
    evidence['inbound_allow_rules'].append(rule)
    with pytest.raises(UnsafeTestDatabase):validate_os(evidence)


def test_package_exclusion_preserves_profile_requirement():
    evidence=package_snapshot();evidence['firewall_profiles']=[]
    with pytest.raises(UnsafeTestDatabase):validate_os(evidence)


def test_package_diagnostic_does_not_echo_untrusted_metadata():
    evidence=package_snapshot();evidence['inbound_allow_rules'][0]['package']='synthetic-secret-credential'
    with pytest.raises(UnsafeTestDatabase) as error:validate_os(evidence)
    assert 'synthetic-secret-credential' not in str(error.value)
