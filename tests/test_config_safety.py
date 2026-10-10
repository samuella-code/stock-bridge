"""Configuration validation uses synthetic credentials and never connects to SQL."""
import pytest
from app import create_app, db
from app.config_safety import (validate_startup, deployment_identity, DEVELOPMENT_KEYS,
                              PRODUCTION_PROJECT_ID, STAGING_PROJECT_ID)
from scripts import vercel_build


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for key in ('VERCEL', 'VERCEL_ENV', 'VERCEL_PROJECT_ID', 'VERCEL_TARGET_ENV',
                'FLASK_ENV', 'SECRET_KEY', 'NEON_DATABASE_URL', 'NEON_POSTGRES_URL',
                'DATABASE_URL', vercel_build.SKIP_SETTING):
        monkeypatch.delenv(key, raising=False)


def production():
    return {'VERCEL_PROJECT_ID': PRODUCTION_PROJECT_ID, 'VERCEL_ENV': 'production',
            'SECRET_KEY': 'synthetic-signing-fixture-only-not-a-real-secret',
            'DATABASE_URL': 'postgresql://fixture:synthetic-password@db.example.invalid/fixture'}


def config(env):
    return {'SECRET_KEY': env['SECRET_KEY'], 'SQLALCHEMY_DATABASE_URI': env['DATABASE_URL']}


def test_local_and_test_configuration_remain_valid():
    assert validate_startup({'SECRET_KEY': 'dev-only-change-me',
                             'SQLALCHEMY_DATABASE_URI': 'sqlite:///stockbridge.db'}, {}) == 'local'
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test',
                      'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'})
    assert app.testing


def test_valid_production_config_without_database_connection(monkeypatch):
    env = production()
    assert validate_startup(config(env), env) == 'production'
    for key, value in env.items(): monkeypatch.setenv(key, value)
    def forbidden(*args, **kwargs): raise AssertionError('SQL initialization reached')
    monkeypatch.setattr(db, 'init_app', forbidden)
    # A valid config advances past validation; no engine or connection is created.
    with pytest.raises(AssertionError, match='SQL initialization reached'):
        create_app(config(env))


@pytest.mark.parametrize('key', [None, '', ' ', *sorted(DEVELOPMENT_KEYS)])
def test_production_rejects_default_or_missing_signing_key(key):
    env = production(); cfg = config(env)
    if key is None: env.pop('SECRET_KEY')
    else: env['SECRET_KEY'] = key; cfg['SECRET_KEY'] = key
    with pytest.raises(RuntimeError, match='signing configuration'):
        validate_startup(cfg, env)


@pytest.mark.parametrize('url', [None, '', 'sqlite:///stockbridge.db', 'not-a-url',
    'postgresql://host/database', 'postgresql://user:pass@host',
    'postgresql://user:pass@host:invalid/database', 'mysql://user:pass@host/database',
    'postgresql://user:pass@host:99999/database'])
def test_production_rejects_missing_or_invalid_database(url):
    env = production(); cfg = config(env)
    if url is None: env.pop('DATABASE_URL')
    else: env['DATABASE_URL'] = url
    with pytest.raises(RuntimeError, match='database configuration'):
        validate_startup(cfg, env)


@pytest.mark.parametrize('variable', ['NEON_DATABASE_URL', 'NEON_POSTGRES_URL', 'DATABASE_URL'])
def test_existing_database_variable_names_and_driver_normalization(variable):
    env = production(); cfg = config(env)
    env[variable] = env.pop('DATABASE_URL').replace('postgresql://', 'postgres://')
    cfg['SQLALCHEMY_DATABASE_URI'] = cfg['SQLALCHEMY_DATABASE_URI'].replace('postgresql://', 'postgresql+psycopg://')
    assert validate_startup(cfg, env) == 'production'


def test_precedence_and_effective_configuration_mismatch_rejected():
    env = production(); cfg = config(env)
    env['NEON_DATABASE_URL'] = 'sqlite:///wrong.db'
    with pytest.raises(RuntimeError): validate_startup(cfg, env)
    env = production(); cfg['SQLALCHEMY_DATABASE_URI'] = 'postgresql://fixture:synthetic-password@other.example.invalid/fixture'
    with pytest.raises(RuntimeError, match='mismatch'): validate_startup(cfg, env)


@pytest.mark.parametrize('environment', ['production', 'preview', 'development'])
def test_staging_identity_preserves_existing_configuration(environment):
    env = {'VERCEL_PROJECT_ID': STAGING_PROJECT_ID, 'VERCEL_ENV': environment,
           vercel_build.SKIP_SETTING: 'true'}
    assert validate_startup({'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:'}, env) == 'staging'
    assert vercel_build.migration_policy(env) == 'staging-skip'


@pytest.mark.parametrize('env', [
    {'VERCEL': '1'}, {'VERCEL_ENV': 'production'}, {'FLASK_ENV': 'production'},
    {'VERCEL_PROJECT_ID': 'prj_unknown', 'VERCEL_ENV': 'production'},
    {'VERCEL_PROJECT_ID': PRODUCTION_PROJECT_ID},
    {'VERCEL_PROJECT_ID': STAGING_PROJECT_ID, 'VERCEL_ENV': 'invalid'},
    {'VERCEL_PROJECT_ID': PRODUCTION_PROJECT_ID, 'VERCEL_ENV': 'preview', 'VERCEL_TARGET_ENV': 'production'},
    {'VERCEL_PROJECT_ID': STAGING_PROJECT_ID, 'VERCEL_ENV': 'production', 'VERCEL_TARGET_ENV': 'preview'},
])
def test_unknown_or_ambiguous_identity_rejected(env):
    with pytest.raises(RuntimeError): deployment_identity(env)


def test_testing_cannot_bypass_real_production_and_failure_precedes_sql(monkeypatch):
    env = production(); env['SECRET_KEY'] = 'test'
    for key, value in env.items(): monkeypatch.setenv(key, value)
    monkeypatch.setattr(db, 'init_app', lambda *a: pytest.fail('SQL initialization reached'))
    with pytest.raises(RuntimeError, match='signing configuration'):
        create_app({**config(env), 'TESTING': True})


def test_invalid_urls_and_keys_are_not_disclosed(capsys):
    env = production(); cfg = config(env)
    env['DATABASE_URL'] = 'private-invalid-credential-url'
    with pytest.raises(RuntimeError) as failure:
        validate_startup(cfg, env)
    assert 'private-invalid-credential-url' not in str(failure.value)
    assert not capsys.readouterr().out


def test_production_preview_preserved():
    env = {'VERCEL_PROJECT_ID': PRODUCTION_PROJECT_ID, 'VERCEL_ENV': 'preview'}
    assert validate_startup({'SECRET_KEY': 'test', 'SQLALCHEMY_DATABASE_URI': 'sqlite://'}, env) == 'preview-development'
