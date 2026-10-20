"""Synthetic evidence only. No live commands, sockets or database connections."""
from copy import deepcopy
from unittest.mock import MagicMock, patch
import subprocess
import sys
import pytest
from tests.support.isolated_postgres import validated_test_url, UnsafeTestDatabase
from tests.support.postgres_preflight import preflight
from tests.support import ubuntu_postgres_identity as ubuntu

RUN='9ca65feb59b0ca68'
NAME='stockbridge_test_'+RUN


def configuration():
    return dict(STOCKBRIDGE_TEST_POSTGRES_ACK='disposable-local-only',
        STOCKBRIDGE_TEST_POSTGRES_MODE='ubuntu-isolated-cluster',
        STOCKBRIDGE_TEST_POSTGRES_UBUNTU_ACK='16/stockbridge-test:55432:loopback-only',
        STOCKBRIDGE_TEST_POSTGRES_RUN_ID=RUN,
        STOCKBRIDGE_TEST_POSTGRES_URL=f'postgresql+psycopg://{NAME}:synthetic@127.0.0.1:55432/{NAME}')


def evidence():
    return dict(os='linux',distribution='ubuntu',cluster='16/stockbridge-test',version_major=16,
        directory=ubuntu.DIRECTORY,port=55432,postmaster_started=1720000000,postgres_uid=110,
        process=dict(pid=5000,parent_pid=1,start_ticks=12000,uid=110,executable=ubuntu.EXECUTABLE),
        commandline=[ubuntu.EXECUTABLE,'-D',ubuntu.DIRECTORY,''],
        sockets=[dict(protocol='TCP',state='LISTEN',address=address,port=55432,pids=[5000]) for address in ('127.0.0.1','::1')])


def snapshot():
    return dict(database=NAME,user=NAME,session_user=NAME,owner=NAME,address='127.0.0.1',port=55432,
        version=160011,database_oid=1000,role_oid=1001,backend_pid=5001,
        role=dict(login=True,superuser=False,createdb=False,createrole=False,replication=False,bypassrls=False),
        memberships=[],owned_other_databases=[],schemas=['public'],extensions=['plpgsql'],tables=[],
        all_relations=[],indexes=[],revision=None,data_directory=ubuntu.DIRECTORY,listen_addresses='localhost',transport_ssl=False,
        hba_rules=[dict(rule_number=1,simple_syntax=True,has_options=False,type='host',database=['all'],user=['all'],address='127.0.0.1',netmask='255.255.255.255',auth_method='scram-sha-256',error=None)])


def test_correct_identity_and_url():
    assert validated_test_url(configuration()).port==55432
    assert ubuntu.validate_ubuntu_os(evidence())==ubuntu.DIRECTORY
    assert ubuntu.validate_ubuntu_database(snapshot(),RUN)


@pytest.mark.parametrize('key,value',[
    ('STOCKBRIDGE_TEST_POSTGRES_ACK',''),('STOCKBRIDGE_TEST_POSTGRES_UBUNTU_ACK',''),
    ('STOCKBRIDGE_TEST_POSTGRES_RUN_ID',''),('PGHOST','neon'),('PGOPTIONS','unsafe'),('VERCEL_ENV','production'),
    *[('STOCKBRIDGE_TEST_POSTGRES_URL',f'postgresql+psycopg://{NAME}:synthetic@{host}:{port}/{database}{query}')
      for host,port,database,query in [('127.0.0.1',5432,NAME,''),('127.0.0.1',55433,NAME,''),
      ('localhost',55432,NAME,''),('::1',55432,NAME,''),('production.neon.tech',55432,NAME,''),
      ('staging.neon.tech',55432,NAME,''),('127.0.0.1',55432,'stockbridge-staging',''),('127.0.0.1',55432,NAME,'?host=neon')]],
    ('STOCKBRIDGE_TEST_POSTGRES_URL',f'postgresql+psycopg://postgres:synthetic@127.0.0.1:55432/{NAME}')])
def test_url_protections(key,value):
    env=configuration();env[key]=value
    with pytest.raises(UnsafeTestDatabase) as error: validated_test_url(env)
    assert value not in str(error.value) if value else True


@pytest.mark.parametrize('key,value',[
    ('os','windows'),('distribution','debian'),('version_major',17),('cluster','16/main'),
    ('directory','/var/lib/postgresql/16/main'),('port',5432),('process',None),('postgres_uid',999),
    ('postmaster_started',None),('sockets',[]),('sockets',None),('commandline',[ubuntu.EXECUTABLE]),
    ('commandline',[ubuntu.EXECUTABLE,'-D','/var/lib/postgresql/16/main'])])
def test_os_fail_closed(key,value):
    data=evidence();data[key]=value
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_os(data)


@pytest.mark.parametrize('key,value',[('executable','/usr/bin/ssh'),('uid',0),('pid',None),('start_ticks',0),('parent_pid',None)])
def test_process_identity_fail_closed(key,value):
    data=evidence();data['process'][key]=value
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_os(data)


@pytest.mark.parametrize('socket',[
    dict(protocol='TCP',address='0.0.0.0',port=55432,pids=[5000]),
    dict(protocol='TCP',address='::',port=55432,pids=[5000]),
    dict(protocol='TCP',address='192.168.1.2',port=55432,pids=[5000]),
    dict(protocol='TCP',address='127.0.0.1',port=55432,pids=[999]),
    dict(protocol='TCP',address='127.0.0.1',port=55432,pids=[5000,999]),
    dict(protocol='TCP',address='127.0.0.1',port=55432,pids=[]),
    dict(protocol='TCP',address='127.0.0.1',port=55433,pids=[5000]),
    dict(protocol='UDP',address='127.0.0.1',port=55432,pids=[5000]),
    dict(protocol='unknown',address='127.0.0.1',port=55432,pids=[5000]),{}])
def test_extra_listeners_rejected(socket):
    data=evidence();data['sockets'].append(socket)
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_os(data)


def test_other_cluster_not_selected_or_modified():
    data=evidence();data['sockets'].append(dict(protocol='TCP',address='127.0.0.1',port=5432,pids=[123]))
    assert ubuntu.validate_ubuntu_os(data)


@pytest.mark.parametrize('key,value',[
    ('database','stockbridge-staging'),('owner','postgres'),('user','postgres'),('session_user','postgres'),
    ('port',5432),('version',170000),('address','10.0.0.1'),('data_directory','/var/lib/postgresql/16/main'),
    ('database_oid',None),('role_oid',0),('memberships',['pg_read_all_data']),('owned_other_databases',['customer']),
    ('schemas',['public','pgx_hidden']),('extensions',['plpgsql','dblink']),('tables',['public.user']),
    ('all_relations',['public.custom_type']),('revision','0017_transaction_submission'),
    ('listen_addresses','*'),('hba_rules',[]),('transport_ssl',None)])
def test_database_fail_closed(key,value):
    data=snapshot();data[key]=value
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN)


@pytest.mark.parametrize('key',['superuser','createdb','createrole','replication','bypassrls'])
def test_admin_privileges_rejected(key):
    data=snapshot();data['role'][key]=True
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN)


@pytest.mark.parametrize('method',['trust','peer','password','md5','unknown'])
def test_password_method_rejected(method):
    data=snapshot();data['hba_rules'][0]['auth_method']=method
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN)


def test_hba_first_match_and_ssl_rules():
    data=snapshot();unsafe=deepcopy(data['hba_rules'][0]);unsafe['auth_method']='trust'
    data['hba_rules'][0]['rule_number']=2
    data['hba_rules'].insert(0,unsafe)
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN)
    unsafe['type']='hostssl'
    assert ubuntu.validate_ubuntu_database(data,RUN)  # Cannot apply to this non-SSL connection.


def test_migration_exact_revision_and_relations():
    data=snapshot();tables=['public.alembic_version','public.transaction_submission']
    data.update(tables=tables,all_relations=tables,revision='0017_transaction_submission')
    assert ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables,(1000,1001))
    data['revision']='0016_inventory_email_outbox'
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables)
    data['revision']='0017_transaction_submission';data['all_relations']=tables+['public.unexpected']
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables)
    with pytest.raises(UnsafeTestDatabase): ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables,(1000,999))


def test_restart_and_backend_linkage():
    first=evidence();ubuntu.same_instance(first,deepcopy(first))
    second=deepcopy(first);second['process']['start_ticks']+=1
    with pytest.raises(UnsafeTestDatabase): ubuntu.same_instance(first,second)
    backend=dict(pid=5001,parent_pid=5000,start_ticks=13000,uid=110,executable=ubuntu.EXECUTABLE)
    ubuntu.validate_backend(snapshot(),first,backend)
    for key,value in [('parent_pid',999),('executable','/usr/bin/ssh'),('uid',0),('pid',100)]:
        bad={**backend,key:value}
        with pytest.raises(UnsafeTestDatabase): ubuntu.validate_backend(snapshot(),first,bad)


def test_os_rejection_before_connect():
    with patch.object(ubuntu,'ubuntu_evidence',return_value={}),patch('psycopg.connect') as connect:
        with pytest.raises(UnsafeTestDatabase): preflight(configuration())
        connect.assert_not_called()


def test_missing_metadata_safe_error_and_read_only():
    conn=MagicMock();conn.execute.return_value.fetchone.return_value=('on',)
    connect=MagicMock();connect.return_value.__enter__.return_value=conn
    with patch.object(ubuntu,'ubuntu_evidence',return_value=evidence()),patch('psycopg.connect',connect),\
         patch('tests.support.postgres_preflight.database_snapshot',side_effect=PermissionError('synthetic-private-value')):
        with pytest.raises(UnsafeTestDatabase) as error: preflight(configuration())
    assert 'synthetic-private-value' not in str(error.value)
    assert conn.execute.call_args_list[0].args==('SET TRANSACTION READ ONLY',)
    assert 'default_transaction_read_only=on' in connect.call_args.kwargs['options']


def test_read_only_success_mocked():
    conn=MagicMock();conn.execute.return_value.fetchone.side_effect=[('on',),(5001,),(False,)]
    conn.execute.return_value.__iter__.return_value=iter([])
    connect=MagicMock();connect.return_value.__enter__.return_value=conn
    backend=dict(pid=5001,parent_pid=5000,start_ticks=13000,uid=110,executable=ubuntu.EXECUTABLE)
    with patch.object(ubuntu,'ubuntu_evidence',return_value=evidence()),patch('psycopg.connect',connect),\
         patch('tests.support.postgres_preflight.database_snapshot',return_value=snapshot()),patch.object(ubuntu,'process_identity',return_value=backend):
        # Database metadata queries return no custom schemas unless explicitly supplied below.
        def execute(sql,*args,**kwargs):
            result=MagicMock()
            if sql=='SHOW transaction_read_only':result.fetchone.return_value=('on',)
            elif sql=='SELECT pg_backend_pid()':result.fetchone.return_value=(5001,)
            elif sql.startswith('SELECT ssl'):result.fetchone.return_value=(False,)
            elif sql.startswith('SELECT nspname'):result.__iter__.return_value=iter([('public',)])
            else:result.__iter__.return_value=iter([])
            return result
        conn.execute.side_effect=execute
        result=preflight(configuration())
    assert result['status']=='PASS' and result['database_oid']==1000
    assert result['server_major']==16
    assert all(call.args[0].startswith(('SELECT','SET TRANSACTION','SHOW')) for call in conn.execute.call_args_list)
    assert '-c lock_timeout=2000' in connect.call_args.kwargs['options']


def test_collector_uses_fixed_read_only_tools_and_no_shell():
    with patch.object(ubuntu.subprocess,'run',return_value=MagicMock(returncode=0,stdout='synthetic',stderr='')) as run:
        assert ubuntu.command(['/usr/bin/ss','-H','-ltnp'])=='synthetic'
    assert run.call_args.kwargs['timeout']==10 and 'shell' not in run.call_args.kwargs
    with patch.object(ubuntu.subprocess,'run',side_effect=subprocess.TimeoutExpired('ss',10)):
        with pytest.raises(UnsafeTestDatabase):ubuntu.command(['/usr/bin/ss','-H','-ltnp'])


def test_socket_parser_and_missing_process_identity():
    row='LISTEN 0 244 127.0.0.1:55432 0.0.0.0:* users:(("postgres",pid=5000,fd=5))'
    assert ubuntu.parse_sockets(row,'TCP')[0]['pids']==[5000]
    assert ubuntu.parse_sockets('LISTEN 0 244 [::1]:55432 [::]:*','TCP')[0]['pids']==[]
    with pytest.raises(UnsafeTestDatabase):ubuntu.parse_sockets('invalid','TCP')


def test_no_mutation_authorization_or_manifest_generated():
    env=configuration()
    assert 'STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK' not in env
    assert 'STOCKBRIDGE_TEST_POSTGRES_MANIFEST' not in env
    with patch('tests.support.postgres_preflight.windows_evidence') as windows,patch.object(ubuntu,'ubuntu_evidence',return_value={}),patch('psycopg.connect') as connect:
        with pytest.raises(UnsafeTestDatabase):preflight(env)
        windows.assert_not_called();connect.assert_not_called()


@pytest.mark.parametrize('failure', [None,'os','cluster','directory','pidfile','permission','missing-tool','missing-process'])
def test_collector_with_mocked_ubuntu_os(failure):
    from types import SimpleNamespace
    files={
        '/etc/os-release':'NAME="Ubuntu"\nID=ubuntu\n',
        ubuntu.DIRECTORY+'/postmaster.pid':'5000\n'+ubuntu.DIRECTORY+'\n1720000000\n55432\n',
        '/proc/5000/cmdline':ubuntu.EXECUTABLE+'\0-D\0'+ubuntu.DIRECTORY+'\0',
    }
    cluster='16 stockbridge-test 55432 online postgres '+ubuntu.DIRECTORY+' /var/log/postgresql/test.log'
    socket='LISTEN 0 244 127.0.0.1:55432 0.0.0.0:* users:(("postgres",pid=5000,fd=5))'
    if failure=='os':files['/etc/os-release']='ID=debian'
    if failure=='cluster':cluster=cluster.replace('stockbridge-test','main')
    if failure=='pidfile':files[ubuntu.DIRECTORY+'/postmaster.pid']='ambiguous'
    def command(args):
        if failure=='missing-tool':ubuntu.reject('bounded OS inspection unavailable')
        if args[0]=='/usr/bin/pg_lsclusters':return cluster
        return socket if '-ltnp' in args else ''
    def read(path):
        if failure=='permission':ubuntu.reject('OS metadata unavailable')
        return files[path]
    def process(pid):
        if failure=='missing-process':ubuntu.reject('process identity unavailable')
        return evidence()['process']
    with patch.object(ubuntu.sys,'platform','linux'),patch.object(ubuntu,'command',side_effect=command), \
         patch.object(ubuntu,'read_text',side_effect=read),patch.object(ubuntu,'process_identity',side_effect=process), \
         patch.dict(sys.modules,{'pwd':SimpleNamespace(getpwnam=lambda name:SimpleNamespace(pw_uid=110))}), \
         patch.object(ubuntu.Path,'resolve',return_value='/var/lib/postgresql/16/main' if failure=='directory' else ubuntu.DIRECTORY):
        if failure:
            with pytest.raises(UnsafeTestDatabase):ubuntu.ubuntu_evidence()
        else:
            assert ubuntu.validate_ubuntu_os(ubuntu.ubuntu_evidence())==ubuntu.DIRECTORY


def test_collector_refuses_non_linux_before_inspection():
    with patch.object(ubuntu.sys,'platform','win32'),patch.object(ubuntu,'command') as command:
        with pytest.raises(UnsafeTestDatabase):ubuntu.ubuntu_evidence()
        command.assert_not_called()


@pytest.mark.parametrize('problem',['returncode','stderr','timeout','decode'])
def test_collector_errors_never_expose_command_output(problem):
    result=MagicMock(returncode=1 if problem=='returncode' else 0,stdout='synthetic-private-value',
                     stderr='synthetic-private-value' if problem=='stderr' else '')
    error=subprocess.TimeoutExpired('synthetic-private-value',10) if problem=='timeout' else UnicodeError('synthetic-private-value')
    with patch.object(ubuntu.subprocess,'run',return_value=result,side_effect=error if problem in ('timeout','decode') else None):
        with pytest.raises(UnsafeTestDatabase) as rejected:ubuntu.command(['/usr/bin/ss','-H','-ltnp'])
    assert 'synthetic-private-value' not in str(rejected.value)


def test_migrated_indexes_must_belong_to_approved_tables():
    data=snapshot();tables=['public.alembic_version','public.transaction_submission']
    data.update(tables=tables,all_relations=tables+['public.submission_index'],
                indexes=[{'name':'public.submission_index','table':'public.transaction_submission'}],revision='0017_transaction_submission')
    assert ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables)
    data['indexes'][0]['table']='public.unapproved'
    with pytest.raises(UnsafeTestDatabase):ubuntu.validate_ubuntu_database(data,RUN,'migrated',tables)


@pytest.mark.parametrize('value,expected',[(1,1),(4294967295,4294967295),('1',1),('4294967295',4294967295)])
def test_oid_normalization_valid(value,expected):
    assert ubuntu.normalized_oid(value)==expected


@pytest.mark.parametrize('value',[None,True,False,0,-1,4294967296,1.0,'','0','-1','+1','01',' 1','1 ',
    '1.0','1e3','4294967296','١','１',[],{},'9'*1000])
def test_oid_normalization_rejects_invalid(value):
    with pytest.raises(UnsafeTestDatabase,match='OID invalid'):
        ubuntu.normalized_oid(value)


@pytest.mark.parametrize('key',['database_oid','role_oid'])
@pytest.mark.parametrize('side',['protected','direct'])
@pytest.mark.parametrize('value',[None,True,-1,'01',4294967296])
def test_scoped_oid_invalid_on_either_side(key,side,value):
    protected=dict(database_oid='1000',role_oid='1001')
    direct=dict(database_oid=1000,role_oid=1001)
    (protected if side=='protected' else direct)[key]=value
    with pytest.raises(UnsafeTestDatabase):ubuntu.validate_scoped_oids(protected,direct)


@pytest.mark.parametrize('key',['database_oid','role_oid'])
def test_scoped_oid_genuine_mismatch(key):
    protected=dict(database_oid='1000',role_oid='1001')
    direct=dict(database_oid=1000,role_oid=1001);direct[key]+=1
    with pytest.raises(UnsafeTestDatabase,match='scoped SQL evidence OIDs mismatch'):
        ubuntu.validate_scoped_oids(protected,direct)


@pytest.mark.parametrize('protected,direct',[
    (dict(database_oid='1000',role_oid='1001'),dict(database_oid=1000,role_oid=1001)),
    (dict(database_oid=1000,role_oid=1001),dict(database_oid=1000,role_oid=1001)),
    (dict(database_oid=1000,role_oid='1001'),dict(database_oid='1000',role_oid=1001))])
def test_scoped_oid_valid_representations(protected,direct):
    ubuntu.validate_scoped_oids(protected,direct)


def test_direct_database_oid_out_of_range_rejected():
    data=snapshot();data['database_oid']=4294967296
    with pytest.raises(UnsafeTestDatabase):ubuntu.validate_ubuntu_database(data,RUN)
