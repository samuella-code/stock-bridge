# Task #6.1 — local review

1. **Branch:** `feat/onboarding-guidance`.
2. **Base:** current reviewed `feat/customer-ui-redesign`, commit
   `9895079aab85c5a874bffdfc72b1a0e331518bc3`. Task #5 and #6 are preserved.
3. **New commit:** the commit containing this report; use `git log -1` on the branch.
4. **Approach:** server-rendered Flask guidance, one shared native expandable
   help pattern and a small browser preference script. No tour library/framework.
5. **First-login experience:** an inline, optional welcome on an empty writable
   business dashboard. Get Started opens Add Product; Explore StockBridge dismisses
   it and focuses the setup summary. Both remember dismissal for this browser.
   Signup, email verification, first-business creation and login are unchanged.
6. **Dashboard checklist:** three meaningful setup actions plus Explore your
   reports as an untracked link. Quick actions remain prominent. No manual ticks.
7. **Completion:** existence of a business-owned product (including archived),
   a non-voided sale with positive-quantity items, and a non-voided expense,
   across all dates. One read-only query, scoped to the selected business.
8. **Existing businesses:** any existing product/activity suppresses the welcome.
   Partial setup is collapsed once records exist; all three complete hides it.
   Setup does not reset with reporting filters.
9. **Multi-business:** all completion queries use the active business ID. The
   browser dismissal key includes account and business IDs. Switching business
   recomputes guidance; another business/account cannot complete its checklist.
10. **Products:** first-use explanation, opening-stock guidance and Add Product,
    Quick Add, Import actions. Reuses existing entry routes.
11. **Inventory:** explains automatic changes from sales, restocks and reasoned
    stock adjustments. Empty state directs to opening stock; no quantity/formula
    changes or barcode scanning.
12. **Sales:** explains search/add, quantities/prices, multiple basket rows,
    stock reduction, overselling prevention and historical price/cost preservation.
    Existing form/basket flow is unchanged. Empty history offers Record Sale;
    without products it first offers Add Product.
13. **Restocking:** simple stock-up/stock-down distinction, received quantities,
    purchase cost and receipt history. Empty history offers Restock Products or
    Add Product as appropriate. Removes empty-history pagination clutter.
14. **Expenses:** running-cost guidance; resale purchases belong in Restocking.
    Empty state includes Add Expense, pointing to the existing form.
15. **Reports:** explains revenue and saved historical COGS, gross profit,
    net profit, separate inventory purchases and current inventory valuation.
    Reuses existing formulas and explanations. No persisted viewed-report claim.
16. **Contextual help:** shared `help_panel` macro renders closed native details
    for Products, Inventory, Sales, Restocking, Expenses and Reports. User-triggered,
    keyboard-operable, no auto-open help modal or focus trap.
17. **Empty states:** clear title, practical explanation and relevant existing
    action; lightweight HTML, no new illustration assets.
18. **Search empty states:** preserves #6 Products/Inventory Clear filters. Fixes
    restocking insight searches that previously suggested adding products when
    products already exist. No first-product message after a zero-match search.
19. **Trial:** subtle status area uses existing effective trial days and the real
    AccountBilling end date in UTC. States seven days, no card required/no automatic
    charge, and View Plans. No browser-generated dates or trial restart.
20. **Legacy Lifetime:** existing protected label/entitlement remains. No trial
    expiry message; empty businesses may receive ordinary product-use guidance.
21. **Basic:** same business-use guidance; no trial pressure for paid accounts.
22. **Plus:** same guidance per business; switcher, limits and ownership unchanged.
23. **Responsive:** flexible wrapped actions/status area, mobile margins, no
    onboarding overlays/fixed heights. Prepared for existing #6 breakpoints.
    Actual 375/390/430/768px and desktop visual checks are BLOCKED, not passed.
24. **Accessibility:** descriptive native summary controls, text Completed/Not yet
    completed (not color alone), labelled Explore dismissal, keyboard focus after
    dismissal and `aria-describedby` helpers on product fields. Existing focus
    styles and alerts retained. Browser behavior was not visually verified.
25. **Database/schema:** no migrations, tables, columns or persisted report-view
    tracking. Preference storage contains only a dismissal flag under IDs.
26. **Business logic:** no entitlement, stock, sales, expense, profit, ownership,
    validation or transaction changes. Backend additions only read existing data
    for presentation and expose the real trial end date to templates.
27. **Files:** see the list below; no homepage/admin/payment changes.
28. **Full automated suite:** **PASS — 333 tests (303 baseline + 30 new); 19,939 existing-style deprecation warnings, 84.04 seconds.**
29. **JavaScript:** existing 11 presentation assertions pass; 12 new welcome
    preference assertions pass (23 total). These are unit checks, not browser tests.
30. **Browser visual:** BLOCKED. Disposable local SQLite app ran on port 5057;
    cloud browser refused `http://127.0.0.1:5057` with `ERR_BLOCKED_BY_CLIENT`.
    No empty/partial/established local layout was visually verified.
31. **Mobile visual:** BLOCKED at 375, 390, 430 and 768px. Local site access is
    blocked and there is no supported viewport-resize control in this browser.
32. **Bugs fixed:** misleading restocking no-result guidance; empty restock history
    pagination; dashboard's active-product-only onboarding could show beginner
    guidance to businesses with archived products. #6 search/table fixes retained.
33. **Limitations:** welcome dismissal is browser-local; clearing/denying storage
    can cause it to reappear while a business remains empty. Get Started/new records
    suppress later welcome through real data. Reports exploration is untracked.
    No deployment, remote push or merge performed; wait for review.

## Changed files

- `app/__init__.py`
- `app/main/routes.py`
- `app/main/onboarding.py`
- `app/static/css/customer.css`
- `app/static/js/onboarding.js`
- `app/templates/base.html`
- `app/templates/components/ui.html`
- `app/templates/components/setup.html`
- `app/templates/dashboard.html`
- `app/templates/products/index.html`
- `app/templates/products/form.html`
- `app/templates/sales/index.html`
- `app/templates/restocking/index.html`
- `app/templates/expenses/index.html`
- `app/templates/reports.html`
- `tests/test_onboarding.py`
- `tests/onboarding.cjs`
- `docs/ONBOARDING_AUDIT.md`
- `docs/ONBOARDING_REVIEW.md`

## Pre-production checklist — still outstanding

- [ ] #5 hosted PostgreSQL Basic/Plus concurrent business creation, limits,
      duplicate/orphan/partial writes and rollback verification on isolated staging.
- [ ] #5/#6 real mobile verification at 375, 390, 430px; #6 tablet at 768px.
- [ ] #6 trial and Legacy Lifetime hosted fixture views.
- [ ] #6.1 local/authorized staging visual review of empty, partial and established
      businesses, welcome dismissal, help keyboard usage and trial presentation.

## Safety results

| Check | Result |
| --- | --- |
| HOMEPAGE REDESIGNED | NO |
| AI ASSISTANT ADDED | NO |
| BUMPA CODE/ASSETS COPIED | NO |
| BUSINESS LOGIC CHANGED | NO |
| DATABASE SCHEMA CHANGED | NO |
| PAYSTACK LOGIC CHANGED | NO |
| TRIAL RULES CHANGED | NO |
| LIFETIME RULES CHANGED | NO |
| PRODUCTION MODIFIED | NO |
| PRODUCTION DATABASE MODIFIED | NO |
| PRODUCTION SUBSCRIPTION FLAGS CHANGED | NO |
| LIVE PAYSTACK MODIFIED | NO |
| STAGING DEPLOYED FOR #6.1 | NO |
