"""Decide migration policy before importing Flask or accessing any database."""
import argparse
import os
import re
import subprocess
import sys

STAGING_PROJECT_ID = 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv'
SKIP_SETTING = 'STOCKBRIDGE_SKIP_BUILD_MIGRATIONS'
SKIP_MARKER = ('STAGING MIGRATIONS SKIPPED: project identity verified; '
               'explicit skip=true; database command not invoked.')


def migration_policy(environ):
    """Return a decision; reject conflicting configuration without logging values."""
    project = environ.get('VERCEL_PROJECT_ID')
    environment = environ.get('VERCEL_ENV')
    target = environ.get('VERCEL_TARGET_ENV')
    skip_present = SKIP_SETTING in environ
    if project is not None and not re.fullmatch(r'prj_[A-Za-z0-9]+', project):
        raise ValueError('Missing or invalid project identity; migration policy rejected.')
    if environment not in (None, 'production', 'preview', 'development'):
        raise ValueError('Unrecognized VERCEL_ENV; migration policy rejected.')
    if target == 'production' and environment != 'production':
        raise ValueError('Conflicting production target; migration policy rejected.')
    if environment == 'production' and target not in (None, 'production'):
        raise ValueError('Conflicting deployment target; migration policy rejected.')
    if project == STAGING_PROJECT_ID:
        if environ.get(SKIP_SETTING) != 'true':
            raise ValueError('Staging requires the exact migration skip setting true.')
        return 'staging-skip'
    if skip_present:
        raise ValueError('Migration skip setting is permitted only in the verified staging project.')
    if environment == 'production':
        if not project:
            raise ValueError('Production-target build requires a verified project identity.')
        return 'migrate'
    return 'preview-local-skip'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true',
                        help='Validate configuration without executing migrations.')
    options = parser.parse_args([] if argv is None else argv)
    try:
        policy = migration_policy(os.environ)
    except ValueError as error:
        print(f'MIGRATION POLICY ERROR: {error}', file=sys.stderr, flush=True)
        return 1
    if policy == 'staging-skip':
        print(SKIP_MARKER, flush=True)
        return 0
    if options.check_only:
        print(f'MIGRATION POLICY CHECK ONLY: {policy}; database command not invoked.', flush=True)
        return 0
    if policy == 'preview-local-skip':
        print('Preview/local build: database migrations skipped. Migrate a dedicated preview database explicitly.', flush=True)
        return 0
    print('PRODUCTION MIGRATION POLICY: non-staging project identified; running database upgrade.', flush=True)
    return subprocess.call([sys.executable, '-m', 'flask', '--app', 'app:create_app', 'db', 'upgrade'])


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
