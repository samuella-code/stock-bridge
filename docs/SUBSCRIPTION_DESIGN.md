# Subscription investigation and proposed implementation

Review stage only. No production/Paystack changes, migrations, merge or deployment are authorized. Rollout will be disabled by default.

## Existing architecture

- Payment stores business link, customer email, reference, provider/product, integer kobo amount/currency, status, paid date, claim token and email delivery claims. Lifetime checkout uses server-side /transaction/initialize and redirects to checkout.paystack.com.
- Callback verifies through /transaction/verify. _confirm checks reference, NGN amount, provider mode and server-owned metadata. _activate_account matches exactly one non-admin customer and one owned business, then sets lifetime/active/no expiry. Signed charge.success webhook shares activation; payment receipt is claimed once.
- Business has old trial and subscription fields. Signup explicitly sets inactive and zero-length trial. has_write_access recognizes active status. Global guard currently blocks entire paid business areas, including reads. Dashboard shows preview without access; reports redirect to plans.
- User→Business is one-to-many in the model; application routes use businesses[0]. There is no customer business-switching/create-second-business UI. That remains a separate task.
- /plans is a lifetime marketing page. No recurring subscription/customer identifiers or lifecycle worker exist. Migration history ends at 0012_product_catalogue; 0002 introduced historical trial fields, 0004 disabled trial access. These migrations must remain unchanged.
- Admin grants are Paystack-verification-backed, not a generic free grant. Admin security and customer-reset tooling stay outside this change.

## Lifetime identification and migration

Evidence: linked Payment(provider=paystack, product=lifetime, status=success, paid_at non-null, NGN, amount=300000), linked business owned by the account, email matches case-insensitively. Migration-0004 placeholder legacy-ID@stockbridge.local emails are separately recognized only for an already-active lifetime business. Account age or inactive/unpaid/initialized/failed records do not qualify. No provider call, recurring subscription or charge is made for these accounts.

Store immutable base lifetime evidence on AccountBilling (legacy payment, original business and grant date). Additional recurring records never overwrite it. Existing lifetime business flags and payments remain unchanged. Multiple qualifying businesses per owner or active lifetime flags without sufficient evidence are migration preflight ambiguities: stop, investigate and obtain an explicit resolution; never silently revoke or infer a grant. No production preflight/migration is run in this task.

## Proposed architecture

AccountBilling is one persistent row per user: trial eligibility/dates, immutable lifetime provenance, selected one-business ID. Existing Business fields remain for compatibility; they are not the source of new account trials. New verified non-admin accounts become trial-eligible on signup while rollout is enabled. Trial starts once at verification or first verified access. Existing unpaid accounts do not automatically receive lifetime or a restarted trial.

RecurringSubscription is separate history: user, plan/interval, integer price, provider plan/customer/subscription codes, state, paid period/start/end/next renewal, cancellation and replacement state. Payment gains nullable account/subscription links and interval/plan snapshots, retaining all existing rows. BillingEvent provides unique event receipts and branded notification delivery claims.

Central service resolves trial/basic/plus/legacy/legacy+plus/restricted and business limits (1/2). Selected primary business defines the future one-business fallback; extra businesses are retained. All paid mutations are blocked server-side when restricted; read-only business history/dashboard/reports and account/billing pages remain available. Trial and expiry use server UTC. No extra grace period: paid period remains usable; failed unpaid renewal never extends it.

## Official Paystack findings (checked 5 October 2026)

Sources:
- https://paystack.com/docs/payments/subscriptions/
- https://paystack.com/docs/api/subscription/
- https://paystack.com/docs/api/plan/
- https://paystack.com/docs/api/transaction/
- https://paystack.com/docs/payments/webhooks/

Plan checkout's plan code overrides its amount, so fetch and validate the configured provider plan before initialization. Providers use monthly/annually intervals; monthly subscriptions created after day 28 renew on day 28. Use authoritative invoice period dates and next_payment_date, never 30/365-day guesses. Subscriptions support card/direct-debit authorization; this MVP restricts initial subscription checkout to card until Direct Debit onboarding is separately verified.

subscription.create is not payment proof. charge.success is verified; invoice.update must be paid/success and reference a verified transaction before access extends. subscription.not_renew stops renewal; subscription.disable signals cancellation. invoice.payment_failed sets past_due, retaining only already-paid time. Docs say no immediate retry, although attention subscriptions can attempt again on a later payment date: do not promise a retry schedule.

Shared Plan updates can modify all subscribers and are not safe for individual tier changes. There is no documented per-customer prorating contract here; use replacement subscriptions without custom proration. Upgrade stops old renewal first, then explicitly authorized checkout for the new full-price plan. Existing paid time remains available if checkout fails; no credit/proration is promised. Downgrade/interval change disables old renewal and creates a replacement at the old paid period's end using the customer's verified reusable authorization. A replacement grants no access before verified payment. Persist operation attempts before external calls; ambiguous create responses require reconciliation, not blind repeat charging.

Cancel uses provider disable and retains the local paid entitlement until its stored paid period ends. Lifetime users then fall back permanently. Billing setup requires four manually created plans (Basic 3000/30000; Plus 5000/50000) and environment-mapped plan codes; never mutate shared provider plans in application code. Business plan and full multi-business UI are unavailable.

## Expected files and risks

Change models, new migration 0013, central subscription service/provider/routes/templates, payment integration branches, signup/verification trial hook, global access enforcement, minimal dashboard/report read-only integration, base trial/access label, conditional welcome copy, minimal admin access labels, new billing email template/outbox and lifecycle tests. Add detailed configuration/release report and migration preflight. Preserve reset tooling, existing migration history and business calculations.

Risks: missing legacy evidence, event ordering/duplicates, provider time/plan mismatch, ambiguous network outcomes, concurrent checkout, old scheduled renewal vs replacements, additional-business fallback. Fail closed on inconsistent billing evidence. Feature and provider flags remain off by default; all testing uses disposable databases and mocked provider calls. Full regression suite is required. No live plans/charges or production queries are needed for implementation.
