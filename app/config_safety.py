"""Offline startup validation. Never connect to SQL or disclose configuration values."""
import os

from sqlalchemy.engine import make_url

PRODUCTION_PROJECT_ID = 'prj_ahZe3mfUk4MvejILpj9Md9QPP4fd'
STAGING_PROJECT_ID = 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv'
DATABASE_VARIABLES = ('NEON_DATABASE_URL', 'NEON_POSTGRES_URL', 'DATABASE_URL')
# The first value is the application's default; the others are explicit test
# fixture keys. None is an acceptable production signing credential.
DEVELOPMENT_KEYS = frozenset(('dev-only-change-me', 'test', 'billing-test', 'race',
    'catalogue-tests', 'csrf-test', 'isolated-multi-business', 'dom-test',
    'isolated-error-tests'))


def deployment_identity(environ):
    project = environ.get('VERCEL_PROJECT_ID')
    environment = environ.get('VERCEL_ENV')
    target = environ.get('VERCEL_TARGET_ENV')
    hosted = bool(environ.get('VERCEL')) or any(
        key in environ for key in ('VERCEL_PROJECT_ID', 'VERCEL_ENV', 'VERCEL_TARGET_ENV'))
    if not hosted:
        if environ.get('FLASK_ENV') == 'production':
            raise RuntimeError('Startup rejected: production deployment identity unavailable.')
        return 'local'
    if project not in (PRODUCTION_PROJECT_ID, STAGING_PROJECT_ID):
        raise RuntimeError('Startup rejected: missing or unrecognized deployment identity.')
    if environment not in ('production', 'preview', 'development'):
        raise RuntimeError('Startup rejected: missing or unrecognized deployment environment.')
    if target is not None and target != environment:
        raise RuntimeError('Startup rejected: conflicting deployment identity.')
    if project == STAGING_PROJECT_ID:
        return 'staging'
    return 'production' if environment == 'production' else 'preview-development'


def postgres_url(value):
    try:
        if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() for c in value):
            raise ValueError
        url = make_url(value)
        if (url.drivername not in ('postgres', 'postgresql', 'postgresql+psycopg')
                or not url.username or not url.password or not url.host or not url.database):
            raise ValueError
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
        return url.set(drivername='postgresql+psycopg')
    except Exception:
        # Parser errors can include the original credential-bearing URL.
        raise RuntimeError('Startup rejected: invalid production database configuration.') from None


def validate_startup(config, environ=None):
    environ = os.environ if environ is None else environ
    identity = deployment_identity(environ)
    if identity != 'production':
        return identity
    # TESTING must not bypass checks for a positively identified live deployment.
    configured_key = environ.get('SECRET_KEY')
    effective_key = config.get('SECRET_KEY')
    if (not isinstance(configured_key, str) or not configured_key.strip()
            or configured_key.strip() in DEVELOPMENT_KEYS
            or not isinstance(effective_key, str) or effective_key.strip() in DEVELOPMENT_KEYS
            or configured_key != effective_key):
        raise RuntimeError('Startup rejected: unsafe production signing configuration.')
    raw = next((environ.get(name) for name in DATABASE_VARIABLES if environ.get(name)), None)
    expected = postgres_url(raw)
    effective = postgres_url(config.get('SQLALCHEMY_DATABASE_URI'))
    if effective != expected:
        raise RuntimeError('Startup rejected: production database configuration mismatch.')
    return identity
