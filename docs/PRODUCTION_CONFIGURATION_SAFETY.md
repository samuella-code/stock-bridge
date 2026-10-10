# Production startup configuration safety

The customer and admin blueprints are mounted in the same Flask app through
`wsgi.py`. The existing deployment guide describes two hostnames pointing to that
same deployment/database. Actual admin hostname assignment has not been verified
by this change; no routing or host configuration is changed.

## Identity and scope

- Real production project: `prj_ahZe3mfUk4MvejILpj9Md9QPP4fd` (`ogefin/stock-bridge`).
- Isolated staging project: `prj_yeCWXPd44jShVUGHAgJu9khMjOMv`.
- Strict configuration checks require the real production ID **and**
  `VERCEL_ENV=production`. The production-target staging alias is still staging.
- Hosted startup with a missing/unknown project ID, missing/unknown environment,
  or conflicting target fails with a generic message. Custom target names are
  unsupported until explicitly reviewed; no identity is inferred from Host.
- Local development with no Vercel identity retains its existing SQLite/test
  behavior. `FLASK_ENV=production` without verified identity is rejected.
- Production-project previews/development retain their existing behavior.
- `TESTING=true` cannot bypass validation of an identified real production.

`VERCEL_PROJECT_ID` and `VERCEL_ENV` must be exposed during function startup,
not merely during the build. Verify system-variable availability before approving
any deployment. No project settings were changed by this implementation.

## Signing and database checks

Production requires an explicit nonempty `SECRET_KEY` matching the effective Flask
configuration. The application default and known repository test-fixture signing
keys are rejected. This is not an entropy estimator or secret rotation mechanism.
Never paste signing values into reports or chat.

Production database precedence is unchanged:
`NEON_DATABASE_URL` → `NEON_POSTGRES_URL` → `DATABASE_URL`.
One must provide an explicit PostgreSQL URL with username, password, host and
nonempty database name. Legacy PostgreSQL schemes normalize to the existing
psycopg driver. Unsupported schemes, malformed URLs, invalid ports, SQLite
fallbacks and mismatches with effective Flask configuration fail before
`db.init_app`. The guard does not make a SQL connection or check database state.
A syntactically valid URL does **not** prove the correct Neon resource, credential
validity, TLS policy, connectivity or migration revision. These remain read-only
release-preflight checks.

Parser errors never disclose the supplied URL. Failures use fixed messages
without configuration values. No secrets are persisted.

## Staging migration protection

`scripts/vercel_build.py` is unchanged. Staging must still have the exact
`STOCKBRIDGE_SKIP_BUILD_MIGRATIONS=true` setting and verified staging project ID.
The existing check-only command remains available:

```sh
python -m scripts.vercel_build --check-only
```

Run only in an appropriately configured, approved environment. The existing
success marker remains unchanged. Startup validation never runs migrations and
never sets rollout flags or environment variables.

## Focused verification

```sh
python -m pytest tests/test_config_safety.py tests/test_vercel_build.py tests/test_safe_error_handling.py tests/test_runtime_diagnostics.py tests/test_catalogue.py -q --disable-warnings
```

Tests use synthetic values, disposable SQLite data or mocked SQL initialization;
production/staging SQL connections and real migrations are not executed.
