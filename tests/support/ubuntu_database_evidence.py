"""Prepared least-privilege SQL evidence function. Never installed automatically.

Operator installation changes only the approved isolated DB after separate approval.
The testing role receives EXECUTE on a fixed, argument-free read-only function,
not pg_read_all_settings, monitoring memberships or administrator privileges.
"""

SCHEMA='stockbridge_preflight'
TARGET='stockbridge_test_9ca65feb59b0ca68'
BODY="""
DECLARE output jsonb;
BEGIN
 IF current_database() <> 'stockbridge_test_9ca65feb59b0ca68'
    OR session_user <> 'stockbridge_test_9ca65feb59b0ca68'
    OR current_setting('data_directory') <> '/var/lib/postgresql/16/stockbridge-test'
    OR current_setting('port') <> '55432'
    OR current_setting('server_version_num')::int NOT BETWEEN 160000 AND 169999 THEN
   RAISE EXCEPTION 'Isolated preflight identity mismatch';
 END IF;
 SELECT jsonb_build_object(
   'database_oid',(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()),
   'role_oid',(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=session_user),
   'data_directory',current_setting('data_directory'),
   'listen_addresses',current_setting('listen_addresses'),
   'hba_rules',COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'rule_number',rule_number,'type',type,'database',database,'user',user_name,
      'address',address,'netmask',netmask,'auth_method',auth_method,'error',error IS NOT NULL,
      'has_options',COALESCE(cardinality(options),0)>0,
      'simple_syntax',CASE WHEN file_name LIKE '/etc/postgresql/16/stockbridge-test/%'
        AND file_name NOT LIKE '%/../%' THEN
        split_part(pg_catalog.pg_read_file(file_name), E'\\n',line_number) !~ '["\\\\]'
        ELSE false END) ORDER BY rule_number) FROM pg_catalog.pg_hba_file_rules),'[]'::jsonb)
 ) INTO output;
 RETURN output;
END
"""
# Only operator print/export of this non-secret SQL is supported; no connection code.
INSTALL_SQL="""BEGIN;
DO $check$ BEGIN
 IF current_database() <> 'stockbridge_test_9ca65feb59b0ca68' OR current_user <> 'postgres'
 OR current_setting('data_directory') <> '/var/lib/postgresql/16/stockbridge-test'
 OR current_setting('port') <> '55432'
 OR current_setting('server_version_num')::int NOT BETWEEN 160000 AND 169999 THEN
 RAISE EXCEPTION 'Wrong isolated cluster'; END IF;
 IF (SELECT pg_get_userbyid(datdba) FROM pg_catalog.pg_database WHERE datname=current_database()) <> 'stockbridge_test_9ca65feb59b0ca68'
 OR NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname='stockbridge_test_9ca65feb59b0ca68'
 AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls)
 OR EXISTS (SELECT FROM pg_catalog.pg_auth_members WHERE member=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='stockbridge_test_9ca65feb59b0ca68'))
 OR EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname<>current_database() AND datdba=(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='stockbridge_test_9ca65feb59b0ca68')) THEN
 RAISE EXCEPTION 'Unsafe isolated role ownership'; END IF;
 IF EXISTS (SELECT FROM pg_catalog.pg_namespace WHERE nspname='stockbridge_preflight')
 OR EXISTS (SELECT FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname NOT IN ('pg_catalog','pg_toast','information_schema')) THEN
 RAISE EXCEPTION 'Expected empty isolated database'; END IF;
END $check$;
CREATE SCHEMA stockbridge_preflight AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA stockbridge_preflight FROM PUBLIC;
CREATE FUNCTION stockbridge_preflight.read_evidence() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $body$"""+BODY+"""$body$;
REVOKE ALL ON FUNCTION stockbridge_preflight.read_evidence() FROM PUBLIC;
GRANT USAGE ON SCHEMA stockbridge_preflight TO stockbridge_test_9ca65feb59b0ca68;
GRANT EXECUTE ON FUNCTION stockbridge_preflight.read_evidence() TO stockbridge_test_9ca65feb59b0ca68;
COMMIT;
"""


def read_scoped_evidence(connection):
    from tests.support.isolated_postgres import UnsafeTestDatabase
    def reject(): raise UnsafeTestDatabase('PostgreSQL preflight rejected: Ubuntu scoped SQL evidence missing or untrusted')
    from psycopg.rows import dict_row
    # Exact function body, owner, search path and ACL, not JSON parsing alone.
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("""SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,
        pg_get_userbyid(p.proowner) AS owner,l.lanname AS language,
        pg_get_userbyid(n.nspowner) AS schema_owner,
        has_schema_privilege(current_user,n.oid,'CREATE') AS can_create,
        (SELECT array_agg(pg_get_userbyid(a.grantee) ORDER BY a.grantee)
         FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.privilege_type='EXECUTE') AS executors
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_catalog.pg_language l ON l.oid=p.prolang
        WHERE n.nspname='stockbridge_preflight' AND p.proname='read_evidence'
        AND p.pronargs=0 AND p.prorettype='jsonb'::regtype""")
        row=cursor.fetchall()
    if len(row)!=1: reject()
    item=dict(row[0])
    if (item.get('prosrc')!=BODY or item.get('prosecdef') is not True or item.get('provolatile')!='s'
            or item.get('proconfig')!=['search_path=pg_catalog'] or item.get('owner')!='postgres'
            or item.get('language')!='plpgsql' or item.get('schema_owner')!='postgres'
            or item.get('can_create') is not False or set(item.get('executors') or [])!={'postgres',TARGET}): reject()
    value=connection.execute('SELECT stockbridge_preflight.read_evidence()').fetchone()
    if not value or not isinstance(value[0],dict): reject()
    return value[0]


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Print reviewed installation SQL only; never connect')
    parser.add_argument('--print-install-sql',action='store_true',required=True)
    parser.parse_args()
    print(INSTALL_SQL)
