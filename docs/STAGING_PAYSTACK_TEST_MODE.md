# Staging subscription Test Mode

This procedure is for the existing `stock-bridge-staging` project only. It does
not authorize a deployment, database mutation, or provider transaction by itself.
Basic supports 1 business (NGN 3,000/month, 30,000/year), Plus 2 (5,000/50,000),
and Pro 5 (10,000/100,000). Existing paid periods and legacy access are retained.
Same-interval higher-tier upgrades start a new full-price checkout. Downgrades
and all interval changes are scheduled at the existing paid-period end. No
proration is introduced. Multi-business slots retain business-ID order; suspended
businesses consume slots. Returning to one business requires the existing explicit
selection, renewed after each paid multi-business generation. No records are deleted.

## Separate authorization gates

Ordinary Preview provider operations and webhooks remain disabled. A Preview must
meet every server-side check in `paystack_policy.staging_preview_authorized`:

* Project `prj_yeCWXPd44jShVUGHAgJu9khMjOMv`, environment and target `preview`.
* Repository owner `samuella-code`, repository slug `stock-bridge`.
* Git branch `release/staging-phase6`, exact approved 40-character commit SHA.
* Explicit opt-in and both billing flags enabled; matching test credentials.
* Approved HTTPS callback origin `https://stock-bridge-staging.vercel.app`.

Enable access to System Environment Variables in Vercel project settings. The
Flask runtime must receive `VERCEL_PROJECT_ID`, `VERCEL_ENV`, `VERCEL_TARGET_ENV`,
`VERCEL_GIT_REPO_OWNER`, `VERCEL_GIT_REPO_SLUG`, `VERCEL_GIT_COMMIT_REF`, and
`VERCEL_GIT_COMMIT_SHA`. Missing/conflicting metadata denies authorization.
Do not manually spoof these platform variables. Request Host/headers/query data
never authorize a deployment. CLI deployments lacking Git metadata are denied.

Only after approval, scope these settings to Preview / `release/staging-phase6`:

* `STAGING_PAYSTACK_TEST_ENABLED=true` (exact spelling/case).
* `STAGING_PAYSTACK_APPROVED_SHA=<approved release commit SHA>`.
* `STAGING_PAYSTACK_CALLBACK_ORIGIN=https://stock-bridge-staging.vercel.app`.
* `SUBSCRIPTIONS_ENABLED=true`, `BILLING_PROVIDER_ENABLED=true`.
* `PAYSTACK_PUBLIC_KEY`, `PAYSTACK_SECRET_KEY`: matching Test Mode keys, server-side.
* Six separately provisioned test plan codes: `PAYSTACK_BASIC_MONTHLY_PLAN`,
  `PAYSTACK_BASIC_YEARLY_PLAN`, `PAYSTACK_PLUS_MONTHLY_PLAN`,
  `PAYSTACK_PLUS_YEARLY_PLAN`, `PAYSTACK_PRO_MONTHLY_PLAN`, `PAYSTACK_PRO_YEARLY_PLAN`.
* `CUSTOMER_HOST=stock-bridge-staging.vercel.app` for other customer links.

Never configure live credentials in Preview. Plans are fetched and validated for
code, domain=test, integer amount, NGN currency and monthly/annually interval.
No plan codes or credentials belong in source control. `STAGING_PAYSTACK_READONLY_PREFLIGHT`
is not read by the application and does not authorize payment operations.
Production behavior is unchanged; this exception enables subscriptions only.
Legacy callbacks/status activation and legacy webhook activation stay blocked
in Preview. The retired purchase routes remain retired.

## Database identity before testing

Resolve the exact deployment's effective connection using `NEON_DATABASE_URL`,
then `NEON_POSTGRES_URL`, then `DATABASE_URL`. Use protected metadata to map the
endpoint to staging Neon project `empty-recipe-08350132` and its intended branch.
Reject production project `icy-night-30356964` and all unknown endpoints. An
SQL database name, health result, or self-declared NEON_PROJECT_ID is insufficient
by itself. Do not print connection strings. Stop if integration metadata cannot
establish identity; request a separately approved protected read-only diagnostic.

The existing preflight additionally pins the previously reviewed staging endpoint
in `scripts/staging_paystack_preflight.py`. If the endpoint changes, review its
control-plane identity and update this pin explicitly; never bypass it.

## Read-only plan preflight

After explicit approval for read-only Paystack metadata requests, run
`python -m scripts.staging_paystack_preflight` in a trusted environment with the
actual deployment metadata and configuration. Do not dump or pull protected
secrets to ordinary files. The command imports no Flask app, opens no SQL
connection, performs GET-only plan lookups, refuses redirects, and prints fixed
PASS/FAIL labels. It validates all six distinct plans. No transaction is created.
It preserves the separately validated staging Production preflight path.

## Release and callback workflow

1. Review/test code, obtain release approval and know the final integration SHA
   (a merge commit has a different SHA from its feature commit).
2. Approve that exact SHA and branch-scoped test configuration before deployment.
3. Deploy only to the staging Vercel project. Check actual runtime metadata using
   protected tooling; do not publish a diagnostics endpoint.
4. Verify database isolation and all six provider plans before transaction testing.
5. Assign the staging hostname only to the approved deployment under separate
   approval. Browser checkout and callback must use the staging origin together.
   Callback is `/plans/callback`; it requires the existing authenticated session.
6. In Paystack Test Mode configure HTTPS staging `/payments/webhook`. Ensure the
   webhook is reachable through Vercel protection without disabling application
   HMAC-SHA512 verification. Never place bypass secrets in public documentation.
7. Perform only separately authorized synthetic staging transactions.

Release-scoped authorization permits redeployments of the same approved commit;
there is no immutable-host allowlist or post-deployment env approval loop.
Environment changes affect new deployments only. Moving an alias or changing
project settings does not revoke old immutable deployments. Old approved releases
can retain test-mode authorization. Immediate revocation requires separately
approved platform access restrictions or runtime authorization infrastructure.
Do not assume an alias rollback rolls back the billing ledger. Roll back only to
schema-compatible code, retain records, reconcile uncertain provider operations,
and never retry ambiguous charges blindly. No build-time migration is authorized;
retain the staging migration-skip safeguard.
