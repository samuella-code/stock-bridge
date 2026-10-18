"""Ubuntu preflight validators and explicit read-only evidence selection."""
from pathlib import Path
import re
import subprocess
import sys
from tests.support.isolated_postgres import UnsafeTestDatabase
from tests.support import ubuntu_os_evidence as os_collector

DIRECTORY=os_collector.DIRECTORY
EXECUTABLE=os_collector.EXECUTABLE
PORT=os_collector.PORT
TIMEOUT=os_collector.TIMEOUT


def reject(category):
    raise UnsafeTestDatabase('PostgreSQL preflight rejected: Ubuntu ' + category)


def wrapped(function, *args, **kwargs):
    try: return function(*args, **kwargs)
    except os_collector.EvidenceError as error: raise UnsafeTestDatabase(str(error)) from None


def read_text(path): return wrapped(os_collector.read_text,path)
def command(args): return wrapped(os_collector.command,args)
def process_identity(pid): return wrapped(os_collector.process_identity,pid)
def parse_sockets(output,protocol): return wrapped(os_collector.parse_sockets,output,protocol)
def ubuntu_evidence():
    return wrapped(os_collector.collect,read_text=read_text,command=command,process_identity=process_identity)


def collect_evidence(environ, backend_pid=None):
    if sys.platform!='linux': reject('Linux operating system required')
    method=environ.get('STOCKBRIDGE_TEST_POSTGRES_OS_EVIDENCE','direct')
    if method=='direct':
        evidence=ubuntu_evidence()
        return evidence, process_identity(backend_pid) if backend_pid is not None else None
    if method!='privileged-socket' or environ.get('STOCKBRIDGE_TEST_POSTGRES_HELPER_ACK')!='approved-local-read-only-helper':
        reject('OS evidence method not explicitly authorized')
    from tests.support.ubuntu_evidence_helper import request_evidence
    try: return request_evidence(backend_pid)
    except os_collector.EvidenceError as error: raise UnsafeTestDatabase(str(error)) from None


def validate_ubuntu_os(evidence):
    if not isinstance(evidence, dict): reject('OS evidence missing')
    if (evidence.get('os')!='linux' or evidence.get('distribution')!='ubuntu'
            or evidence.get('cluster')!='16/stockbridge-test' or evidence.get('version_major')!=16
            or evidence.get('directory')!=DIRECTORY or evidence.get('port')!=PORT):
        reject('cluster/OS identity mismatch')
    process=evidence.get('process')
    if (not isinstance(process,dict) or process.get('executable')!=EXECUTABLE
            or any(type(process.get(k)) is not int or process[k]<=0 for k in ('pid','start_ticks','uid'))
            or type(process.get('parent_pid')) is not int or process['parent_pid']<0
            or type(evidence.get('postgres_uid')) is not int or process['uid']!=evidence['postgres_uid']
            or type(evidence.get('postmaster_started')) is not int or evidence['postmaster_started']<=0):
        reject('executable/process identity mismatch')
    args=evidence.get('commandline')
    if (not isinstance(args,list) or not args or args[0]!=EXECUTABLE or args.count('-D')!=1
            or args.index('-D')+1>=len(args) or args[args.index('-D')+1]!=DIRECTORY):
        reject('postmaster command identity mismatch')
    sockets=evidence.get('sockets')
    if not isinstance(sockets,list) or not sockets: reject('socket evidence missing')
    listeners=[]
    for socket in sockets:
        if (not isinstance(socket,dict) or socket.get('protocol') not in ('TCP','UDP')
                or type(socket.get('port')) is not int or not 1<=socket['port']<=65535
                or not isinstance(socket.get('address'),str) or not isinstance(socket.get('pids'),list)
                or any(type(pid) is not int or pid<=0 for pid in socket['pids'])):
            reject('socket evidence ambiguous')
        relevant=socket['port']==PORT or process['pid'] in socket['pids']
        if not relevant: continue  # Other clusters are not touched or treated as this instance.
        if (socket['protocol']!='TCP' or socket.get('state')!='LISTEN' or socket['port']!=PORT or socket['address'] not in ('127.0.0.1','::1')
                or socket['pids']!=[process['pid']]):
            reject('unexpected listener or network exposure')
        listeners.append(socket)
    if not listeners or not any(s['address']=='127.0.0.1' for s in listeners):
        reject('loopback listener identity incomplete')
    return DIRECTORY


def validate_backend(snapshot, evidence, backend):
    process=evidence['process']
    if (not isinstance(backend,dict) or backend.get('pid')!=snapshot.get('backend_pid')
            or backend.get('parent_pid')!=process['pid'] or backend.get('executable')!=EXECUTABLE
            or backend.get('uid')!=process['uid'] or type(backend.get('start_ticks')) is not int
            or backend['start_ticks']<=0): reject('connected backend does not belong to verified cluster')


def same_instance(first, second):
    validate_ubuntu_os(second)
    for key in ('cluster','directory','port','postmaster_started','postgres_uid','process','commandline'):
        if first.get(key)!=second.get(key): reject('cluster changed during read-only inspection')
    def listeners(data):
        return sorted((s['protocol'],s['address'],s['port'],tuple(s['pids'])) for s in data['sockets'] if s['port']==PORT or data['process']['pid'] in s['pids'])
    if listeners(first)!=listeners(second): reject('cluster listeners changed during read-only inspection')



def validate_hba(snapshot, name):
    import ipaddress
    rules=snapshot.get('hba_rules')
    ssl=snapshot.get('transport_ssl')
    if not isinstance(rules,list) or not rules or type(ssl) is not bool: reject('authentication evidence missing')
    numbers=[r.get('rule_number') if isinstance(r,dict) else None for r in rules]
    if any(type(n) is not int or n<=0 for n in numbers) or numbers!=sorted(set(numbers)):
        reject('authentication ordering ambiguous')
    effective=None
    for rule in rules:
        if (rule.get('error') or rule.get('type') not in ('local','host','hostssl','hostnossl')
                or rule.get('simple_syntax') is not True or rule.get('has_options') is not False):
            reject('authentication syntax requires manual review')
        if rule['type']=='local' or rule.get('database')==['replication']: continue
        if rule['type']=='hostssl' and not ssl or rule['type']=='hostnossl' and ssl: continue
        def selector(values, database=False):
            if not isinstance(values,list) or not values: reject('authentication selectors missing')
            matches=False
            for value in values:
                if not isinstance(value,str): reject('authentication selector ambiguous')
                if value=='all' or value==name: matches=True
                elif database and value in ('sameuser','samerole','samegroup'): matches=True  # user and database match; membership list is empty.
                elif not database and value.startswith('+') and value[1:]==name: matches=True
                elif not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*',value): reject('authentication selector unsupported')
            return matches
        database_matches=selector(rule.get('database'),True)
        user_matches=selector(rule.get('user'))
        try:
            address=ipaddress.ip_address(rule['address'])
            mask=ipaddress.ip_address(rule['netmask'])
            if address.version!=mask.version: raise ValueError()
            bits=format(int(mask), '0'+str(mask.max_prefixlen)+'b')
            if '01' in bits: raise ValueError()
            network=ipaddress.ip_network(str(address)+'/'+str(bits.count('1')),strict=False)
        except (KeyError,TypeError,ValueError): reject('authentication address unsupported')
        if network.version==4 and ipaddress.ip_address('127.0.0.1') in network and database_matches and user_matches:
            effective=rule;break
    if not effective or effective.get('auth_method')!='scram-sha-256': reject('SCRAM password authentication not established')


def validate_ubuntu_database(snapshot, run_id, phase='empty', expected_tables=(), expected_identity=None):
    if not isinstance(snapshot,dict) or not isinstance(run_id,str) or not re.fullmatch(r'[a-f0-9]{16}',run_id): reject('database evidence missing')
    name='stockbridge_test_'+run_id
    if (any(snapshot.get(key)!=name for key in ('database','user','session_user','owner'))
            or snapshot.get('address')!='127.0.0.1' or snapshot.get('port')!=PORT
            or type(snapshot.get('version')) is not int or not 160000<=snapshot['version']<170000
            or snapshot.get('data_directory')!=DIRECTORY): reject('database/role/server identity mismatch')
    oids=(snapshot.get('database_oid'),snapshot.get('role_oid'))
    if any(type(oid) is not int or oid<=0 for oid in oids): reject('database/role OIDs missing')
    if expected_identity is not None and oids!=expected_identity: reject('approved database/role OID mismatch')
    role=snapshot.get('role')
    if (not isinstance(role,dict) or set(role)!={'superuser','createdb','createrole','replication','bypassrls','login'}
            or role['login'] is not True or any(role[k] is not False for k in role if k!='login')
            or snapshot.get('memberships')!=[] or snapshot.get('owned_other_databases')!=[]):
        reject('role privileges, memberships or ownership unsafe')
    schemas=['public','stockbridge_preflight'] if snapshot.get('protected_schema_verified') is True else ['public']
    if snapshot.get('schemas')!=schemas or snapshot.get('extensions')!=['plpgsql']:
        reject('unexpected schema or extension')
    listening=snapshot.get('listen_addresses')
    if not isinstance(listening,str) or not listening or any(a.strip() not in ('localhost','127.0.0.1','::1') for a in listening.split(',')):
        reject('listening settings not loopback-only')
    rules=snapshot.get('hba_rules')
    ssl=snapshot.get('transport_ssl')
    if not isinstance(rules,list) or not rules or type(ssl) is not bool: reject('authentication evidence missing')
    validate_hba(snapshot, name)
    if phase=='empty':
        if snapshot.get('tables')!=[] or snapshot.get('all_relations')!=[] or snapshot.get('revision') is not None:
            reject('database is not empty')
    elif phase=='migrated':
        if (not expected_tables or not isinstance(snapshot.get('tables'),list)
                or set(snapshot['tables'])!=set(expected_tables) or snapshot.get('revision')!='0017_transaction_submission'):
            reject('migration/table identity mismatch')
        indexes=snapshot.get('indexes')
        if (not isinstance(indexes,list) or any(not isinstance(i,dict) or i.get('table') not in expected_tables or not isinstance(i.get('name'),str) for i in indexes)
                or not isinstance(snapshot.get('all_relations'),list)
                or set(snapshot['all_relations'])!=set(expected_tables)|{i['name'] for i in indexes}):
            reject('unexpected migrated relation')
    else: reject('unknown schema phase')
    return True


def ubuntu_preflight(environ, url, phase='empty', expected_tables=(), expected_identity=None):
    from tests.support.postgres_preflight import database_snapshot
    evidence,_=collect_evidence(environ)
    validate_ubuntu_os(evidence)  # Every OS gate runs before connection.
    import psycopg
    if psycopg.pq.version()<160000: reject('libpq 16 or newer required for enforced SCRAM')
    try:
        with psycopg.connect(host=url.host,port=url.port,dbname=url.database,user=url.username,password=url.password,
                connect_timeout=5,require_auth='scram-sha-256',
                options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000 -c search_path=pg_catalog') as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                if conn.execute('SHOW transaction_read_only').fetchone()[0]!='on': reject('read-only transaction not established')
                method=environ.get('STOCKBRIDGE_TEST_POSTGRES_DB_EVIDENCE','direct')
                protected=None
                if method=='scoped-function':
                    if environ.get('STOCKBRIDGE_TEST_POSTGRES_SQL_EVIDENCE_ACK')!='approved-isolated-read-only-function':
                        reject('scoped SQL evidence not explicitly authorized')
                    from tests.support.ubuntu_database_evidence import read_scoped_evidence
                    protected=read_scoped_evidence(conn)
                elif method!='direct': reject('database evidence method unsupported')
                snapshot=database_snapshot(conn,protected_evidence=protected) if protected is not None else database_snapshot(conn)
                if protected is not None:
                    if (protected.get('database_oid'),protected.get('role_oid'))!=(snapshot['database_oid'],snapshot['role_oid']):
                        reject('scoped SQL evidence OIDs mismatch')
                    snapshot['protected_schema_verified']=True
                else: snapshot['protected_schema_verified']=False
                snapshot['schemas']=[r[0] for r in conn.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','pg_toast','information_schema') ORDER BY nspname")]
                snapshot['tables']=[r[0] for r in conn.execute("""SELECT n.nspname||'.'||c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','pg_toast','information_schema')
                    AND c.relkind IN ('r','p','v','m','f','S') ORDER BY 1""")]
                snapshot['indexes']=[{'name':r[0],'table':r[1]} for r in conn.execute("""SELECT ni.nspname||'.'||ci.relname, nt.nspname||'.'||ct.relname
                    FROM pg_index ix JOIN pg_class ci ON ci.oid=ix.indexrelid JOIN pg_namespace ni ON ni.oid=ci.relnamespace
                    JOIN pg_class ct ON ct.oid=ix.indrelid JOIN pg_namespace nt ON nt.oid=ct.relnamespace
                    WHERE ni.nspname NOT IN ('pg_catalog','pg_toast','information_schema') ORDER BY 1""")]
                snapshot['backend_pid']=conn.execute('SELECT pg_backend_pid()').fetchone()[0]
                ssl=conn.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()
                snapshot['transport_ssl']=ssl[0] if ssl else None
                snapshot['all_relations']=[r[0] for r in conn.execute("""SELECT n.nspname||'.'||c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','pg_toast','information_schema') ORDER BY 1""")]
                validate_ubuntu_database(snapshot,environ['STOCKBRIDGE_TEST_POSTGRES_RUN_ID'],phase,expected_tables,expected_identity)
                fresh,backend=collect_evidence(environ,snapshot['backend_pid'])
                validate_backend(snapshot,fresh,backend)
                same_instance(evidence,fresh)
        return {'status':'PASS','phase':phase,'database':url.database,'server_major':16,
                'database_oid':snapshot['database_oid'],'role_oid':snapshot['role_oid']}
    except UnsafeTestDatabase: raise
    except Exception: reject('read-only metadata unavailable; no privilege escalation attempted')
