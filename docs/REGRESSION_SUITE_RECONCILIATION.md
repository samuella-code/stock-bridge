# Regression suite reconciliation

## Evidence and scope

Business details PR #67 initially ran the complete GitHub suite at d5d5a08:
286 failed, 3,053 passed, 66 skipped. Rerunning those exact failing cases on
unchanged staging base 85c6263 reproduced 285 failures; the remaining case was
the business-suspension fixture updated in d93e004. Its replay and duplicate-mail
assertions were retained with the newly required exact business name, allowed
reason and explicit email opt-in.

This follow-up starts at d93e004 on fix/regression-suite-review. No backend
business logic, authorization, feature flags, schema or migration files change.
No hosted database, provider payment or live email operation is needed.

## Causes and corrections

- Presentation tests still expected retired dropdowns, large daily-value lists,
  question-mark help panels and setup cards removed by the approved redesign.
  They now assert the current segmented links, custom-date form, accessible
  chart buttons, empty states and help navigation.
- Currency expectations still required redundant .00 suffixes. Fixed amounts,
  saved-cost arithmetic and void exclusions remain asserted; display expectations
  use the approved format. The dashboard workflow now checks displayed net
  profit, while its existing gross-profit calculation assertion is retained.
- Pricing tests expected the old card classes, duplicated features, price data
  attributes and Included/Not included wording. They now check both intervals,
  exact configured prices and limits, inherited feature copy, accessible status
  text and the Plus-only recommendation in cards and comparison headers.
- Synthetic dashboard/staff/report renders lacked the context or shared macros
  required by the new templates. Page-form fixtures isolate the shell; form
  comparisons retain targets, CSRF, names, values, selections and validation
  while allowing layout order and equivalent select/radio controls. Added
  negative checks prove changes to CSRF, permissions, required fields or targets
  are still detected. The approved Manager finance default remains explicit.
- The billing migration test expected an old head despite upgrading to head.
  It now compares the stored revision with Alembic's actual current head. The
  synthetic missing-revision fixture removes descendants as well as the missing
  revision, avoiding a broken graph unrelated to the intended lookup failure.
- One Help widget contact condition accessed request.endpoint without the guard
  already used elsewhere in that component. It now tolerates missing request
  context. Existing strict standalone tests cover that case. Normal request
  behavior and account/provider access remain unchanged.

No failing tests are skipped or disabled. Existing financial calculations,
tenant boundaries, authentication, read-only behavior, CSRF and transaction
replay assertions remain in the suite. Final disconnected full-suite result: **3,343 passed, 66 skipped, zero failures**
in 540.08 seconds (62,811 warnings), exit 0. The 66 skips are the existing
PostgreSQL opt-in tests; no new skips were added. PostgreSQL execution and hosted staging smoke
testing are not claimed by disconnected SQLite/template tests.
