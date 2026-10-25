"""Synthetic build configuration; provider and subprocess calls are mocked."""
import sys
import pytest
from scripts import staging_paystack_preflight as preflight
from scripts import vercel_build as build


def authorized():
    return {
        'VERCEL_PROJECT_ID': build.STAGING_PROJECT_ID,
        'VERCEL_ENV': 'preview', 'VERCEL_TARGET_ENV': 'preview',
        'VERCEL_GIT_REPO_OWNER': 'samuella-code',
        'VERCEL_GIT_REPO_SLUG': 'stock-bridge',
        'VERCEL_GIT_COMMIT_REF': 'release/staging-phase6',
        'VERCEL_GIT_COMMIT_SHA': 'a' * 40,
        'STAGING_PAYSTACK_APPROVED_SHA': 'a' * 40,
        'STAGING_PAYSTACK_CALLBACK_ORIGIN': 'https://stock-bridge-staging.vercel.app',
        'STAGING_PAYSTACK_TEST_ENABLED': 'true',
        'SUBSCRIPTIONS_ENABLED': 'true', 'BILLING_PROVIDER_ENABLED': 'true',
        'PAYSTACK_PUBLIC_KEY': 'pk_test_fixture',
        'PAYSTACK_SECRET_KEY': 'sk_test_fixture',
        build.SKIP_SETTING: 'true', build.PREFLIGHT_SETTING: 'true',
    }


@pytest.fixture
def harness(monkeypatch, capsys):
    env, calls = {}, []
    monkeypatch.setattr(build.os, 'environ', env)

    def checker():
        calls.append('checker')
        return 0

    def command(args):
        calls.append(args)
        return 0

    def forbidden(*args, **kwargs):
        pytest.fail('Unexpected provider activity')

    monkeypatch.setattr(preflight, 'main', checker)
    monkeypatch.setattr(preflight, 'fetch_plan', forbidden)
    monkeypatch.setattr(build.subprocess, 'call', command)

    def run(config, argv=None):
        env.clear()
        env.update(config)
        status = build.main(argv)
        output = capsys.readouterr()
        return status, calls, output.out + output.err
    return run


@pytest.mark.parametrize('value', [None, ''])
def test_default_and_production_preserved(harness, value):
    env = {'VERCEL_PROJECT_ID': 'prj_Other', 'VERCEL_ENV': 'production'}
    if value is not None:
        env[build.PREFLIGHT_SETTING] = value
    status, calls, output = harness(env)
    assert status == 0
    assert calls == [[sys.executable, '-m', 'flask', '--app',
                      'app:create_app', 'db', 'upgrade']]
    assert 'PAYSTACK PREFLIGHT' not in output


@pytest.mark.parametrize('value', [None, ''])
def test_default_staging_skips_checker(harness, value):
    env = authorized()
    env.pop(build.PREFLIGHT_SETTING)
    if value is not None:
        env[build.PREFLIGHT_SETTING] = value
    status, calls, output = harness(env)
    assert status == 0 and calls == [] and build.SKIP_MARKER in output


def test_authorized_continues_without_migrations(harness):
    status, calls, output = harness(authorized())
    assert status == 0 and calls == ['checker']
    assert 'PAYSTACK PREFLIGHT: PASS' in output and build.SKIP_MARKER in output


@pytest.mark.parametrize('key', [k for k in authorized() if k != build.PREFLIGHT_SETTING])
def test_missing_required_configuration_stops_checker(harness, key):
    env = authorized()
    env.pop(key)
    status, calls, output = harness(env)
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output


@pytest.mark.parametrize('key,value', [
    ('VERCEL_PROJECT_ID', 'prj_Other'), ('VERCEL_ENV', 'production'),
    ('VERCEL_TARGET_ENV', 'production'), ('VERCEL_GIT_REPO_OWNER', 'other'),
    ('VERCEL_GIT_REPO_SLUG', 'other'), ('VERCEL_GIT_COMMIT_REF', 'other'),
    ('VERCEL_GIT_COMMIT_SHA', 'b' * 40), ('VERCEL_GIT_COMMIT_SHA', 'invalid'),
    ('STAGING_PAYSTACK_APPROVED_SHA', 'b' * 40),
    ('STAGING_PAYSTACK_CALLBACK_ORIGIN', 'https://example.invalid'),
    ('PAYSTACK_SECRET_KEY', 'sk_live_fixture'),
])
def test_incorrect_authorization_stops_checker(harness, key, value):
    env = authorized()
    env[key] = value
    status, calls, output = harness(env)
    assert status == 1 and calls == []
    assert output.strip() == 'PAYSTACK PREFLIGHT AUTHORIZATION: FAIL'


@pytest.mark.parametrize('value', ['', 'false', 'TRUE', '1', ' true', 'true '])
def test_invalid_migration_skip_stops_checker(harness, value):
    env = authorized()
    env[build.SKIP_SETTING] = value
    status, calls, output = harness(env)
    assert status == 1 and calls == [] and build.SKIP_MARKER not in output


@pytest.mark.parametrize('value', ['false', 'TRUE', '1', ' true', 'true '])
def test_malformed_opt_in_stops_checker(harness, value):
    env = authorized()
    env[build.PREFLIGHT_SETTING] = value
    assert harness(env)[:2] == (1, [])


def test_check_only_has_no_activity(harness):
    status, calls, output = harness(authorized(), ['--check-only'])
    assert status == 0 and calls == []
    assert 'PAYSTACK PREFLIGHT CHECK ONLY: PASS' in output


@pytest.mark.parametrize('raises', [False, True])
def test_preflight_failure_cannot_migrate(harness, monkeypatch, raises):
    def failure():
        if raises:
            raise RuntimeError('private-provider-response')
        return 7
    monkeypatch.setattr(preflight, 'main', failure)
    status, calls, output = harness(authorized())
    assert status == 1 and calls == []
    assert build.SKIP_MARKER not in output
    assert 'PAYSTACK PREFLIGHT: FAIL' in output
    assert 'private-provider-response' not in output
