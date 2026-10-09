# Staging migration control

Target only `ogefin/stock-bridge-staging`, project
`prj_yeCWXPd44jShVUGHAgJu9khMjOMv`. Do not change production settings.
The repository build command remains `python -m scripts.vercel_build`.
The previous local-config override did not take effect; its exact rejection
mechanism remains unproven. Do not rely on that override for migration safety.

## Required configuration (separate approval required)

In the staging project's Environment Variables settings:

1. Enable access to System Environment Variables, ensuring `VERCEL_PROJECT_ID`
   and `VERCEL_ENV` are available to the build.
2. Set `STOCKBRIDGE_SKIP_BUILD_MIGRATIONS` to exactly `true` in the **Production**
   environment, because the staging live alias uses a production-target build.
3. Set the same value in Preview if staging preview builds will be used.
4. Do not add this variable to production or team-wide shared configuration.

The setting is non-secret. Do not print or export other environment values.
Do not manually override Vercel's system project identity.
A staging build missing the setting fails before migrations. A skip setting on
another project, invalid identity, missing production identity or conflicting
production target fails. Non-staging production with a valid project ID and no
skip setting retains the existing Flask upgrade command and failure exit code.
Project IDs are configuration identifiers, not database-isolation proof: verify
the staging database connection separately before authorizing any deployment.

## Pre-deployment check

Confirm branch, approved commit, clean tree, linked staging project, system-variable
exposure, and environment scopes before pushing (a push may trigger a build).
For a configuration-only local check in Windows PowerShell:

```powershell
$env:VERCEL_PROJECT_ID = 'prj_yeCWXPd44jShVUGHAgJu9khMjOMv'
$env:VERCEL_ENV = 'production'
$env:STOCKBRIDGE_SKIP_BUILD_MIGRATIONS = 'true'
python -m scripts.vercel_build --check-only
if ($LASTEXITCODE -ne 0) { throw 'Migration policy check failed' }
```

These values affect the current terminal only; they do not configure Vercel.
Use a fresh terminal for this check to avoid replacing existing local variables.
This validates the code's decision, not remote environment configuration.
Check-only never imports the Flask application, connects to a database or
executes the migration subprocess. Never use the normal build command to test
production policy; use check-only or mocked tests.

## Build evidence after a separately approved deployment

Require this exact line in the actual deployment's build logs:

```text
STAGING MIGRATIONS SKIPPED: project identity verified; explicit skip=true; database command not invoked.
```

The script may still run; its verified staging branch returns before Flask is
invoked. READY alone is not proof. If the marker is missing, stop verification
and investigate; do not rerun a build or migrations to obtain it.
`MIGRATION POLICY ERROR` means a failed build, not successful prevention approval.
A check-only success does not establish the outcome of a prior deployment.

## Focused tests

```text
python -m pytest tests/test_vercel_build.py -q
```

All migration subprocesses are mocked. No database connection or schema changes
are needed. The older `tests/test_catalogue.py::test_vercel_build_migration_guard`
assumes production can omit project identity; that expectation is superseded by
the approved fail-safe requirement and needs a separately approved update before
the full suite can be claimed passing. It is intentionally unchanged in this
three-file scope.
