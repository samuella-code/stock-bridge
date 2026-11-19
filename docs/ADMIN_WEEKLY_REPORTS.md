# Weekly platform reports — staged backend, delivery off

The report page, CSV and weekly email use the same verified payment rules. Bars
sum exactly to Collected. Account counts exclude employees and administrators;
Active and Paying are current owner totals, not historical snapshots. Email
verification counts are owner verifications during the selected date window.
Week boundaries are Monday–Sunday in Africa/Lagos (WAT). ISO query links remain
supported; visible date fields use day/month/year. The existing API report
contract is unchanged.

## Persistence and consent

Prepared migration: `0021_admin_weekly_reports`, after `0020_admin_customer_tools`.
It adds `admin_report_preference` and `admin_report_delivery`. It has not been run
by this change. Do not deploy the page before a separately approved schema review
and migration; retain the build-time migration skip guard.

`POST /admin/reports/weekly-email` requires the existing admin session, MFA guard,
verified email and CSRF token. It changes only the signed-in admin's preference.
A per-user lock and preference version reject stale concurrent updates. Changes
are audited. Security/auth-version changes require fresh opt-in. A newly required
MFA policy requires enrolment and fresh MFA-verified report consent. There is no
email send during the preference request.

## Delivery activation — separate approval required

No configuration, scheduler, recipient or SMTP setting is enabled by this patch.
The rollout is staging-only and fails closed for local, missing, conflicting or
production deployment identity. Inventory and staff email gates are unchanged.

Required configuration:

| Setting | Required scope |
| --- | --- |
| `ADMIN_REPORT_EMAILS_ENABLED` | Exact string `true` after approval |
| `ADMIN_REPORT_EMAILS_APPROVED_SHA` | Exact deployed 40-character lowercase SHA |
| `ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS` | Comma-separated exact lowercase, verified admin addresses; no wildcard matching |
| `ADMIN_REPORT_EMAIL_ORIGIN` | Exact staging origin `https://stock-bridge-staging.vercel.app` |
| `ADMIN_REPORT_EMAIL_DISPATCH_SECRET` | Dedicated secret of at least 32 characters, stored securely |
| Existing SMTP settings | Existing approved sender and transport |

Positive Vercel project, Preview environment, target, repository and
`release/staging-phase6` branch identity are also mandatory. Production remains
blocked even if the flag is accidentally enabled there.

## Scheduler integration

`GET /admin/internal/reports/weekly-dispatch` accepts only a dedicated bearer
secret. Cookies and admin browser sessions confer no worker authority. Each
invocation delivers at most one due report and returns a small status only.
The dispatcher is due on Monday from 08:00 to before 12:00 WAT. It sends the
previous complete Monday–Sunday report; missed old weeks are not replayed.

An approved scheduler should start at 08:00 WAT (07:00 UTC) each Monday and
repeat within that morning until `idle`; this drains every opted-in admin
through separate bounded invocations. Alternatively an approved scheduled worker
can run `flask --app wsgi:app admin dispatch-weekly-reports --limit 20`, repeating
bounded batches as needed. Do not put secrets in command arguments or URLs.
No scheduler has been created. Vercel native Cron runs on Production deployments,
so a Preview staging rollout needs a separately approved Preview-capable
scheduler rather than silently adding a Production cron job. See the official
[Flask deployment guidance](https://vercel.com/kb/guide/ship-a-flask-app-on-vercel).

The claim for `(admin, reporting week)` and its audit entry commit before report
generation or SMTP. Recipient, consent, verified email, current admin access,
auth version, recipient digest and deployment gate are rechecked before send.
Emails contain aggregate metrics and an authenticated report link, never a
customer list, payment references or full customer emails. The new transport call
uses a five-second socket timeout; existing email callers keep their default.

Any SMTP exception is `unknown`, because delivery may already have happened.
False transport results are `failed`; neither is automatically retried in the
same week. A process interrupted after claiming leaves `sending`, also excluded
from retries. A successful SMTP handoff is `sent`, not a guarantee of inbox
delivery. Audit/commit failure before transport prevents sending; failure after
transport leaves an uncertain durable claim. Never reset those claims blindly.

## Disconnected verification

Tests use disposable SQLite only, mocked SMTP and synthetic deployment metadata.
They cover consent/CSRF/version conflicts, no immediate preference send, role and
auth revocation, allowlists, exact identity, Monday/WAT boundaries, post-render
rechecks, durable uniqueness, repeat invocations, unknown sends and report/CSV
agreement. PostgreSQL DDL/query compilation is offline, not a live concurrency or
migration test. A hosted migration and scheduler smoke test remain separate
approved rollout steps.
