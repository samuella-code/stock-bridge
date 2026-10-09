"""Migration policy tests never import Flask or run migration subprocesses."""
import sys
import pytest
from scripts import vercel_build as build

OTHER_PROJECT = 'prj_TestProduction123'

@pytest.fixture
def harness(monkeypatch, capsys):
    for key in ('VERCEL_PROJECT_ID', 'VERCEL_ENV', 'VERCEL_TARGET_ENV', build.SKIP_SETTING):
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(build.subprocess, 'call', lambda args: calls.append(args) or 0)
    def run(config, argv=None):
        for key, value in config.items():
            monkeypatch.setenv(key, value)
        status = build.main(argv)
        output = capsys.readouterr()
        return status, calls, output.out + output.err
    return run

@pytest.mark.parametrize('environment', ['production', 'preview', 'development'])
@pytest.mark.parametrize('check_only', [False, True])
def test_verified_staging_skips(harness, environment, check_only):
    status, calls, output = harness({'VERCEL_PROJECT_ID': build.STAGING_PROJECT_ID,
        'VERCEL_ENV': environment, build.SKIP_SETTING: 'true'}, ['--check-only'] if check_only else [])
    assert status == 0 and calls == []
    assert build.SKIP_MARKER in output

@pytest.mark.parametrize('value', [None, '', 'false', 'TRUE', '1', ' true', 'true ', 'garbage'])
def test_staging_missing_or_invalid_setting_fails(harness, value):
    config = {'VERCEL_PROJECT_ID': build.STAGING_PROJECT_ID, 'VERCEL_ENV': 'production'}
    if value is not None:
        config[build.SKIP_SETTING] = value
    status, calls, output = harness(config)
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output

@pytest.mark.parametrize('project', [None, OTHER_PROJECT])
@pytest.mark.parametrize('value', ['true', 'false', '', 'TRUE'])
def test_setting_outside_staging_rejected(harness, project, value):
    config = {'VERCEL_ENV': 'production', build.SKIP_SETTING: value}
    if project:
        config['VERCEL_PROJECT_ID'] = project
    status, calls, output = harness(config, ['--check-only'])
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output

@pytest.mark.parametrize('project', [None, '', ' ', 'stock-bridge-staging', 'prj_a,prj_b', 'prj_a\n'])
def test_missing_or_ambiguous_production_identity_fails(harness, project):
    config = {'VERCEL_ENV': 'production'}
    if project is not None:
        config['VERCEL_PROJECT_ID'] = project
    status, calls, output = harness(config)
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output


def test_identified_production_preserves_command(harness):
    status, calls, output = harness({'VERCEL_ENV': 'production', 'VERCEL_PROJECT_ID': OTHER_PROJECT})
    assert status == 0
    assert calls == [[sys.executable, '-m', 'flask', '--app', 'app:create_app', 'db', 'upgrade']]
    assert build.SKIP_MARKER not in output


def test_production_failure_propagated(harness, monkeypatch):
    monkeypatch.setattr(build.subprocess, 'call', lambda args: 7)
    assert harness({'VERCEL_ENV': 'production', 'VERCEL_PROJECT_ID': OTHER_PROJECT})[0] == 7

@pytest.mark.parametrize('environment', [None, 'preview', 'development'])
def test_preview_local_preserved(harness, environment):
    status, calls, output = harness({} if environment is None else {'VERCEL_ENV': environment})
    assert status == 0 and calls == [] and 'Preview/local build' in output
    assert build.SKIP_MARKER not in output


def test_check_only_does_not_import_flask_or_call_subprocess(harness, monkeypatch):
    import builtins
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert name != 'app' and not name.startswith(('flask', 'sqlalchemy'))
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    status, calls, output = harness({'VERCEL_ENV': 'production', 'VERCEL_PROJECT_ID': OTHER_PROJECT}, ['--check-only'])
    assert status == 0 and calls == [] and 'CHECK ONLY' in output
    assert build.SKIP_MARKER not in output

@pytest.mark.parametrize('config', [
    {'VERCEL_TARGET_ENV': 'production'},
    {'VERCEL_ENV': 'preview', 'VERCEL_TARGET_ENV': 'production'},
    {'VERCEL_ENV': 'production', 'VERCEL_TARGET_ENV': 'preview', 'VERCEL_PROJECT_ID': OTHER_PROJECT},
    {'VERCEL_ENV': 'unknown'},
])
def test_conflicting_environment_fails(harness, config):
    status, calls, output = harness(config)
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output
