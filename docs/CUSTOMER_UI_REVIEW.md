# Task #6 local review

Implementation is on `feat/customer-ui-redesign`, based directly on the existing
`feat/plus-multi-business` commit `fdb9d70`. No remote push, merge or deployment
was performed. This is a local review candidate, not production acceptance.

| Requested report item | Result |
|---|---|
| 1. Branch | `feat/customer-ui-redesign`; #5 ancestor verified |
| 2. Design approach | Small authenticated customer design system, white surfaces, green actions, readable type, consistent spacing. Flask/Jinja retained. |
| 3. Navigation | Dashboard, Business tools, Reports and Account groups; no unfinished features or new Help backend. |
| 4. Desktop sidebar | 236px sidebar, 212px on smaller desktops; selected links expose `aria-current`. |
| 5. Mobile navigation | Existing accessible drawer/focus trap retained; responsive drawer sizing and 44px controls. Actual device/browser acceptance pending. |
| 6. Active business | Prominent Working in indicator and existing secure switch forms in topbar; explicit choice messaging during downgrade. |
| 7. Dashboard | Four financial cards plus secondary operating metrics; existing periods, calculations and quick actions retained. |
| 8. Products | Single product-entry action group; labelled filters; consistent catalogue, stock badges and pagination. |
| 9. Inventory | Stock-focused view of the existing catalogue, with priority sort and preserved view query in pagination. Existing product history/adjustments retained. |
| 10. Sales | Record Sale heading, shared basket styling and clearer history/empty state. Existing multi-product calculations retained. |
| 11. Restocking | Restock Products heading, receipt/insights table styling, existing basket and cost snapshots retained. |
| 12. Expenses | Consistent form and responsive history; void reason labelled. Categories and totals unchanged. |
| 13. Reports | Business name, existing period metrics and estimation explanation in a disclosure. Reports remain per business. |
| 14. My Businesses | Selected card styling, clear availability/selection state, locked-data explanation and existing switch/edit/add controls. |
| 15. Billing & Plan | Current access/price/status, comparison cards, existing secure management and history. Removed stale switcher-coming copy and malformed title. |
| 16. Toggle | Monthly/yearly buttons change display and prospective checkout interval only; no submission or provider request. Select remains available without JS. |
| 17. Basic | 1 business, implemented inventory/sales/restocking/expense/dashboard/report tools. Prices derived from `PLANS`. |
| 18. Plus | Up to 2 businesses and separate records/reports; existing subscription changes go through management, not duplicate checkout. |
| 19. Trial | Remaining days/end date and explicit no automatic charge; existing trial rules retained. |
| 20. Lifetime | Explicit Legacy Lifetime Access; no Basic requirement; optional Plus with underlying entitlement preservation. Flags-off Lifetime route retained. |
| 21. Forms | Consistent input/label/button spacing, server-rendered CSRF fields, persistent feedback, repeat-submit busy state. Named submitters stay enabled. |
| 22. Tables | Labelled mobile card adaptation, hidden mobile header rows, consistent spacing and existing pagination. |
| 23. Empty states | Products/onboarding retained; sales and expense guidance clarified; shared styling. |
| 24. Accessibility | Skip link, current-page state, focus outlines, 44px controls, Escape/outside dismissal, visible status text. Existing drawer trap preserved. |
| 25. Responsive improvements | Breakpoints for desktop, tablet and narrow phones; single-column forms/cards, compact metrics and business dropdown. These are implementation changes, not proof of mobile PASS. |
| 26. Files changed | See file inventory below and `git diff` for exact changes. |
| 27. Schema/database | No schema/model/migration changes. Test-only disposable SQLite data used. |
| 28. Automated tests | See final validation below. |
| 29. Browser verification | BLOCKED: controlled browser navigation to local review returned `net::ERR_BLOCKED_BY_CLIENT`. |
| 30. Mobile verification | NOT VERIFIED at 375/390/430/768/1024px or desktop. No layout PASS inferred from response/unit tests. |
| 31. Limitations | Actual browser layout, dropdown interactions, touch targets and full workflow visual acceptance require review in a browser that can reach the local app. Existing field validation remains backend-driven; this task does not introduce a new validation service. |
| 32. Bugs fixed | Reports previously highlighted Dashboard; duplicate product entry action; hidden business switcher; misleading fallback business during selection; stale billing text/title; duplicated price constants; mobile header rows/expense table; disappearing errors; duplicate profile links. |

## File inventory

- `app/templates/base.html`, new `components/ui.html`
- `app/static/css/customer.css`, `app/static/js/customer.js`, `app/static/js/app.js`
- `app/templates/dashboard.html`, `reports.html`
- `app/templates/businesses/index.html`, `profile/index.html`
- `app/templates/products/index.html`, `basket.html`, `detail.html`, `form.html`,
  `quick_add.html`, `import.html`, `import_preview.html`
- `app/templates/sales/index.html`, `restocking/index.html`, `expenses/index.html`
- `app/templates/subscriptions/billing.html`, `app/subscriptions/routes.py`
  (presentation context only)
- `tests/test_customer_ui.py`, `tests/customer_ui.cjs`
- `docs/CUSTOMER_UI_AUDIT.md`, this review report

## Pre-production checklist

- [ ] Outstanding #5 hosted PostgreSQL Basic/Plus concurrency, rollback,
  duplicate/orphan/partial-write acceptance on isolated staging only.
- [ ] Outstanding #5 actual mobile verification at 375px, 390px and 430px,
  including switching, explicit downgrade selection and locked messaging.
- [ ] #6 actual browser review of all customer pages and billing states,
  at 375/390/430/768/1024px and desktop; check overflow, text/control clipping,
  drawer/dropdown keyboard interaction, forms, tables and touch targets.
- [ ] User local review before any isolated staging deployment.
- [ ] Any staging deployment must use the isolated staging database and
  staging-safe billing only. No Preview deployment backed by production DB.

## Safety

HOMEPAGE REDESIGNED: NO  
AI ASSISTANT ADDED: NO  
BUSINESS LOGIC CHANGED: NO  
DATABASE SCHEMA CHANGED: NO  
PRODUCTION MODIFIED: NO  
PRODUCTION DATABASE MODIFIED: NO  
PRODUCTION SUBSCRIPTION FLAGS CHANGED: NO  
LIVE PAYSTACK MODIFIED: NO

## Final validation

Full automated suite: **PASS — 303 tests** (285 existing + 18 new),
19,093 existing deprecation warnings, 68.73 seconds. JavaScript presentation checks passed
(11 assertions): price-only toggle, cancelled-submit recovery, repeated-click
protection and preservation of named submitters. `node --check` and
`git diff --check` passed. Existing jsdom drawer harness could not run because
this workspace lacks its test-only `jsdom` dependency; this does not establish
browser/mobile acceptance.
