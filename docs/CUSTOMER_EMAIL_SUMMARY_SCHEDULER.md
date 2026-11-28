# Customer daily and weekly summary email scheduler

This worker calls the authenticated StockBridge summary endpoint. It does not send mail itself; StockBridge queues summary snapshots in the existing SQL outbox and uses the existing lease, recipient validation, opt-out check and retry handling.

## Schedule

Configure the Cloudflare Worker Cron Trigger as:

- `*/5 7-10 * * *` — every five minutes from 07:00–10:55 UTC (08:00–11:55 WAT).
- The weekly summary is queued by the same run on Mondays for the previous Monday–Sunday WAT week.

Deploy `scripts/cloudflare/customer_summary_scheduler.mjs` as a separate Worker. Do not reuse the admin-report Worker or its credentials. Each Worker invocation makes at most three one-email requests so SMTP does not run in one long serverless request.

## StockBridge environment configuration

Set these only after the migration is approved and applied:

- `CUSTOMER_SUMMARY_EMAILS_ENABLED=true`
- `CUSTOMER_SUMMARY_EMAIL_BASE_URL=https://stock-bridge-one.vercel.app`
- `CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET` — a newly generated random secret of at least 32 characters.

Keep `CUSTOMER_SUMMARY_EMAILS_ENABLED=false` until the worker and schedule are configured. The feature is disabled by default. It does not require enabling inventory alert emails.

## Cloudflare Worker secret

Set the Worker secret with the exact same value:

- `CUSTOMER_SUMMARY_EMAIL_DISPATCH_SECRET`

Never put this secret in source control, a URL, or a plain-text Worker variable.

## Behaviour and safety

- Daily email is sent only when at least one active product is at or below its stock threshold.
- Enabling the daily summary replaces individual low-stock emails for that business; out-of-stock alerts remain immediate.
- Weekly email is sent only when the business had sales, expenses, or stock needing attention.
- Both preferences are opt-in and scoped to each business. New preference columns default to false.
- Event keys are unique per business and WAT reporting period, preventing duplicate summaries on repeated or overlapping runs.
- Only verified, active business owners are eligible. Staff-only and admin accounts are excluded.
- The endpoint requires a bearer secret and drains at most one summary email per request; the Worker makes at most three requests per scheduled invocation.
- The Worker and endpoint do not change production configuration or deploy themselves.
