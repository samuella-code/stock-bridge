# Controlled staging inventory email testing

Status: local implementation only. No deployment, configuration change, hosted
SQL, or real SMTP delivery is authorized by this document.

## Root cause and prerequisites

The observed disabled response comes from the existing INVENTORY_EMAILS_ENABLED
rollout gate. The staging variables were scoped to the staging project's
Production target while its active deployment was Preview. SMTP configuration
alone does not enable inventory delivery. Correcting scope requires separate
approval and a fresh deployment; do not activate the old unrestricted code.

Verify the selected database endpoint and independently establish staging-only
database identity before any hosted testing. No migration is required: cohort
markers use the existing JSON payload in newly created email_outbox rows.

## Fail-closed staging test policy

The existing global INVENTORY_EMAILS_ENABLED remains false by default.
STAGING_INVENTORY_EMAIL_TEST_ENABLED also defaults to false. Both must be true
for controlled staging delivery. Staging delivery requires the exact metadata below, the explicit test opt-in
and the known staging email origin. Missing, partial, unknown or inconsistent
deployment identity is blocked, regardless of whether the origin looks like
staging. Other Preview projects are blocked.

All of these exact metadata values are required:

- VERCEL_PROJECT_ID=prj_yeCWXPd44jShVUGHAgJu9khMjOMv
- VERCEL_ENV=preview
- VERCEL_TARGET_ENV=preview
- VERCEL_GIT_REPO_OWNER=samuella-code
- VERCEL_GIT_REPO_SLUG=stock-bridge
- VERCEL_GIT_COMMIT_REF=release/staging-phase6

Do not spoof system variables. Production-target deployments of the staging
project are blocked. Unrestricted production delivery requires the existing production project ID
from app.config_safety, VERCEL_ENV=production and VERCEL_TARGET_ENV=production,
with staging test opt-in absent/false and a non-staging origin. If present, Git
owner/slug/provider and VERCEL must also be consistent. Missing Git metadata is
permitted for legitimate production deployments without Git provenance, but
missing any of the three core identity fields blocks delivery. Verified
production retains the existing dispatcher behavior. Local/unidentified
environments no longer receive an unrestricted production fallback. The application does not identify
production by a customer-controlled request header.

Additional operator settings, scoped only to the approved staging Preview:

| Setting | Purpose |
| --- | --- |
| STAGING_INVENTORY_EMAIL_TEST_ENABLED | Exact true opt-in; otherwise disabled |
| STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS | 1–20 comma-separated approved test mailbox addresses; no wildcard/domain matching |
| STAGING_INVENTORY_EMAIL_TEST_RUN_ID | Fresh lowercase 32-character hexadecimal identifier; never reuse a previous run |
| STAGING_INVENTORY_EMAIL_TEST_STARTED_AT | UTC YYYY-MM-DDTHH:MM:SSZ; starts a fixed one-hour window |
| INVENTORY_EMAIL_BASE_URL | https://stock-bridge-staging.vercel.app |

Only simple mailbox addresses are supported, without display names or quoted
local parts. Matching is case-insensitive; whitespace around list entries is
trimmed. Empty, duplicate and malformed entries reject the entire policy.

Retain SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL,
SMTP_FROM_NAME and SMTP_USE_TLS. Never put real credentials in source, tests or
logs. The bearer worker additionally requires its existing private
INVENTORY_EMAIL_DISPATCH_SECRET. The optional durable trigger and scheduler are
not provisioned by this implementation.

## Backlog protection

Only newly queued events for allowlisted owners during an active approved window
receive an internal _staging_test_run marker. A dispatcher must match that marker,
the active run ID, the creation-time window and an approved recipient. These
filters apply to selection, exhausted-attempt cleanup and the guarded claim
UPDATE. Thus old rows are not claimed, retried, failed, suppressed, deleted or
otherwise adopted into the test cohort. Recent untagged jobs also remain intact.
The recipient and cohort policy are checked again immediately before SMTP.

Create new synthetic products and stock events only under separate authorization
and only for staging-owned test accounts with controlled mailboxes. Existing
preferences and verified-owner eligibility still apply. Do not use real customer
mailboxes. Do not use production transactions to exercise this feature.

Existing browser and bearer worker entry points share the same policy. Each
browser page can request up to ten sends, but only eligible approved cohort jobs
can be claimed. Never invoke dispatch merely to inspect status: it is a mutating
operation and may send mail. No read-only status endpoint was added.

## Separate future activation procedure

1. Review this patch and complete isolated PostgreSQL verification first.
2. Verify the deployed database identity and inspect the backlog read-only under
   separate authorization. Review jobs without releasing them.
3. Confirm the approved code is deployed only to staging with delivery disabled.
4. Separately approve Preview-only configuration, a fresh run ID, start time and
   controlled recipient addresses. Allow time for deployment within the hour.
5. Only after explicit approval, create fresh synthetic events. Observe the
   existing sanitized delivery diagnostics and verify only test recipients.
6. Stop at expiration or separately disable the staging test flag. Never widen the
   list or enable unrestricted staging delivery to clear a backlog.
7. A later test run requires a new run ID and time window. Prior run jobs remain
   untouched; decide their disposition separately, without bulk replay/deletion.

## Guarantees and verification limits

Event uniqueness, claim tokens, leases, attempt ceilings, retry backoff and
preference checks are preserved. SMTP remains at-least-once: a provider accepting
mail before a worker crash or ambiguous closure failure can result in a later
repeat. This patch does not claim exactly-once delivery or real provider success.
A failed current-cohort attempt retries only when due and within the session.
Expiration preserves unsent jobs; no automatic cleanup or reset is performed.

Tests here use disposable in-memory SQLite with mocked SMTP/HTTP plus PostgreSQL
SQL compilation. No PostgreSQL server/tools or verified isolated cluster were
available in the coding environment. Actual PostgreSQL execution, concurrent
claim behavior, Vercel metadata exposure and real delivery remain unverified.
Do not run the Phase 9 mutation suite or change its workspace to bypass its
independent safety gates. No production, billing, admin or customer transaction
logic is changed by this feature.
