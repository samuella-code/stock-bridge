"""Disconnected policy and DDL compilation; no application or database startup."""
import ast
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.dialects.postgresql import dialect
from sqlalchemy.schema import CreateTable
from alembic.migration import MigrationContext
from tests.support.isolated_postgres import UnsafeTestDatabase
from tests.support.postgres_connections import connection_policy
from tests.support.postgres_staff_schema import staff_application_policy
from tests.test_postgres_staff_guards import env


def test_staff_engine_has_exact_public_creation_profile_without_changing_workers():
    settings=env()
    worker=connection_policy(settings)
    observer=connection_policy(settings,observer=True)
    actual=staff_application_policy(settings)
    assert actual == dict(connect_timeout=5,require_auth='scram-sha-256',
        options='-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=public')
    assert connection_policy(settings)==worker
    assert connection_policy(settings,observer=True)==observer
    assert worker['options'].endswith('search_path=pg_catalog,public')
    assert observer['options'].endswith('search_path=pg_catalog,public')
    assert 'default_transaction_read_only=on' in observer['options']


@pytest.mark.parametrize('field,value',[
    ('STOCKBRIDGE_TEST_POSTGRES_MODE','existing-local-service'),
    ('STOCKBRIDGE_TEST_POSTGRES_MODE','disposable-instance'),
    ('STOCKBRIDGE_TEST_POSTGRES_ACK',''),
    ('STOCKBRIDGE_TEST_POSTGRES_UBUNTU_ACK',''),
    ('STOCKBRIDGE_TEST_POSTGRES_URL','postgresql+psycopg://other:secret@localhost:55432/other'),
    ('PGOPTIONS','-c search_path=public'),
])
def test_staff_profile_rejects_unapproved_environment(field,value):
    settings=env();settings[field]=value
    with pytest.raises(UnsafeTestDatabase):staff_application_policy(settings)


@pytest.mark.parametrize('policy',[
    dict(connect_timeout=5,require_auth='scram-sha-256',options='-c search_path=public'),
    dict(connect_timeout=5,options='-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=pg_catalog,public'),
    dict(connect_timeout=6,require_auth='scram-sha-256',options='-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=pg_catalog,public'),
    dict(connect_timeout=5,require_auth='scram-sha-256',autocommit=True,options='-c statement_timeout=10000 -c lock_timeout=5000 -c search_path=pg_catalog,public'),
])
def test_upstream_policy_drift_fails_closed(policy):
    with patch('tests.support.postgres_staff_schema.connection_policy',return_value=policy):
        with pytest.raises(UnsafeTestDatabase):staff_application_policy(env())


def test_actual_alembic_version_and_application_ddl_are_unqualified():
    # Compilation only: no connection, run_migrations, Flask or upgrade call.
    context=MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True})
    version=context._version
    table=Table('synthetic_application',MetaData(),Column('id',Integer,primary_key=True))
    assert version.schema is None and table.schema is None
    assert 'CREATE TABLE alembic_version' in str(CreateTable(version).compile(dialect=dialect()))
    assert 'CREATE TABLE synthetic_application' in str(CreateTable(table).compile(dialect=dialect()))
    # PostgreSQL documents first explicit usable schema as creation namespace;
    # implicit pg_catalog lookup does not make it the creation namespace.
    old=connection_policy(env())['options'].split('search_path=')[1].split(',')
    new=staff_application_policy(env())['options'].split('search_path=')[1].split(',')
    assert old[0]=='pg_catalog' and new==['public']


def test_runtime_engine_config_uses_scoped_profile():
    support=Path(__file__).parent/'support'
    module=ast.parse((support/'postgres_staff_fixtures.py').read_text())
    runtime=next(n for n in module.body if isinstance(n,ast.ClassDef) and n.name=='Runtime')
    app=next(n for n in runtime.body if isinstance(n,ast.FunctionDef) and n.name=='app')
    args=[n.value for n in ast.walk(app) if isinstance(n,ast.keyword) and n.arg=='connect_args']
    assert len(args)==1 and isinstance(args[0],ast.Call)
    assert args[0].func.id=='staff_application_policy'
    assert ast.unparse(args[0].args[0])=='self.env'


def test_migration_sources_do_not_override_unqualified_creation_namespace():
    root=Path(__file__).parents[1]
    environment=ast.parse((root/'migrations/env.py').read_text())
    configure=[n for n in ast.walk(environment) if isinstance(n,ast.Call)
               and isinstance(n.func,ast.Attribute) and n.func.attr=='configure']
    assert len(configure)==1
    assert not any(k.arg=='version_table_schema' for k in configure[0].keywords)
    for filename in ('0001_initial_schema.py','0017_transaction_submission.py'):
        tree=ast.parse((root/'migrations/versions'/filename).read_text())
        create=[n for n in ast.walk(tree) if isinstance(n,ast.Call)
                and isinstance(n.func,ast.Attribute) and n.func.attr=='create_table']
        assert create
        assert all(not any(k.arg=='schema' for k in n.keywords) for n in create)
