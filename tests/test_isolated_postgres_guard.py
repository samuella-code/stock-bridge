"""Configuration validation only. No engine, connection or database mutations."""
import pytest
from tests.support.isolated_postgres import validated_test_url, UnsafeTestDatabase

RUN = '0123456789abcdef'

def configured():
    return dict(STOCKBRIDGE_TEST_POSTGRES_ACK='disposable-local-only',
                STOCKBRIDGE_TEST_POSTGRES_RUN_ID=RUN,
                STOCKBRIDGE_TEST_POSTGRES_URL=f'postgresql+psycopg://stockbridge_test_{RUN}:synthetic@127.0.0.1:55432/stockbridge_test_{RUN}')


def test_explicit_configuration_only():
    env=configured()
    assert validated_test_url(env).database == 'stockbridge_test_' + RUN
    env.update(DATABASE_URL='postgresql://synthetic@production/db', NEON_DATABASE_URL='postgresql://synthetic@staging/db')
    assert validated_test_url(env).host == '127.0.0.1'

@pytest.mark.parametrize('value', [None, '', 'false', 'true'])
def test_missing_ack_rejected(value):
    env=configured();env['STOCKBRIDGE_TEST_POSTGRES_ACK']=value
    with pytest.raises(UnsafeTestDatabase):validated_test_url(env)

@pytest.mark.parametrize('url', [
    'sqlite:///:memory:', 'not-a-url',
    'postgresql+psycopg://synthetic@stockbridge-staging.neon.tech/db',
    'postgresql+psycopg://synthetic@production.neon.tech/db',
    f'postgresql+psycopg://stockbridge_test_{RUN}:synthetic@localhost:55432/stockbridge_test_{RUN}',
    f'postgresql+psycopg://stockbridge_test_{RUN}:synthetic@127.0.0.1:5432/stockbridge_test_{RUN}',
    f'postgresql+psycopg://stockbridge_test_{RUN}:synthetic@127.0.0.1:55432/stockbridge-staging',
    f'postgresql+psycopg://stockbridge_test_{RUN}:synthetic@127.0.0.1:55432/stockbridge_test_{RUN}?host=production.neon.tech',
    f'postgresql+psycopg://postgres:synthetic@127.0.0.1:55432/stockbridge_test_{RUN}',
])
def test_unsafe_targets_rejected_without_exposing_url(url):
    env=configured();env['STOCKBRIDGE_TEST_POSTGRES_URL']=url
    with pytest.raises(UnsafeTestDatabase) as caught:validated_test_url(env)
    assert url not in str(caught.value)

@pytest.mark.parametrize('key', ['PGHOST','PGSERVICE','PGOPTIONS','VERCEL','VERCEL_ENV','VERCEL_PROJECT_ID'])
def test_ambient_overrides_rejected(key):
    env=configured();env[key]='synthetic'
    with pytest.raises(UnsafeTestDatabase):validated_test_url(env)

@pytest.mark.parametrize('key', ['STOCKBRIDGE_TEST_POSTGRES_RUN_ID','STOCKBRIDGE_TEST_POSTGRES_URL'])
def test_missing_explicit_settings_rejected(key):
    env=configured();del env[key]
    with pytest.raises(UnsafeTestDatabase):validated_test_url(env)
