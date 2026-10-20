"""Explicit read-only preflight. Importing this module never connects.

This releases no mutation capability. Missing OS/config evidence fails closed.
"""
import json
import os
from pathlib import Path
import re
import subprocess
from tests.support.isolated_postgres import validated_test_url, UnsafeTestDatabase


def reject(category):
    raise UnsafeTestDatabase('PostgreSQL preflight rejected: ' + category)


def validate_network(evidence):
    addresses=evidence.get('listener_addresses', [])
    if not addresses:reject('listener address evidence missing')
    if all(a in ('127.0.0.1','::1') for a in addresses):return True
    if any(a not in ('127.0.0.1','::1','0.0.0.0','::') for a in addresses):
        reject('unexpected listener binding')
    profiles=evidence.get('firewall_profiles')
    rules=evidence.get('inbound_allow_rules')
    if (not isinstance(profiles,list) or {p.get('Name') for p in profiles}!={'Domain','Private','Public'}
            or any(p.get('Enabled') is not True or p.get('DefaultInboundAction') not in ('Block',4) for p in profiles)
            or not isinstance(rules,list)):
        reject('effective firewall evidence incomplete')
    import ipaddress
    for rule in rules:
        protocol=rule.get('protocol')
        # Exact observed IP protocols only. Encapsulated IPv6 does not grant
        # TCP permission: any resulting TCP traffic must still pass TCP rules.
        if protocol in ('UDP','17','2','41'):continue
        if protocol not in ('TCP','6','Any','256'):reject('firewall protocol requires manual review')
        program=rule.get('program','').lower()
        if program not in ('any','system') and not program.endswith('\\postgres.exe'):continue
        ports=rule.get('ports',[])
        if not ports:reject('firewall ports unavailable')
        relevant=False
        for port in ports:
            port=str(port)
            if port=='Any' or port=='5432':relevant=True
            elif re.fullmatch(r'[0-9]+-[0-9]+',port):
                low,high=map(int,port.split('-'));relevant |= low<=5432<=high
            elif not port.isdigit():reject('firewall port requires manual review')
        if not relevant:continue
        # A specific package SID is an exclusion only for a runtime-verified
        # unpackaged, non-AppContainer listener. Unknown evidence never excludes.
        package=rule.get('package')
        if package not in (None, '', 'Any'):
            if not isinstance(package,str) or not re.fullmatch(r'S-1-15-2-(?:[0-9]+-){6}[0-9]+',package):
                reject('unsupported firewall package restriction')
            if any(int(part)>4294967295 for part in package.split('-')[4:]):
                reject('unsupported firewall package restriction')
            identity=evidence.get('listener_package_identity',{})
            if (not isinstance(identity,dict) or identity.get('method')!='win32-process-and-token'
                    or identity.get('status')!='unpackaged-non-appcontainer'
                    or type(identity.get('pid')) is not int
                    or identity['pid']!=evidence.get('listener_pid')):
                reject('listener package identity not independently verified')
            continue
        remote=rule.get('remote',[])
        if not remote:reject('firewall remote address unavailable')
        for address in remote:
            try:network=ipaddress.ip_network(address,strict=False)
            except ValueError:reject('inbound PostgreSQL exposure requires manual review')
            loopback=ipaddress.ip_network('127.0.0.0/8' if network.version==4 else '::1/128')
            if not network.subnet_of(loopback):reject('inbound PostgreSQL exposure not confined to loopback')
    return True


def validate_os(evidence):
    expected = 'c:/program files/postgresql/17/bin/'.replace('/', chr(92))
    for key, filename in [('listener_executable', 'postgres.exe'), ('service_executable', 'pg_ctl.exe')]:
        value = evidence.get(key, '')
        if not isinstance(value,str) or value.lower() != expected + filename:
            reject('Windows '+key+' missing or unexpected')
    if (evidence.get('service_name') != 'postgresql-x64-17'
            or evidence.get('service_state') != 'Running'
            or evidence.get('listener_name') != 'postgres.exe'
            or evidence.get('listener_parent_pid') != evidence.get('service_pid')
            or not isinstance(evidence.get('listener_pid'),int)
            or evidence['listener_pid'] <= 0):
        reject('Windows service/listener relationship missing or unexpected')
    service_path = evidence.get('service_path','')
    match = re.search(r'-D\s+"([^"]+)"', service_path)
    if not match or not service_path.lower().startswith('"' + expected + 'pg_ctl.exe"'):
        reject('Windows service data directory unavailable')
    validate_network(evidence)
    return match.group(1).rstrip('\\/').lower()


def validate_database(snapshot, expected_directory, phase='empty', expected_tables=(), network_verified=False, expected_identity=None):
    if phase not in ('empty','migrated'):
        reject('unknown schema phase')
    if expected_identity is not None and (snapshot.get('database_oid'),snapshot.get('role_oid')) != expected_identity:
        reject('approved database/role OID mismatch')
    expected_role='stockbridge_test_user'
    if (snapshot.get('database') != 'stockbridge_disposable_test'
            or snapshot.get('user') != expected_role or snapshot.get('session_user') != expected_role
            or snapshot.get('owner') != expected_role
            or snapshot.get('address') != '127.0.0.1' or snapshot.get('port') != 5432
            or not 170000 <= snapshot.get('version',0) < 180000):
        reject('database/role/server identity')
    role = snapshot.get('role', {})
    if (set(role) != {'superuser','createdb','createrole','replication','bypassrls','login'}
            or role['login'] is not True or any(role[k] is not False for k in role if k!='login')
            or snapshot.get('memberships') != []):
        reject('role privileges or memberships')
    if snapshot.get('owned_other_databases') != []:
        reject('role owns unrelated databases')
    if snapshot.get('schemas') != ['public'] or snapshot.get('extensions') != ['plpgsql']:
        reject('unexpected schema or extension')
    directory=snapshot.get('data_directory','').rstrip('\\/').lower()
    if directory != expected_directory:
        reject('server directory does not match Windows service')
    if snapshot.get('listen_addresses') not in ('127.0.0.1','localhost') and not (snapshot.get('listen_addresses')=='*' and network_verified):
        reject('network exposure not independently verified')
    rules=snapshot.get('hba_rules')
    if not isinstance(rules,list) or not rules:
        reject('authentication rules unavailable')
    # Conservative subset; complex include/group/hostname rules need review.
    import ipaddress
    matched=False
    for rule in rules:
        if rule.get('error') or rule.get('type') not in ('local','host','hostssl','hostnossl'):
            reject('authentication rules require manual review')
        if rule['type']=='local' or rule.get('database')==['replication']: continue
        if rule.get('database') not in (['all'],['stockbridge_disposable_test']) or rule.get('user') not in (['all'],[expected_role]):
            reject('authentication selectors require manual review')
        try:
            network=ipaddress.ip_network(f"{rule['address']}/{rule['netmask']}",strict=False)
        except (ValueError,KeyError):reject('authentication address requires manual review')
        if ipaddress.ip_address('127.0.0.1') in network:
            if rule.get('auth_method') not in ('scram-sha-256','md5','password'):
                reject('password authentication not established')
            matched=True
    if not matched:reject('no verified loopback authentication rule')
    tables=snapshot.get('tables')
    if phase=='empty':
        if tables != [] or snapshot.get('revision') is not None:
            reject('database is not empty')
    elif not expected_tables or set(tables or [])!=set(expected_tables) or snapshot.get('revision')!='0017_transaction_submission':
        reject('migration/table identity')
    return True


# Fixed total collection budget; never configurable through untrusted environment.
WINDOWS_INSPECTION_TIMEOUT_SECONDS = 60

WINDOWS_FAILURE_CODES = {
    'service-query', 'service-not-running', 'listener-query', 'listener-ambiguous',
    'listener-process', 'service-process', 'firewall-profiles', 'firewall-rules',
    'firewall-port-filter', 'firewall-application-filter', 'firewall-address-filter',
    'evidence-serialization', 'listener-package-query',
}


def windows_evidence():
    if os.name != 'nt':reject('Windows inspection unavailable on this machine')
    script=Path(__file__).with_name('windows_postgres_identity.ps1')
    if not script.is_file():reject('Windows evidence collector file missing')
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(script)],
                              capture_output=True,text=True,timeout=WINDOWS_INSPECTION_TIMEOUT_SECONDS,check=False)
    except subprocess.TimeoutExpired:
        reject(f'Windows evidence collector timed out after {WINDOWS_INSPECTION_TIMEOUT_SECONDS} seconds')
    except FileNotFoundError:
        reject('Windows PowerShell executable unavailable')
    except PermissionError:
        reject('Windows evidence collector execution denied')
    except OSError:
        reject('Windows evidence collector could not be started')
    except UnicodeError:
        reject('Windows evidence collector output decoding failed')
    # Never echo stderr, stdout or exception details: they may contain secrets.
    try:
        evidence=json.loads(result.stdout)
    except (ValueError,TypeError):
        if result.returncode:
            # Report transport shape, never the content of either stream.
            shape = 'empty' if not result.stdout.strip() else 'non-JSON'
            stderr = 'present' if result.stderr else 'empty'
            reject('Windows collector failed without structured diagnostics'
                   + f' (exit={result.returncode}; stdout={shape}; stderr={stderr})')
        reject('Windows evidence collector returned invalid JSON')
    if not isinstance(evidence,dict):reject('Windows evidence collector returned a non-object')
    if result.returncode or evidence.get('inspection_error'):
        code=evidence.get('inspection_error')
        if code in WINDOWS_FAILURE_CODES:
            reject('Windows evidence collection failed at '+code)
        reject('Windows collector failed without recognized diagnostics')
    return evidence


def database_snapshot(connection, protected_evidence=None):
    # This function must run within an explicit READ ONLY transaction.
    row=connection.execute("""SELECT current_database(),current_user,session_user,
        host(inet_server_addr()),inet_server_port(),current_setting('server_version_num')::int,
        pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()""").fetchone()
    snapshot=dict(zip(('database','user','session_user','address','port','version','owner'),row))
    role=connection.execute("""SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin
        FROM pg_roles WHERE rolname=current_user""").fetchone()
    snapshot['database_oid']=connection.execute('SELECT oid FROM pg_database WHERE datname=current_database()').fetchone()[0]
    snapshot['role_oid']=connection.execute('SELECT oid FROM pg_roles WHERE rolname=current_user').fetchone()[0]
    snapshot['role']=dict(zip(('superuser','createdb','createrole','replication','bypassrls','login'),role))
    snapshot['memberships']=[r[0] for r in connection.execute("""SELECT parent.rolname FROM pg_auth_members m
        JOIN pg_roles child ON child.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid
        WHERE child.rolname=current_user""")]
    snapshot['owned_other_databases']=[r[0] for r in connection.execute("""SELECT datname FROM pg_database
        WHERE datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND datname<>current_database()""")]
    snapshot['schemas']=[r[0] for r in connection.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY nspname")]
    snapshot['extensions']=[r[0] for r in connection.execute('SELECT extname FROM pg_extension ORDER BY extname')]
    snapshot['tables']=[r[0] for r in connection.execute("""SELECT n.nspname||'.'||c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%'
        AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f','S') ORDER BY 1""")]
    snapshot['revision']=None
    if 'public.alembic_version' in snapshot['tables']:
        revisions=connection.execute('SELECT version_num FROM public.alembic_version').fetchall()
        if len(revisions)!=1:reject('ambiguous migration head')
        snapshot['revision']=revisions[0][0]
    # Restricted roles may lack permission for these reads. Do not elevate them:
    # failure is BLOCKED pending a separately reviewed read-only evidence route.
    if protected_evidence is not None:
        for key in ('data_directory','listen_addresses','hba_rules'):
            snapshot[key]=protected_evidence.get(key)
    else:
        snapshot['data_directory']=connection.execute("SHOW data_directory").fetchone()[0]
        snapshot['listen_addresses']=connection.execute("SHOW listen_addresses").fetchone()[0]
        from psycopg.rows import dict_row
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT type,database,user_name AS \"user\",address,netmask,auth_method,error FROM pg_hba_file_rules ORDER BY rule_number")
            snapshot['hba_rules']=[dict(r) for r in cursor]
    return snapshot


def preflight(environ, phase='empty', expected_tables=(), expected_identity=None):
    url=validated_test_url(environ)
    if environ.get('STOCKBRIDGE_TEST_POSTGRES_MODE')=='ubuntu-isolated-cluster':
        from tests.support.ubuntu_postgres_identity import ubuntu_preflight
        return ubuntu_preflight(environ,url,phase,expected_tables,expected_identity)
    if environ.get('STOCKBRIDGE_TEST_POSTGRES_MODE')!='existing-local-service':
        reject('this preflight requires existing-local-service mode')
    evidence=windows_evidence()
    directory=validate_os(evidence)  # Before any connection; includes firewall checks.
    import psycopg
    try:
        with psycopg.connect(host=url.host,port=url.port,dbname=url.database,user=url.username,
                password=url.password,connect_timeout=5,
                options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000') as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION READ ONLY')
                snapshot=database_snapshot(conn)
                validate_database(snapshot,directory,phase,expected_tables,network_verified=validate_network(evidence),expected_identity=expected_identity)
        return {'status':'PASS','phase':phase,'database':url.database,'server_major':17,'database_oid':snapshot['database_oid'],'role_oid':snapshot['role_oid']}
    except UnsafeTestDatabase:raise
    except Exception:reject('read-only metadata unavailable; no privilege escalation attempted')

if __name__=='__main__':
    try:
        print(json.dumps(preflight(dict(os.environ))))
    except UnsafeTestDatabase as error:
        print(str(error))
        raise SystemExit(1) from None
