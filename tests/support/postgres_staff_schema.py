"""Fixed Staff application-engine schema policy; never connects or grants privileges."""
from tests.support.postgres_connections import connection_policy
from tests.support.isolated_postgres import UnsafeTestDatabase


def staff_application_policy(environ):
    policy = connection_policy(environ)
    expected = '-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=pg_catalog,public'
    if (environ.get('STOCKBRIDGE_TEST_POSTGRES_MODE') != 'ubuntu-isolated-cluster'
            or policy != dict(connect_timeout=5, options=expected,
                              require_auth='scram-sha-256')):
        raise UnsafeTestDatabase('Staff application profile rejected')
    # pg_catalog remains implicitly searched FIRST for lookup, but is no longer
    # the explicit current schema for unqualified Alembic/application DDL.
    policy['options'] = '-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=public'
    return policy
