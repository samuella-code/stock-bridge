# Task #6.1 audit — before implementation

Base: `feat/customer-ui-redesign`, `9895079aab85c5a874bffdfc72b1a0e331518bc3`.

- Signup already creates the first business and an eligible AccountBilling record.
  Email verification starts the existing seven-day account trial. First login
  redirects to the dashboard after verification; these routes need no changes.
- Plus business creation already opens its empty dashboard. Existing ownership,
  selection, locked-business and write guards resolve the active business first.
- Dashboard already has prominent quick actions and a product-count-only setup
  paragraph. It lacks business-specific progress or dismissible welcome guidance.
- Products and Inventory share a protected catalogue. Product creation already
  excludes current-stock editing and explains opening stock, but individual cost,
  price and threshold fields lack helper text. Quick Add and Import already exist.
- Sales and Restocking already support product search, multiple basket rows,
  quantities, price/cost review, totals and existing validation. Preserve them.
- Sales and Expenses have plain history empty messages; Restocking has an empty
  table row and misleading no-products copy when insight searches have no matches.
- Reports already explain historical COGS, gross/net profit and separate purchase
  accounting in an expandable panel. Reuse that explanation and native details.
- Products' filtered empty state was fixed in #6. Preserve Clear filters.
- Billing already renders Basic/Plus comparisons, real trial days, renewal state
  and Legacy Lifetime access. The base trial banner duplicates some information
  and can be quieter, with the real account trial end date and View Plans.
- AccountBilling is the subscription-era trial authority. Business has older
  legacy trial fields, including a 14-day default; do not use these for new trial
  presentation or change either entitlement system.
- No existing persisted welcome/report-view preference was found. Use only
  browser-local welcome dismissal, keyed by account and business; no sensitive
  data or authorization state. Reports remains an exploration action, without
  a persisted completion claim. State resets if browser storage is cleared.

No changes to billing, inventory formulas, signup, verification, access guards,
database schema or transaction behavior are required. Actual responsive browser
and hosted PostgreSQL concurrency checks from #5 remain on the pre-production
checklist; HTML/CSS assertions cannot substitute for them.
