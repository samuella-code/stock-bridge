"""Keep preview builds from migrating a shared production database."""
import os
import subprocess
import sys

def main():
    if os.environ.get('VERCEL_ENV') != 'production':
        print('Preview/local build: database migrations skipped. Migrate a dedicated preview database explicitly.')
        return 0
    return subprocess.call([sys.executable, '-m', 'flask', '--app', 'app:create_app', 'db', 'upgrade'])

if __name__ == '__main__':
    raise SystemExit(main())
