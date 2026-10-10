"""Pure validation only: never connect, provision, migrate or clean up here."""
import re
from sqlalchemy.engine import make_url

class UnsafeTestDatabase(ValueError):
    pass


def validated_test_url(environ):
    """Reject all hosted targets, defaults and ambiguous libpq overrides.

    This is a necessary precondition, not proof that a local server is disposable.
    A future fixture must also verify the dedicated instance/role/database ownership
    manifest before migration, seeding or cleanup. Never print the returned URL.
    """
    def reject():
        raise UnsafeTestDatabase('Explicit disposable local PostgreSQL configuration required.')
    if environ.get('STOCKBRIDGE_TEST_POSTGRES_ACK') != 'disposable-local-only':
        reject()
    run_id = environ.get('STOCKBRIDGE_TEST_POSTGRES_RUN_ID', '')
    if not re.fullmatch(r'[a-f0-9]{16}', run_id):
        reject()
    raw = environ.get('STOCKBRIDGE_TEST_POSTGRES_URL', '')
    try:
        url = make_url(raw)
    except Exception:
        reject()
    mode = environ.get('STOCKBRIDGE_TEST_POSTGRES_MODE', 'disposable-instance')
    if mode not in ('disposable-instance', 'existing-local-service'):
        reject()
    existing = mode == 'existing-local-service'
    if existing and environ.get('STOCKBRIDGE_TEST_POSTGRES_SERVICE_ACK') != 'postgresql-x64-17:stockbridge_disposable_test:stockbridge_test_user':
        reject()
    # Existing-service URLs pass only static validation here. Read-only preflight
    # must verify OS/database identity before any future mutation is authorized.
    expected_database = 'stockbridge_disposable_test' if existing else 'stockbridge_test_' + run_id
    expected_user = 'stockbridge_test_user' if existing else 'stockbridge_test_' + run_id
    # A strict positive allowlist rejects Neon/staging/production connection URLs,
    # including encoded hostnames, query-string host overrides and Unix sockets.
    if (url.drivername != 'postgresql+psycopg'
            or url.host not in ('127.0.0.1', '::1')
            or url.port is None or not 1024 <= url.port <= 65535
            or (existing and (url.host != '127.0.0.1' or url.port != 5432))
            or (not existing and url.port == 5432)
            or url.database != expected_database
            or url.username != expected_user
            or not url.password or url.query):
        reject()
    if any(key.startswith('PG') and value for key, value in environ.items()):
        reject()  # Do not permit libpq service/host/options/environment overrides.
    if any(environ.get(key) for key in ('VERCEL', 'VERCEL_PROJECT_ID', 'VERCEL_ENV')):
        reject()
    return url
