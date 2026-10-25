# Admin subscription reporting (review patch)

Base: `4f8d700d6d1bc9957fecc3f07bef9e7941c2b627`.
No schema, billing processing, plan prices, entitlements or historical records change.

## Definitions

- Current subscription accounts: one ordinary customer per effective paid-period
  subscription, using `effective_access` (latest period start, then subscription ID).
  The account must have `can_write` at a single captured UTC instant. Admin/dual-role,
  suspended and unverified users are excluded. Business counts and business suspension
  do not multiply or remove an otherwise eligible account. All six Basic/Plus/Pro
  monthly/yearly buckets are emitted, including zeros. Paid-through cancelled,
  cancel-at-period-end and past-due records follow existing entitlement rules;
  expired periods are excluded. Legacy accounts upgrading to a subscription count once
  in their effective subscription bucket. Trial-only and legacy-only accounts do not
  count as subscription accounts. These are entitlement totals, not renewal forecasts.
- Subscription collections: durable Payment rows with product=subscription,
  status=success, provider=paystack, currency=NGN and non-null paid_at. Account and
  business ownership, subscription linkage, plan, interval and stored amount must match
  the RecurringSubscription. Unknown plans/intervals, zero/negative amounts and
  unlinked/inconsistent rows are excluded. Amounts use stored historical values, not
  today's plan prices. Each payment primary key counts once; business joins cannot
  multiply it. Distinct successful renewal references are separate collections.
- Dashboard collections are all time. Reports use paid_at >= UTC start and < UTC end;
  custom end dates include that entire calendar day. Account totals are always current,
  independent of report dates. Collections from suspended customers remain financial
  history. This metric is collected revenue, not MRR, accrued revenue or projected ARR.
- Existing legacy lifetime metric keys, calculations and scope are unchanged. The
  business-status label says "Businesses without active status" and retains the
  existing subscription_status != active calculation across all businesses; it is not
  a legacy-only or subscription entitlement count. Subscription and legacy collections are never combined.

## API compatibility

No in-repository consumer of the admin payment/report API was found outside tests.
Unknown external consumers may exist. Therefore:

- GET /api/admin/payments keeps its historical lifetime default and five row fields.
- Explicit product=lifetime behaves the same; product=subscription opts into the
  existing HTML subscription ownership scope and status filters, and adds user_id,
  plan_code, billing_interval and paid_at. Both views order by created_at/id descending;
  API remains capped at 100 while HTML retains pagination. Unknown products return 400.
- Dashboard/report overview keys remain. New current_subscription_accounts and
  subscription_payments fields are additive. API reports additionally expose
  period_metrics matching HTML report calculations. Existing top-level collections
  remain all time. Invalid report ranges now return 400 rather than being ignored.
- No legacy API row was silently reclassified as a subscription payment.

## Alerts and safety

The old active business marker without a lifetime payment is not proof of an access
problem. Notifications now identify legacy active markers without current writable
entitlement, using effective_access with the business context (including slot/selection
rules). Suspended customers/businesses are excluded from this billing alert. Before
subscription rollout, verified legacy payment evidence is retained as a fallback.
These alerts do not grant or revoke access, contact providers or modify data.

Admin decorators, sessions, CSRF, suspension handlers, provider verification and
payment-processing code are untouched. Reporting GETs are tested to issue SELECT only.

## Limits and acceptance

Shared entitlement evaluation queries per customer/business. Large-installation
performance requires measurement on an approved isolated dataset; no hosted profiling
was performed. No browser/mobile viewport or PostgreSQL verification is claimed.
Tests use synthetic data and disposable SQLite with network/SMTP/provider/subprocess
and non-SQLite connections blocked. Migration tests are deliberately excluded.
All changes remain uncommitted pending review. No release or deployment is authorized.

## Local query-count review (25 October 2026)
Measured isolated in-memory SQLite requests with 10/100 customers and three businesses
per customer, cycling through all six plan/intervals. Each account has a valid paid
period; Basic accounts have explicit primary-business selection. Only each primary
business has the historical active marker. No payment rows are seeded.
Three requests per endpoint/dataset, ORM session and Flask-Login cached user cleared
before every request; first sample includes cold template compilation. Counts include
all SQL during the GET, including authentication. Every observed statement was SELECT.
Timings are end-to-end Flask test-client milliseconds, not database-only or hosted
latency. Below are medians of all three samples; raw samples are in the measurement
JSON and logs. Environment/process guards match the regression runner.
| Endpoint | Customers | Before queries | After queries | Before median ms | After median ms |
|---|---:|---:|---:|---:|---:|
| /admin/ | 10 | 59 | 59 | 20.248 | 21.230 |
| /admin/reports | 10 | 44 | 44 | 15.935 | 15.082 |
| /api/admin/reports | 10 | 97 | 66 | 35.662 | 19.946 |
| /admin/notifications | 10 | 69 | 69 | 24.550 | 20.940 |
| /admin/ | 100 | 329 | 329 | 101.871 | 140.384 |
| /admin/reports | 100 | 314 | 314 | 106.299 | 96.785 |
| /api/admin/reports | 100 | 637 | 336 | 210.561 | 102.712 |
| /admin/notifications | 100 | 579 | 579 | 181.806 | 132.604 |

API reports now passes its already computed current-account totals into period_metrics.
A regression test proves one computation and identical nested/top-level account totals.
The business-status label is corrected without changing the existing SQL calculation.

### Bounded-query proposal (not implemented)

Linear per-account SQL remains. Do not replace effective_access with separate admin
SQL rules. A follow-up should introduce a shared batch entitlement interface in the
entitlement module: bulk-load account billing, the effective subscription winner
(identical start/end/status filters and start-descending/ID-descending ordering), owned
business/primary/slot data and selection events for a captured UTC instant. Extract
one shared evaluator over that snapshot; both scalar effective_access and batch callers
must delegate to it. Cover legacy overlays, trial boundaries, cancelled paid-through
periods, suspended/unverified/admin users, invalid primary IDs and business selection.
Use fixed set-based reads per request, or explicitly bounded batches for parameter
limits; do not materialize an unbounded IN list. Reuse the immutable snapshot across
API overview/period summaries and notification business checks. Prove scalar/batch
parity and query bounds on SQLite and later approved isolated PostgreSQL before use.
This is a proposal requiring separate review; entitlement code is unchanged here.
