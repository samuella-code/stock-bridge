# Selected-business Business Insights

This first patch adds `/insights` for current paid Plus and Pro access only.
Pro combined reporting, Staff Access and Smart Restock are outside this patch.
No migrations, provider calls or new dependencies are required.

## Authorization and privacy

The existing authoritative `EntitlementSnapshot` evaluates the owner and each
candidate business at one captured UTC instant. The subscription winner must
be Plus or Pro and the business must pass the shared ownership, suspension,
slot and selection rules. A stale or foreign session selection falls back only
to a permitted owned business: valid session selection first, then a permitted
primary business, then the first permitted business by ID. The route resolves
this once and passes the same request-local context to the shared header,
switcher and notification count. Rendering does not call a second business
resolver or repair the session. Denied pages expose no business choices.
The shared billing label accepts the already evaluated access result; existing
callers keep their original behavior. No business ID supplied in a URL grants access.
There is no entitlement cache across requests. Trial, Basic and legacy-only
accounts retain their existing Reports behavior; paid legacy overlays qualify
only while the winning Plus/Pro paid period remains current.

All Insights responses, including access errors and redirects, are private,
no-store. Insights GETs do not start a trial. The existing subscription flag
is respected; no environment or deployment settings are changed by this patch.

## Recorded calculations

`app/main/reporting.py` shares the financial formulas with the existing
Dashboard/Reports `figures()` function, preserving its return keys and values:

* Revenue: sum(quantity × saved sale-item unit price).
* COGS: sum(quantity × saved sale-item unit cost).
* Estimated gross profit: revenue − COGS.
* Recorded operating expenses: sum(non-voided expense amounts).
* Estimated result after recorded expenses: gross profit − operating expenses.

Voided sales/expenses are excluded. Expenses are aggregated separately to
avoid multiplication by sale-item joins. Current product costs never replace
saved historical costs. Stock purchases remain separate, without a second
deduction. These are recorded figures, not complete accounts or forecasts.

Explicit dates take precedence over presets, using existing Reports validation
and native date controls. Inclusive calendar dates become half-open UTC
`[start, midnight after last day)` ranges. The preceding comparison has the
same number of calendar days, ending exactly at `start`. Insights rejects
dates too early to represent that preceding range; existing Reports does not
gain this restriction. Existing maximum range validation is unchanged (a
maximum difference of 366 days, including both boundary dates).

If the selected range includes today or future days, totals are labelled
incomplete and directional trends are withheld. Percent changes use only a
positive preceding value. Zero baselines receive no percentage, and negative
baselines show amount changes only. No recorded activity is not evidence of
no real activity. Unrecorded operating expenses cannot be detected.

Zero saved costs may be intentional or incomplete. The arithmetic is retained,
but either period containing such lines produces a warning and withholds
profit direction. Explanations are fixed deterministic rules, not causal
claims, AI output or demand forecasts.

## Separate stock review

Independently of selected financial dates, list at most 10 active products
with positive current stock, created on/before the beginning of the trailing
30 complete UTC days, with no non-voided sale in that window. End at today's
UTC midnight, excluding today's sales. Sort by stock descending then product
ID for stable ordering. New products have insufficient history for this list.

This deliberately uses complete calendar days. Existing Reports keeps its
historical rolling-30-day list and wording unchanged, as do restocking tools.
The Insights page labels both windows explicitly; they are not interchangeable.

## Verification and outstanding acceptance

Use a clean external runner with dotenv discovery disabled, all network,
provider, mail, storage, PostgreSQL and subprocess operations blocked, and
only `sqlite:///:memory:` allowed. Do not run migration or file-backed SQLite
fixtures under this authorization. The review delivery includes the exact
runner, commands and captured results.

Regression coverage includes historical cost arithmetic, separate expenses,
UTC boundaries, voids, periods, empty data, zero costs/baselines, entitlement
states, tenant/slot isolation, expiry, SELECT-only GETs and bounded activity
query counts. The new page uses existing mobile navigation, Help placement,
native date appearance and preset synchronization; its extra CSS is scoped.

Physical iPhone Safari containment, empty date-control height and picker
operation remain manual acceptance items. Static previews are inert synthetic
fixtures, not evidence of browser layout or live entitlement enforcement.
