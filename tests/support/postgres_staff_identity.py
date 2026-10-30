"""Staff0017/0018 identity adapter; shared collectors remain byte-for-byte unchanged.

Identity/HBA/privilege rules mirror the existing validator. Only schema-stage
selection is Staff-specific; no snapshot revision is rewritten or hidden.
"""
from tests.support.postgres_staff_guard import RUN, V17, V18, target_url
from tests.support.ubuntu_postgres_identity import (reject, normalized_oid, validate_hba,
    collect_evidence, validate_ubuntu_os, validate_backend, same_instance, validate_scoped_oids,
    DIRECTORY, PORT)
from tests.support.isolated_postgres import UnsafeTestDatabase
import re

def validate_database(snapshot, phase, expected_tables, expected_identity):
    run_id=RUN
    if phase not in ('empty', V17, V18): reject('staff schema phase invalid')
    if not isinstance(snapshot,dict) or not isinstance(run_id,str) or not re.fullmatch(r'[a-f0-9]{16}',run_id): reject('database evidence missing')
    name='stockbridge_test_'+run_id
    if (any(snapshot.get(key)!=name for key in ('database','user','session_user','owner'))
            or snapshot.get('address')!='127.0.0.1' or snapshot.get('port')!=PORT
            or type(snapshot.get('version')) is not int or not 160000<=snapshot['version']<170000
            or snapshot.get('data_directory')!=DIRECTORY): reject('database/role/server identity mismatch')
    oids=(snapshot.get('database_oid'),snapshot.get('role_oid'))
    if any(type(oid) is not int or oid<=0 for oid in oids): reject('database/role OIDs missing')
    for oid in oids: normalized_oid(oid)
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
    elif phase in (V17,V18):
        if (not expected_tables or not isinstance(snapshot.get('tables'),list)
                or set(snapshot['tables'])!=set(expected_tables) or snapshot.get('revision')!=phase):
            reject('migration/table identity mismatch')
        indexes=snapshot.get('indexes')
        if (not isinstance(indexes,list) or any(not isinstance(i,dict) or i.get('table') not in expected_tables or not isinstance(i.get('name'),str) for i in indexes)
                or not isinstance(snapshot.get('all_relations'),list)
                or set(snapshot['all_relations'])!=set(expected_tables)|{i['name'] for i in indexes}):
            reject('unexpected migrated relation')
    else: reject('unknown schema phase')
    return True


def preflight(environ, *, phase, expected_tables, expected_identity):
    url=target_url(environ)
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
                    from tests.support.postgres_staff_evidence import read_scoped_evidence
                    protected=read_scoped_evidence(conn)
                elif method!='direct': reject('database evidence method unsupported')
                snapshot=database_snapshot(conn,protected_evidence=protected) if protected is not None else database_snapshot(conn)
                if protected is not None:
                    validate_scoped_oids(protected,snapshot)
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
                validate_database(snapshot,phase,expected_tables,expected_identity)
                fresh,backend=collect_evidence(environ,snapshot['backend_pid'])
                validate_backend(snapshot,fresh,backend)
                same_instance(evidence,fresh)
        return {'status':'PASS','phase':phase,'database':url.database,'server_major':16,
                'database_oid':snapshot['database_oid'],'role_oid':snapshot['role_oid']}
    except UnsafeTestDatabase: raise
    except Exception: reject('read-only metadata unavailable; no privilege escalation attempted')
