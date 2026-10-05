# Plus multi-business support

Basic, trial and Legacy Lifetime support one business. Plus supports two.
The existing effective entitlement service decides limits; Paystack billing,
Lifetime records and subscription flags are not changed by this feature.

## Context and ownership

The signed Flask session stores `active_business_id`. Every resolution checks
ownership, suspension and the current entitlement. Invalid session selections
fall back to an allowed owned business. Products, sales, restocks, stock
movements, expenses, dashboard figures, reports and catalogue lookup/import
continue to use their existing business-scoped queries through this resolver.
Switching does not move any records. Forms use Flask-WTF CSRF protection.

## Downgrades

When an account with two businesses has a one-business entitlement, all
business screens redirect to My Businesses until the owner explicitly chooses
one. The existing `AccountBilling.primary_business_id` stores that choice.
A unique internal BillingEvent identifies the choice for the most recent paid
Plus subscription generation. A later Plus subscription makes both businesses
available again. After that Plus entitlement ends, a new choice is required.
Locked businesses retain all records. No business deletion route is added.
Accounts with no active entitlement retain existing read-only behavior for
their selected business and cannot create another business.

## Creation safety

A database write lock on the owner row is obtained before checking the business
count and effective entitlement. Concurrent requests for the same owner serialize
on PostgreSQL; SQLite serializes writers. Business creation, primary selection
when needed and an internal audit event commit together. A signed, user-bound,
one-hour form token makes repeated submission idempotent through the event's
existing unique key. Plans and limits are never read from submitted fields.

No schema migration is required. Existing account and event fields are reused.
Internal business events are claimed to prevent the billing email worker from
sending them and excluded from the customer billing notification list.

## Rollout

Feature branch: `feat/plus-multi-business`. Do not merge or deploy to production
until reviewed. With subscription flags off, the existing first-business resolver
and Lifetime access remain in place; additional business creation returns 404
and the new switcher is hidden. No production or staging deployment, database
migration, Paystack request, payment, or hosted database mutation is part of
local implementation/testing.

## Verification limits

Automated acceptance uses isolated SQLite databases with no real Paystack calls
or SMTP. The concurrency test uses two independent sessions/threads against a
file-backed disposable SQLite database. PostgreSQL relies on its owner-row write
lock; a hosted PostgreSQL concurrency run is still a staging follow-up.
Responsive CSS includes a single-column business page and 44px tap targets.
Actual mobile browser verification and hosted staging acceptance must be reported
separately; automated Flask response tests do not establish either.

## Implementation report

- Branch: `feat/plus-multi-business` (local; not merged or deployed).
- Schema/database changes: none. Migration: none.
- Active context: signed Flask session, revalidated on every resolution.
- Primary selection: existing AccountBilling field plus generation-specific audit event.
- Ownership: owned business queries and the central resolver; foreign IDs return safe 404.
- Limits: central entitlement, serialized owner-row lock, signed idempotency token.
- Basic/trial/Lifetime: one business. Plus/Lifetime + Plus: two businesses.
- Downgrade: explicit selection, one usable business, other locked with data intact.
- Re-upgrade: both existing businesses available; no recreation or copying.
- Products/inventory/sales/restocking/expenses/dashboard/reports: automated isolation passed.
- IDOR/security: forged IDs, foreign selection, stale sessions, CSRF and cross-business import passed.
- Existing-customer regression: one-business customer works without selection; flags-off behavior retained.
- End-to-end: Mini Mart Coke stock 90; Fashion T-shirt stock 28. Mini Mart revenue
  5,000, COGS 3,000, expenses 2,000, net 0. Fashion revenue 14,000, COGS 8,000,
  expenses 3,000, net 3,000. Switching preserves both. Restocking Fashion does not
  affect Mini Mart. Today/week/month/custom reports remain separately scoped.
- Mobile: responsive layout implemented; actual mobile/browser verification pending.
- Hosted staging: this new multi-business implementation has not been deployed or tested there.
- Follow-up: deploy only to isolated staging after review, verify desktop/mobile navigation,
  switcher, creation and downgrade choice; run hosted PostgreSQL concurrency acceptance.
- Existing suspension policy is preserved: an admin suspension may block the customer
  account as before. Entitlement locks do not set suspension or delete records.
- Cancellation, renewal, subscription pricing, Paystack Live and admin authentication unchanged.

### Files changed

- `app/__init__.py`
- `app/businesses/__init__.py`
- `app/businesses/service.py`
- `app/businesses/routes.py`
- `app/subscriptions/entitlements.py`
- `app/subscriptions/routes.py` (hide internal business audit events from billing notices)
- `app/profile/routes.py`
- `app/templates/base.html`
- `app/templates/businesses/index.html`
- `app/templates/businesses/form.html`
- `app/templates/products/index.html`
- `app/static/css/businesses.css`
- `tests/test_billing.py` (require explicit lower-tier choice)
- `tests/test_multi_business.py`
- `docs/PLUS_MULTI_BUSINESS.md`

PRODUCTION MODIFIED: NO

PRODUCTION DATABASE MODIFIED: NO

PRODUCTION SUBSCRIPTION FLAGS CHANGED: NO

LIVE PAYSTACK MODIFIED: NO

### Automated test result

Full suite: **285 passed**, 18,722 warnings, 97.05 seconds.
Command: `python -m pytest -q --disable-warnings` using the existing isolated test
virtual environment. The new multi-business module contains 23 test cases after
parametrization, including concurrency and rollback. Existing billing fallback
expectations now require explicit business selection. Warnings are retained in
the test result; they did not fail the suite.

No push was performed: the existing Vercel Preview deployments share the
production database and must not be used for this feature's acceptance testing.
