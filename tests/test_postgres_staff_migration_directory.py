"""Resolve real Alembic revisions from an unrelated cwd; synthetic execution only."""
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import MagicMock
import sys
import pytest
from flask import Flask
from flask_migrate import Migrate
from alembic.script import ScriptDirectory
from alembic.operations import Operations
from sqlalchemy import event
from tests.support import postgres_staff_fixtures as fixtures

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def migration_app():
    app=Flask('isolated-directory-check')
    Migrate(app,SimpleNamespace())
    return app


def capture_revision(config,revision):
    assert Path(config.get_main_option('script_location'))==ROOT/'migrations'
    assert Path(config.config_file_name)==ROOT/'migrations/alembic.ini'
    assert ScriptDirectory.from_config(config).get_revision(revision).revision==revision


def test_migrate17_resolves_reviewed_checkout_from_unrelated_cwd(tmp_path,monkeypatch,migration_app):
    monkeypatch.chdir(tmp_path)
    calls=[]
    def upgrade(config,revision,**kwargs):
        capture_revision(config,revision);calls.append(revision)
    monkeypatch.setattr('flask_migrate.command.upgrade',upgrade)
    rt=SimpleNamespace(root=ROOT,app=lambda:migration_app,preflight=MagicMock())
    fixtures.Runtime.migrate17(rt)
    assert calls==[fixtures.V17]
    assert [c.args for c in rt.preflight.call_args_list]==[('empty',),(fixtures.V17,)]
    assert Path.cwd()==tmp_path


@pytest.mark.parametrize('case',['M00','M01'])
def test_staff_migration_cases_resolve_reviewed_checkout(tmp_path,monkeypatch,migration_app,case):
    monkeypatch.chdir(tmp_path)
    fake=ModuleType('app');fake.db=SimpleNamespace(engine=object())
    monkeypatch.setitem(sys.modules,'app',fake)  # Synthetic module only, no application loaded.
    callbacks={};calls=[]
    monkeypatch.setattr(event,'listen',lambda engine,name,fn:callbacks.__setitem__(name,fn))
    monkeypatch.setattr(event,'remove',lambda engine,name,fn:callbacks.pop(name))
    monkeypatch.setattr(Operations,'create_table',lambda *args,**kwargs:object())
    def upgrade(config,revision,**kwargs):
        capture_revision(config,revision);calls.append(revision)
        if case=='M00':
            try:Operations.create_table(None,'staff_role')
            finally:callbacks['rollback'](None)
    monkeypatch.setattr('flask_migrate.command.upgrade',upgrade)
    connection=MagicMock();connection.__enter__.return_value=connection
    connection.execute.return_value.fetchone.return_value=(0,)
    before={'user':[{'id':1}]}
    rt=SimpleNamespace(root=ROOT,app=lambda:migration_app,preflight=MagicMock(),
        baseline=lambda:before,snapshot=lambda **kwargs:before,connection=lambda:connection)
    fixtures.migration_case(rt,case)
    assert calls==[fixtures.V18]
    assert [c.args for c in rt.preflight.call_args_list]==[(fixtures.V17,),
        (fixtures.V17 if case=='M00' else fixtures.V18,)]
    assert not callbacks and Path.cwd()==tmp_path


def test_relative_directory_reproduces_system_exit_when_staff_revision_absent(tmp_path,monkeypatch,migration_app):
    import shutil
    from flask_migrate import upgrade
    directory=tmp_path/'migrations'
    shutil.copytree(ROOT/'migrations',directory,ignore=shutil.ignore_patterns('0018_staff_foundation.py'))
    monkeypatch.chdir(tmp_path)
    def resolve_only(config,revision,**kwargs):
        ScriptDirectory.from_config(config).get_revision(revision)
    monkeypatch.setattr('flask_migrate.command.upgrade',resolve_only)
    with migration_app.app_context(),pytest.raises(SystemExit) as error:
        upgrade(revision=fixtures.V18)
    assert error.value.code==1
