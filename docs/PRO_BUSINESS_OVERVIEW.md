# Pro Multi-business Overview

`/business-overview` is a read-only report for current paid Pro owners. Pro
continues to include selected-business Plus Insights. No Staff Access, Smart
Restock, plan-price changes, migrations or new dependencies are introduced.

## Authorization and scope

Capture one UTC instant per operation and use the shared authoritative
EntitlementSnapshot via insights_context(plans=('pro',)). Include only owned,
permitted, non-suspended businesses in the authoritative Pro slots. Suspended
businesses still occupy their existing slot; this report never reallocates it.
Paid legacy + Pro overlays qualify during their current paid period. Basic,
Plus, trial, expired and legacy-only accounts do not gain overview access.

Reuse the same request-local context for the selected-business header and
switcher without session repair or a second entitlement evaluation. The page
explicitly reports ALL permitted businesses, independently of that header.
Missing/foreign/locked/suspended selection follows the existing permitted
session → primary → first-business fallback. No business ID supplied by a
browser expands the scope. Denied contexts contain no business choices or
financial records. Results are never cached across requests.

All overview responses, including denied/redirected requests, use private,
no-store. GETs do not start a trial or write database records. A current Pro
owner with no permitted non-suspended businesses receives an empty overview
without financial queries; suspended/unavailable business names are omitted.

## Calculations and grouped reads

Both scalar financial_totals and grouped_financial_totals share the same SQL
aggregate columns and arithmetic. For each authorized business and half-open
UTC range [start, end):

* Revenue = sum(sale-item quantity × saved sale price).
* COGS = sum(quantity × saved sale-time unit cost).
* Estimated gross profit = revenue − COGS.
* Recorded operating expenses = sum(non-voided expense amounts).
* Estimated result after recorded expenses = gross profit − expenses.

Voided sales and expenses are excluded. Never replace historical costs with
current product costs. Aggregate expenses separately from sales: joining the
two would multiply amounts. Stock purchases are not deducted again. These
figures are recorded estimates, not complete accounts, causal explanations or
forecasts; unrecorded expenses cannot be inferred.

Grouped queries return one row per business. Fill absent businesses with zero
recorded activity, preserving them in the visible list. Combined figures sum
those exact business rows (including preceding totals), without another report
query. At the current Pro limit of five businesses, there are four financial
SELECTs: sales and expenses for each of two periods. Generic internal batching
limits each ID list to 200 (two queries per period/batch); no scalar report runs
per business. A unit test covers the 200/201 boundary with synthetic records;
that internal helper test is not an authorization grant above Pro's limit.

## Dates and comparisons

Insights and Overview share date handling: explicit dates beat presets; retain
Reports validation, native controls and error associations. Inclusive selected
calendar dates become half-open UTC intervals. Compare against the immediately
preceding equal-length period. Reject a start too early to represent that
preceding period. Existing Reports behavior and maximum range are unchanged.

Reuse the Insights comparison evaluator. A period including today/future UTC
days is labelled incomplete; directional comparisons are withheld. Positive
preceding values permit percentage changes; zero has no percentage baseline;
negative baselines show amount changes only. Zero saved costs in either period
warn for the affected business and combined report, retain the arithmetic and
suppress profit direction. Other businesses retain their own valid comparisons.

## Presentation and verification

Pro-only navigation opens a responsive overview with combined cards and
business comparison cards over the same range, including empty businesses.
Native date controls, mobile appearance, Help behavior and preset JS are reused.
The new CSS is scoped to the Overview and stacks business cards below 820px.

Tests use guarded disposable :memory: SQLite only. Block PostgreSQL/other
DB targets, outbound services, subprocesses and dotenv discovery. Preserve
existing Insights, Reports, financial, entitlement, multi-business, suspension
and assistant regressions. Exclude the previously identified three existing
file-backed SQLite/migration/concurrency tests; they are outside this task's
in-memory-only authorization. No hosted or PostgreSQL execution is claimed.

The delivery records exact commands, outputs, exits, SQL counts, source hashes
and preservation checks. Static preview fixtures use actual templates/CSS/JS
with synthetic data and disabled transports/submissions. They do not prove
physical-iPhone Safari layout, picker operation, empty-field height, focus or
scrolling. Those browser acceptance checks remain outstanding.
