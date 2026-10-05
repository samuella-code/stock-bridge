"""One-off staging build preflight. No Flask startup, DB access or mutations.

Output is fixed labels and PASS/FAIL only. Never log exception/provider content.
This module is not registered as a route or invoked by application startup.
"""
import json
import os
import re
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROJECT = "prj_yeCWXPd44jShVUGHAgJu9khMjOMv"
NEON_PROJECT = "empty-recipe-08350132"
ENDPOINT = "ep-lingering-cherry-b8rquuz8"
HOST = "stock-bridge-staging.vercel.app"
PLANS = (
    ("Basic Monthly", "PAYSTACK_BASIC_MONTHLY_PLAN", 300000, "monthly"),
    ("Basic Yearly", "PAYSTACK_BASIC_YEARLY_PLAN", 3000000, "annually"),
    ("Plus Monthly", "PAYSTACK_PLUS_MONTHLY_PLAN", 500000, "monthly"),
    ("Plus Yearly", "PAYSTACK_PLUS_YEARLY_PLAN", 5000000, "annually"),
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def isolated(env):
    """Match the application's DB precedence without opening any DB connection."""
    try:
        uri = next((env.get(k) for k in (
            "NEON_DATABASE_URL", "NEON_POSTGRES_URL", "DATABASE_URL"
        ) if env.get(k)), "")
        parsed = urlsplit(uri)
        return (
            env.get("NEON_PROJECT_ID") == NEON_PROJECT
            and parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg"}
            and bool(re.fullmatch(
                re.escape(ENDPOINT) + r"(?:-pooler)?\.[a-z0-9.-]+\.neon\.tech",
                parsed.hostname or "",
            ))
            and parsed.path == "/neondb"
            and parsed.port in (None, 5432)
            and parse_qs(parsed.query).get("sslmode") in (["require"], ["verify-full"])
        )
    except (ValueError, TypeError):
        return False


def fetch_plan(code, secret):
    # Fixed destination, GET only, no redirects, no provider payload in output.
    req = Request("https://api.paystack.co/plan/" + code, method="GET", headers={
        "Authorization": "Bearer " + secret,
        "Accept": "application/json",
        "User-Agent": "StockBridge-Staging-ReadOnly-Preflight/1.0",
    })
    with build_opener(NoRedirect()).open(req, timeout=20) as response:
        if response.status != 200:
            return None
        raw = response.read(131073)
        if len(raw) > 131072:
            return None
        return json.loads(raw)


def validate(env=None, getter=None):
    env = os.environ if env is None else env
    getter = fetch_plan if getter is None else getter
    result = {"Paystack public key mode": False, "Paystack secret key mode": False}
    result.update({name: False for name, *_ in PLANS})
    result.update({"Subscription flags": False, "Staging database isolation": False})
    # Fail closed BEFORE inspecting credentials or making any network request.
    if not (env.get("VERCEL_PROJECT_ID") == PROJECT
            and env.get("VERCEL_ENV") == "production"
            and env.get("CUSTOMER_HOST") == HOST):
        return result
    result["Staging database isolation"] = isolated(env)
    result["Subscription flags"] = all(
        env.get(k, "").strip().lower() == "true"
        for k in ("SUBSCRIPTIONS_ENABLED", "BILLING_PROVIDER_ENABLED")
    )
    public = env.get("PAYSTACK_PUBLIC_KEY", "")
    secret = env.get("PAYSTACK_SECRET_KEY", "")
    result["Paystack public key mode"] = bool(re.fullmatch(r"pk_test_[A-Za-z0-9]+", public))
    result["Paystack secret key mode"] = bool(re.fullmatch(r"sk_test_[A-Za-z0-9]+", secret))
    if not all(result[k] for k in result if k not in {p[0] for p in PLANS}):
        return result
    codes = [env.get(key, "") for _, key, _, _ in PLANS]
    if len(set(codes)) != 4:
        return result
    for (name, _, amount, interval), code in zip(PLANS, codes):
        if not re.fullmatch(r"PLN_[A-Za-z0-9]+", code):
            continue
        try:
            payload = getter(code, secret)
            data = payload.get("data") if isinstance(payload, dict) else None
            result[name] = bool(
                isinstance(data, dict) and payload.get("status") is True
                and data.get("plan_code") == code and data.get("domain") == "test"
                and type(data.get("amount")) is int and data["amount"] == amount
                and data.get("currency") == "NGN" and data.get("interval") == interval
            )
        except Exception:
            result[name] = False
    return result


def main():
    try:
        results = validate()
    except Exception:
        results = {label: False for label in (
            "Paystack public key mode", "Paystack secret key mode",
            *(name for name, *_ in PLANS), "Subscription flags", "Staging database isolation",
        )}
    for label, passed in results.items():
        print(label + ": " + ("PASS" if passed else "FAIL"), flush=True)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
