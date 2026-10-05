import pytest
from scripts import staging_paystack_preflight as preflight


def environment():
    env = {
        "VERCEL_PROJECT_ID": preflight.PROJECT, "VERCEL_ENV": "production",
        "CUSTOMER_HOST": preflight.HOST, "NEON_PROJECT_ID": preflight.NEON_PROJECT,
        "NEON_DATABASE_URL": f"postgresql://fake:fake@{preflight.ENDPOINT}-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require",
        "PAYSTACK_PUBLIC_KEY": "pk_test_fixture", "PAYSTACK_SECRET_KEY": "sk_test_fixture",
        "SUBSCRIPTIONS_ENABLED": "true", "BILLING_PROVIDER_ENABLED": "true",
    }
    env.update({key: "PLN_fixture" + str(i) for i, (_, key, _, _) in enumerate(preflight.PLANS)})
    return env


def good_plan(code, secret):
    _, _, amount, interval = preflight.PLANS[int(code[-1])]
    return {"status": True, "data": {"plan_code": code, "domain": "test", "amount": amount,
                                   "interval": interval, "currency": "NGN"}}


def test_valid_plans():
    calls = []
    def getter(code, secret):
        calls.append(code)
        return good_plan(code, secret)
    assert all(preflight.validate(environment(), getter).values())
    assert len(calls) == 4


@pytest.mark.parametrize("key,value", [
    ("VERCEL_PROJECT_ID", "production-project"), ("VERCEL_ENV", "preview"),
    ("CUSTOMER_HOST", "stock-bridge-one.vercel.app"),
    ("NEON_PROJECT_ID", "production-neon-project"),
    ("NEON_DATABASE_URL", "sqlite:///local.db"),
    ("NEON_DATABASE_URL", "postgresql://fake:fake@production.neon.tech/neondb?sslmode=require"),
    ("PAYSTACK_PUBLIC_KEY", "pk_live_fixture"), ("PAYSTACK_SECRET_KEY", "sk_live_fixture"),
    ("PAYSTACK_SECRET_KEY", ""), ("SUBSCRIPTIONS_ENABLED", "false"),
    ("BILLING_PROVIDER_ENABLED", "false"),
])
def test_unsafe_environment_never_requests(key, value):
    env = environment(); env[key] = value
    def forbidden(*args):
        pytest.fail("Unsafe environment made network request")
    assert not all(preflight.validate(env, forbidden).values())


@pytest.mark.parametrize("field,value", [
    ("domain", "live"), ("amount", 1), ("amount", "300000"),
    ("currency", "USD"), ("interval", "weekly"), ("plan_code", "PLN_wrong"),
])
def test_rejects_mismatch(field, value):
    def getter(code, secret):
        payload = good_plan(code, secret); payload["data"][field] = value
        return payload
    result = preflight.validate(environment(), getter)
    assert not any(result[name] for name, *_ in preflight.PLANS)


def test_provider_exception_and_environment_never_logged(monkeypatch, capsys):
    monkeypatch.setattr(preflight.os, "environ", environment())
    def failure(*args):
        raise RuntimeError("sk_test_fixture pk_test_fixture private_response")
    monkeypatch.setattr(preflight, "fetch_plan", failure)
    assert preflight.main() == 1
    output = capsys.readouterr().out
    assert "fixture" not in output and "private_response" not in output
    assert all(line.endswith((": PASS", ": FAIL")) for line in output.splitlines())


def test_duplicates_and_invalid_codes_never_accepted():
    env = environment(); env[preflight.PLANS[1][1]] = env[preflight.PLANS[0][1]]
    assert not preflight.validate(env, good_plan)["Basic Monthly"]
    env = environment(); env[preflight.PLANS[0][1]] = "PLN_x/../../transaction/initialize"
    assert not preflight.validate(env, good_plan)["Basic Monthly"]


def test_database_precedence_and_malformed_uri():
    env = environment(); env["DATABASE_URL"] = "postgresql://production.invalid/prod"
    assert preflight.isolated(env)
    env["NEON_DATABASE_URL"] = "postgresql://[broken"
    assert not preflight.isolated(env)


def test_get_only_fixed_host_and_no_redirect(monkeypatch):
    requests = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit): return b'{"status":true,"data":{}}'
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 20
            return Response()
    monkeypatch.setattr(preflight, "build_opener", lambda handler: Opener())
    preflight.fetch_plan("PLN_fixture0", "sk_test_fixture")
    assert requests[0].get_method() == "GET"
    assert requests[0].full_url == "https://api.paystack.co/plan/PLN_fixture0"
    assert requests[0].data is None
    assert preflight.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid") is None
