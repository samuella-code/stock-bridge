# Task #6: customer UI audit

Base: `feat/plus-multi-business`, `fdb9d70`. Review performed before implementation.
The #5 hosted concurrency and mobile checks are still blocked; this redesign
does not imply those checks passed.

## Pages inspected

Dashboard and its flags-off preview; Products, Add/Edit, Details/stock history,
Quick Add, Import and Import Review; Sales and shared multi-item basket;
Restocking, receipts and insights; Expenses; Reports; My Businesses and
Add/Edit Business; Profile; subscription billing and flags-off Lifetime page;
Lifetime checkout, pending, launch and success pages. Authentication shares
the base but is outside the logged-in redesign; homepage and admin use their
existing presentation.

## Findings and reuse

- Shared `base.html` owns navigation, status banner, flashes and profile.
  Dashboard is active on Reports too. Profile and My Businesses are hidden
  in dropdowns. No standalone inventory backend exists: reuse Products as
  a stock-focused inventory view instead of adding a new workflow.
- Business selection is buried in the sidebar. During downgrade selection,
  the footer still displays the resolver's fallback business as if selected.
  Topbar must distinguish selection required from an active business.
- Eight equal dashboard cards obscure the four financial metrics. Existing
  metrics and period filters can be reused with different visual hierarchy.
- Products has two Add Product actions. Search/filter controls lack visible
  labels. Existing catalogue action links can become a single toolbar.
- Sales/restocks use a narrow entry column next to wide history tables.
  Entry/history headings, basket grouping and total can be clarified without
  changing request names, cost snapshots or transaction services.
- Expense history lacks the existing responsive-table class. Other tables
  without `thead` display their header row among mobile cards. Fix the shared
  table adaptation and consistent labelled cells, preserving pagination.
- Reports repeats large equal cards and lengthy explanations; retain the
  figures and explain estimation in a disclosure.
- Forms mix small labels, inconsistent gaps and buttons. Several POST forms
  depend on JavaScript for their CSRF field. Render CSRF fields directly as
  well as retaining backend protection. Keep errors visible; auto-fading
  feedback currently hides errors and the persistent access banner.
- My Businesses retains the correct secure actions; polish status, selected
  state and data-preservation explanations without changing selection rules.
- Billing has a malformed title block, duplicate page heading, unstyled
  current-plan box, hardcoded duplicated prices and stale "switcher coming"
  text. Reuse `PLANS`, verified subscription dates, access and ownership.
  Separate current access, comparison and management/history. A price-only
  toggle must never submit a form. Active subscriptions use the existing
  change endpoint and confirmation terms, not a duplicate checkout action.
- Profile has duplicate dropdown links for the same page. Keep one clear
  Profile & settings action and distinguish account and business fields.

## Implementation boundary

Reuse Flask/Jinja, existing CSS/JS and small shared template macros. Scope new
styles to authenticated customer pages; do not change homepage styling.
No new framework, API, schema, business calculations, entitlement logic,
provider calls, trial rules or security bypass. Test with isolated SQLite,
mocked email/provider and disabled real checkout. Do not deploy automatically.
