"""Temporary, opt-in configuration evidence; never opens a database connection."""
import json
import logging
import re
from threading import Lock
from urllib.parse import parse_qsl, unquote, urlsplit

FLAG = 'STAGING_DATABASE_ENDPOINT_DIAGNOSTIC_ENABLED'
VARIABLES = ('NEON_DATABASE_URL', 'NEON_POSTGRES_URL', 'DATABASE_URL')
ENDPOINT = 'ep-lingering-cherry-b8rquuz8'
HOSTS = frozenset(f'{ENDPOINT}{suffix}.c-14.us-east-1.aws.neon.tech'
                  for suffix in ('', '-pooler'))
METADATA = {
    'VERCEL': '1',
    'VERCEL_PROJECT_ID': 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv',
    'VERCEL_ENV': 'preview',
    'VERCEL_TARGET_ENV': 'preview',
    'VERCEL_GIT_REPO_OWNER': 'samuella-code',
    'VERCEL_GIT_REPO_SLUG': 'stock-bridge',
    'VERCEL_GIT_COMMIT_REF': 'release/staging-phase6',
}
_lock = Lock()
_attempted = False


def normalized(value):
    # Exactly the transformations performed by config.database_url().
    if not isinstance(value, str):
        return None
    for prefix in ('postgres://', 'postgresql://'):
        if value.startswith(prefix):
            return 'postgresql+psycopg://' + value[len(prefix):]
    return value


def endpoint_matches(value):
    if (not isinstance(value, str) or not value
            or re.search(r'\s|[\x00-\x1f\x7f]', value)
            or re.search(r'%(?![0-9a-fA-F]{2})', value)
            or '#' in value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'postgresql+psycopg' or parsed.netloc.count('@') != 1:
            return False
        credentials, authority = parsed.netloc.split('@')
        # Validate the original authority, not a decoded or case-folded host.
        # This rejects escaped host delimiters, multi-hosts and empty ports.
        if authority not in {host + port for host in HOSTS for port in ('', ':5432')}:
            return False
        username, separator, password = credentials.partition(':')
        if not separator or not username or not password:
            return False
        identifier = r'(?:[A-Za-z0-9_.~-]|%[0-9a-fA-F]{2})+'
        # RFC userinfo characters only. Escaped password delimiters remain data;
        # SQLAlchemy also unquotes credentials exactly once, never recursively.
        if (not re.fullmatch(identifier, username)
                or not re.fullmatch(r"(?:[A-Za-z0-9_.~!$&'()*+,;=:-]|%[0-9a-fA-F]{2})+", password)):
            return False
        decoded_username = unquote(username, encoding='utf-8', errors='strict')
        decoded_password = unquote(password, encoding='utf-8', errors='strict')
        if (not re.fullmatch(r'[A-Za-z0-9_.~-]+', decoded_username)
                or not decoded_password.isprintable()):
            return False
        # Conservative Neon database identifiers: no encoded path separators,
        # controls, whitespace, nested escapes, or DSN-like database strings.
        database = parsed.path[1:] if parsed.path.startswith('/') else ''
        if (not re.fullmatch(identifier, database)
                or not re.fullmatch(r'[A-Za-z0-9_.~-]+',
                    unquote(database, encoding='utf-8', errors='strict'))):
            return False
        if '?' in value and not parsed.query:
            return False
        # Keys and values must be canonical literals; reject encoded aliases,
        # host overrides, duplicate options, and invalid SSL option values.
        allowed_options = {
            'sslmode': {'require', 'verify-ca', 'verify-full'},
            'channel_binding': {'require', 'prefer', 'disable'},
        }
        if parsed.query:
            for item in parsed.query.split('&'):
                key, equals, setting = item.partition('=')
                if (not equals or key not in allowed_options
                        or setting not in allowed_options[key]):
                    return False
        options = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        keys = [key for key, _ in options]
        return len(keys) == len(set(keys))
    except (TypeError, ValueError):
        return False


def startup_diagnostic(config, environ):
    """One attempt per process. Output is restricted to three fixed JSON keys.

    Invoke only from the WSGI startup path, never an HTTP handler. Unauthorized
    environments produce no record, even when the opt-in flag is present.
    """
    global _attempted
    with _lock:
        if _attempted:
            return
        _attempted = True
    if environ.get(FLAG) != 'true':
        return
    if any(environ.get(key) != expected for key, expected in METADATA.items()):
        return
    selected = next((key for key in VARIABLES if environ.get(key)), None)
    raw = normalized(environ.get(selected)) if selected else None
    effective = normalized(config.get('SQLALCHEMY_DATABASE_URI'))
    matches = bool(raw and raw == effective and endpoint_matches(raw))
    logging.getLogger('stockbridge.runtime').warning(json.dumps({
        'selected_variable': selected,
        'endpoint_matches_staging': matches,
        'database_identity_verified': False,
    }, sort_keys=True))
