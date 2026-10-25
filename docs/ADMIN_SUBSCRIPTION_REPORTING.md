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

Entitlement reads now use four SELECTs per batch of at most 200 accounts. CPU and
memory still scale with customer/business facts. No hosted profiling was performed. No browser/mobile viewport or PostgreSQL verification is claimed.
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

### Earlier bounded-query proposal (historical; implementation below)

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
The proposal above records the prior review. The uncommitted implementation below
now changes the shared entitlement loader/evaluator for review.


## PR 39 uncommitted bulk entitlement performance review

Base: 3bb901bc0b248f5b42203665629c18439fb39db5, on
feat/admin-subscription-reporting. No commits, dependencies, schema, provider calls,
configuration or billing processing changes are part of this review patch.

Scalar and bulk loading now call the same _account_access and _business_access
rules. Current-period eligibility and selection-generation predicates are shared.
Selection generation still means highest ID of a started Plus/Pro row, including
expired/failed/future-started rows; subscription winner still means latest eligible
period start then highest ID. Those two orderings intentionally differ.

EntitlementSnapshot captures one naive UTC instant for all of its evaluations.
Snapshots are local variables in admin reporting, never stored in Flask globals,
process caches or sessions. Account Access results are computed once per user and
reused for business checks. The API keeps reusing its overview account totals.
User identity membership is checked before a snapshot result is returned.

Four SELECTs per batch of <=200 unique loaded User IDs:
1. AccountBilling rows.
2. Only effective subscription winners, selected by SQL ROW_NUMBER partitioned by
   user ID, with existing status and half-open period filters and tie ordering.
3. Ordered owned business IDs (not entire business objects), needed for primary
   validation, counts and slots. Suspended IDs still consume their original slots.
4. Only matching selection-event user IDs, joined to a grouped MAX subscription-ID
   generation subquery. Historical subscription/event objects are not loaded.

The largest repeated ID parameter list is bounded at 200 per clause (the event
query uses two such clauses). Empty snapshots issue no entitlement reads. For N
accounts, entitlement loading costs 4*ceil(N/200) SELECTs; account and business
checks after loading issue none. Query bounds do not imply constant memory, CPU,
rows scanned or latency. Loaded customer rows must not be expired before evaluation.
Notification candidates eagerly load owners, avoiding per-business owner lookups.
No isolation/consistency guarantee beyond the existing transaction is introduced.

Tests use explicit expected outcomes as well as scalar/bulk comparison. Coverage
includes all plans/intervals, tie-breaking, period/trial boundaries, legacy overlays,
user restrictions, invalid primary ownership, suspended slot consumption, foreign
businesses/events, stale and future-failed selection generations, no-query business
checks, next-request refresh, and 201-account batching (8 entitlement SELECTs).

### Local measured results

Same synthetic layout and cold ORM method described above. Before samples are the
previously captured measurements of the reviewed base, not a fresh baseline run.
After samples come from the final guarded regression run. SQL counts are exact per
request across three samples. Milliseconds below are three-sample medians, including
the first cold-template sample. These are local Flask test-client timings, not hosted
latency; no PostgreSQL execution/performance claim is made.

| Endpoint | Customers | Before SQL | After SQL | Before ms | After ms |
|---|---:|---:|---:|---:|---:|
| /admin/ | 10 | 59 | 33 | 21.230 | 12.792 |
| /admin/reports | 10 | 44 | 18 | 15.082 | 8.327 |
| /api/admin/reports | 10 | 66 | 40 | 19.946 | 14.829 |
| /admin/notifications | 10 | 69 | 15 | 20.940 | 7.627 |
| /admin/ | 100 | 329 | 33 | 140.384 | 17.993 |
| /admin/reports | 100 | 314 | 18 | 96.785 | 11.536 |
| /api/admin/reports | 100 | 336 | 40 | 102.712 | 18.616 |
| /admin/notifications | 100 | 579 | 15 | 132.604 | 11.051 |

Final guarded regression: 332 passed, 4 migration tests deselected, exit 0,
92.01 seconds; 12,349 warnings (primarily existing deprecations). All measured GET
statements are SELECT. Endpoint tests assert exact fixed counts at both 10 and 100
customers, each with three businesses. The SQLite runner blocks external sockets,
SMTP, subprocesses and PostgreSQL, disables dotenv, starts with a clean environment,
and permits SQLite only in memory or the dedicated disposable test directory.
No migrations were executed. Browser and hosted validation remain outstanding.
