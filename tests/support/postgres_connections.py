"""Pure connection policy shared by the authorized harness; no connection here."""
from tests.support.isolated_postgres import validated_test_url, UnsafeTestDatabase


def connection_policy(environ, *, observer=False):
    validated_test_url(environ)  # Never use application defaults or libpq overrides.
    options = ('-c default_transaction_read_only=on -c statement_timeout=3000'
               if observer else '-c statement_timeout=10000 -c lock_timeout=5000')
    result = dict(connect_timeout=5, options=options)
    if environ.get('STOCKBRIDGE_TEST_POSTGRES_MODE') == 'ubuntu-isolated-cluster':
        import psycopg
        if psycopg.pq.version() < 160000:
            raise UnsafeTestDatabase('PostgreSQL preflight rejected: libpq 16 or newer required')
        result['require_auth'] = 'scram-sha-256'
        # Preflight uses pg_catalog only. ORM workers need public for app tables.
        result['options'] += ' -c search_path=pg_catalog,public'
    return result
