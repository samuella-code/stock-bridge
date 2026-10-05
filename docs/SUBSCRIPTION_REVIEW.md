# StockBridge subscription implementation review

Local implementation on `feat/subscription-entitlements`, based on main `712580e` (merged PR #26). No push, merge, deployment, production database access, production migration, live Paystack configuration, plan creation, charge or customer reset was performed. Both rollout flags default to false.

## Resulting customer behavior

- Accounts created while subscription rollout is enabled receive one persistent account-level trial eligibility record. The seven-day UTC trial starts only after email verification, once. No card is collected and trial expiry does not trigger a charge. Existing unpaid accounts are not silently granted Lifetime Access or another trial.
- Basic: ₦3,000 monthly / ₦30,000 yearly; one selected business. Plus: ₦5,000 monthly / ₦50,000 yearly; entitlement architecture supports two businesses. The full business switcher remains a separate task; the page explicitly says so. No Business plan is offered.
- Expiry keeps products, inventory, sales, expenses, restocks, dashboard and reports readable. Every POST in the Products/Sales/Expenses/Restocking blueprints is blocked server-side without an effective write entitlement, including imports, voids, adjustments and settings. Account/profile/billing management remains available.
- Current one-business screens use the persisted, ownership-checked primary business. Additional business records are retained after Plus ends.
- Lifetime Access is an independent base entitlement. Voluntary Plus purchase, renewal, cancellation, expiry and failed renewal never overwrite its evidence or force a Basic purchase. Dual-role administrators' existing legitimate lifetime business entitlement and admin security fields remain preserved.

## Migration and lifetime evidence

`migrations/versions/0013_account_billing.py` adds AccountBilling, RecurringSubscription and BillingEvent, plus nullable account/subscription/plan/interval columns on Payment. Existing business trial fields and all old migrations remain intact. No records are deleted; no provider calls occur during migration.

Lifetime evidence requires a linked Paystack lifetime payment: success, paid timestamp, NGN, 300000 kobo, matching owner email ignoring case. The exact migration-0004 `legacy-ID@stockbridge.local` placeholder qualifies only for an already active lifetime business. Account age or a success string without sufficient evidence is not enough. Multiple qualifying businesses per owner or active lifetime flags without evidence stop the migration before billing DDL. Historical payment mode was not stored by the old schema; preflight is based on available records, so operators must review legitimacy before rollout.

Read-only preflight, with an explicitly selected authorized database environment (never paste its URL into logs):

```bash
python -m flask --app app:create_app billing-preflight
```

It prints counts only and ends `READ ONLY: no records changed.` A warning is a stop condition, not permission to stamp or bypass a migration. A destructive downgrade is deliberately disabled to protect billing history. SQLite migration tests passed; PostgreSQL/Neon migration and locking must still be verified against an isolated restored staging database before any production approval.

## Paystack implementation

Uses the existing HTTP adapter and signature-checked webhook. Initial checkout fetches the configured plan and validates provider mode, code, integer amount, NGN and interval before initialization. Server-owned plan/price/account/reference metadata prevents client price manipulation. Only verified successful transactions with matching account, provider customer, ownership, metadata and amount grant paid access. A subscription-created event alone does not grant access.

Renewals require a paid/success invoice with a transaction reference, verified transaction and authoritative period dates. Initial access uses verified paid_at and provider next_payment_date; it never adds guessed 30/365-day periods. Implausible or missing dates stop reconciliation. Unique payment references/event receipts and account serialization prevent duplicate grants/notifications; repeated verified references cannot extend another invoice period. Out-of-order failure events cannot overwrite a newer paid period. Unpaid failed renewal adds no grace period and never extends access.

Basic → Plus with the same billing interval stops old renewal, then opens a new full-price checkout. Paid upgrade access starts only after verification. Other tier/interval changes stop old renewal, then create a replacement with start_date at the old paid period's end, using the verified reusable authorization. This uses documented provider primitives; it is an application-level replacement strategy, not a Paystack prorating API. No credit/custom proration is promised. One replacement per source subscription is enforced; other pending/scheduled billing operations block a new checkout. Cancellation retains paid access through its stored end. A scheduled replacement can also be cancelled before its first paid period.

Pending attempts are committed before external creation. An unknown network outcome stays pending and blocks blind retries. Such an account needs operator reconciliation against Paystack; there is deliberately no automatic “discard pending and charge again” button. Missing subscription association waits for the signed subscription event. HTTP 503 signals provider retry/reconciliation without granting access. Existing delayed lifetime callbacks/webhooks remain supported even when recurring-provider billing is disabled.

The HTTP helper accepts a successful response without `data` only for subscription disable; payment verification still requires structured data. Card update links are generated after ownership/provider checks and must point to Paystack's HTTPS subscription-management path. Card details, reusable authorization codes, email tokens and management links are not persisted in the billing ledger.

Official documentation investigated: [subscriptions](https://paystack.com/docs/payments/subscriptions/), [subscription API](https://paystack.com/docs/api/subscription/), [plan API](https://paystack.com/docs/api/plan/), [transactions](https://paystack.com/docs/api/transaction/), [webhooks](https://paystack.com/docs/payments/webhooks/).

## Configuration for a later approved rollout

Create four separate plans manually in the correct Paystack mode, currency NGN, invoice limit unlimited. Do not edit existing shared plans or use update_existing_subscriptions to change individual customers.

| Environment mapping | Price in kobo | Paystack interval |
| --- | ---: | --- |
| PAYSTACK_BASIC_MONTHLY_PLAN | 300000 | monthly |
| PAYSTACK_BASIC_YEARLY_PLAN | 3000000 | annually |
| PAYSTACK_PLUS_MONTHLY_PLAN | 500000 | monthly |
| PAYSTACK_PLUS_YEARLY_PLAN | 5000000 | annually |

`SUBSCRIPTIONS_ENABLED=false` and `BILLING_PROVIDER_ENABLED=false` remain the defaults. Existing matched `PAYSTACK_PUBLIC_KEY` / `PAYSTACK_SECRET_KEY` are reused; no keys were changed. Map the four `PLN_…` identifiers only after mode/price validation. Preview provider calls remain disabled. Test-mode acceptance must use an isolated local/staging environment and test keys; never reuse the production database for preview testing.

Production continues using its existing precedence: NEON_DATABASE_URL, then NEON_POSTGRES_URL, then DATABASE_URL. No new database was created. The existing production Vercel build script upgrades to migration head: do not merge/deploy this branch before the backup, preflight and migration review are approved.

## Notifications and admin scope

Branded billing templates reuse the existing email base. Trial/payment/renewal failure/plan change/cancellation/period-end notices are recorded once. The explicit worker queues due notices and delivers claimed messages:

```bash
python -m flask --app app:create_app billing-notifications
```

It does not run during deployment/startup, and no production schedule was installed. An approved scheduler is needed later. Claims precede SMTP; an ambiguous send failure is retained for operator review rather than automatically sending duplicate receipts. Verification templates/admin authentication were not redesigned.

Admin customer/business lists and details show trial, plan/interval, pending, past due, cancellation and expiry labels. Subscription payment references are displayed without broken lifetime-only detail links. Existing admin financial dashboard/payment tabs remain explicitly lifetime-scoped; broader recurring revenue analytics are not implemented in this task. Admin authentication/security and customer-reset tooling were not changed. Do not use the historical reset tool on accounts with new billing-ledger records without a separately reviewed extension; its old deletion plan does not include these records.

## Validation and remaining release checks

Final full regression result: **234 passed**, 17266 datetime deprecation warnings, in 75.50 seconds. The dedicated billing acceptance suite contains 67 cases. `git diff --check` also passed. Billing acceptance covers all four plans; verified-only activation; seven-day trial/nonrestart/expiry; all registered business POSTs; complete inventory/sale/restock/expense flow; business isolation; lifetime monthly/yearly fallback; verified renewal/duplicates/out-of-order failure; cancellation/replacement/scheduled changes/resubscription; CSRF/signature/price/ownership/mode checks; durable ambiguous failures; concurrent checkout; migration preflight/preservation; branded notification claims.

Historical migration tests explicitly stop at their original migration so they still test the original upgrade contract. New tests separately exercise migration head and the required stop for incomplete lifetime evidence. Existing datetime deprecation warnings are present; unrelated timestamp migration was not included.

Browser verification was attempted twice using the Vercel agent-browser skills. Its daemon exited during startup without diagnostics. The disposable Flask preview server started successfully and was stopped. Server-side rendering tests passed, but no visual/mobile-browser pass is claimed.

Before release:

1. Review this branch and the original investigation in SUBSCRIPTION_DESIGN.md.
2. Verify a recoverable production restore point; test restoration to an isolated PostgreSQL staging database. No production backup was assumed or verified in this task.
3. Run read-only lifetime preflight on the approved target, resolve ambiguities explicitly, then validate migration/data preservation and concurrent operations on staging.
4. Manually configure the four test-mode Paystack plans; verify actual checkout, webhook payloads, initial/renewal dates, cancellation, scheduled first debit, replacement and card-update behavior. Mocks are not proof of live-provider behavior.
5. Complete mobile and desktop browser verification, including menu/access states and billing forms.
6. Review operator procedures for ambiguous pending attempts and claimed-but-unsent emails. Arrange the approved notification schedule.
7. Obtain explicit approval for merge, deployment, production migration and live plan/flag configuration. None are authorized by this report.

Full multi-business UX, Business plan, Direct Debit onboarding, custom proration, automatic retry/grace, provider reconciliation automation and recurring admin revenue analytics remain outside this implementation.

## Files changed

Models/config/migration: app/models.py, config.py, migrations/versions/0013_account_billing.py.

Billing: app/subscriptions/{entitlements,legacy,provider,billing,notifications,routes}.py; app/payments/{routes,service}.py.

Access/onboarding: app/__init__.py, app/auth/routes.py, app/email_service.py; selected-business compatibility in app/{main,products,sales,expenses,restocking,profile}/routes.py.

UI: app/templates/subscriptions/billing.html, app/templates/email/billing.html, app/templates/base.html, app/templates/home.html, app/templates/auth/signup.html, app/static/css/billing.css; admin user/business list/detail access labels.

Tests: tests/test_billing.py; historical migration boundaries in tests/test_admin_portal.py, tests/test_business_workflows.py, tests/test_catalogue.py. Documentation: this report and docs/SUBSCRIPTION_DESIGN.md.
