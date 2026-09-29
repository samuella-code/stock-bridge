# StockBridge transactional emails

StockBridge keeps its existing SMTP transport. Set `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_FROM_NAME`,
and `SMTP_USE_TLS` in the deployment environment. The From email must be an
address authorized by the SMTP provider. Never put live credentials in
`.env.example` or source control.

The shared `app/templates/email/base.html` layout uses a text StockBridge
wordmark, email-compatible tables and inline styles. Each customer message has
both HTML and a complete plain-text body:

| Event | Trigger |
| --- | --- |
| Verify email | Signup and explicit resend |
| Welcome | First successful verification only |
| Reset password | Customer forgot-password request |
| Password changed | After customer reset is committed |
| Lifetime Access receipt | After Paystack success is verified and customer access is committed |
| Account/business suspended or reactivated | After the admin change is committed |

The existing admin password reset stays on its separate single-use security
flow. No failed/abandoned payment email is sent: the current integration has
no reliable server-side failure event, and leaving checkout is not evidence
that Paystack failed a charge.

Production customer links are built from `CUSTOMER_HOST` (default
`stock-bridge-one.vercel.app`), not the incoming Vercel preview URL or
`ADMIN_HOST`. Keep both host variables accurate if domains change.
Verification tokens expire in 24 hours; customer password reset tokens in one
hour. The token mechanism itself is unchanged.

## Receipt safety

Migration `0011_payment_receipt_email` adds nullable claimed/sent timestamps
to existing payments; it does not reset existing payment or customer data.
Callback, webhook, status, and admin verification all use the same conditional
database claim. Only a verified successful NGN payment for the configured
price, linked to an active non-admin customer business, qualifies. Duplicate
webhooks and callbacks cannot claim an already-sent receipt. SMTP failure
releases the claim and leaves Lifetime Access active. A failed attempt can be
retried on a later webhook/callback/status request; a crashed claim expires
after five minutes. There is no scheduled retry worker.

SMTP and the database cannot offer perfect exactly-once delivery: a process
that crashes after SMTP accepts the message but before the sent timestamp is
committed could send it again on retry. This is a remaining limitation.

Run `flask db upgrade` against the intended environment **before deploying
code that reads the new columns**. Take a database backup and verify the target
first. Do not run a customer reset.

## Sender reputation

Production currently uses a Gmail SMTP mailbox and the same Gmail address for
`SMTP_FROM_EMAIL`; `SMTP_FROM_NAME=StockBridge` is branding, not sender
authentication. StockBridge has no configured custom sending domain in this
repository. We cannot confirm an owned domain's SPF, DKIM or DMARC without a
domain and its DNS/provider settings. Do not set an unverified
`no-reply@...` From address.

If StockBridge obtains a sending domain and a provider verifies it, publish
the provider's SPF TXT record at the domain, enable its DKIM signing and
publish the provider's selector TXT/CNAME records, then publish a DMARC TXT
record at `_dmarc.<domain>` with a policy appropriate for the rollout.
Confirm the provider reports passing SPF/DKIM and domain alignment before
switching `SMTP_FROM_EMAIL`. The precise DNS values must come from the chosen
provider; do not guess them. Inbox placement is controlled by recipient
filters and is not guaranteed by HTML styling or display name.

## Safe manual checks

On a test customer account, request verification and resend; open the link
once and verify exactly one welcome message. Request a password reset and
confirm the one-hour link and password-change notice. To test a real receipt,
complete one intentional payment through the customer site and check the
receipt against the Paystack reference, active access and Admin Portal.
Repeating a callback or webhook should not send another receipt. Test account
suspension/reactivation with an admin and check that admin notes are absent
from the message. Check both HTML and plain text and inspect all links for
the customer domain. Do not send test messages to real customers.

Tests: `.venv/bin/pytest -q --disable-warnings`.
