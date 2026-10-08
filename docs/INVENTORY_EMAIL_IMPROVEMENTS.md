# Task #10.1: inventory emails, preferences and images

## Transaction and delivery architecture

The shared `stock_transition` service produces an in-app warning and, when the
business preference permits, an `EmailOutbox` snapshot in the existing sale or
adjustment transaction. The immutable event key includes the sale/product or
stock-movement identity and warning kind. Above-threshold → positive low stock
and positive → zero are separate transitions; zero takes priority. Restocking
creates no email, but replenishment permits a later legitimate crossing.

Only the verified, unsuspended business owner is the recipient. Names, unit,
quantity and threshold are snapshots of that transition, not a subsequent stock
query. The dispatcher rechecks ownership, account status, the verified email
and current email preference. Changed/invalid recipients are terminal failures;
newly disabled preferences suppress delivery without deleting history.

SQL commit precedes all SMTP and trigger network work. No Python background
thread, filesystem queue, in-memory job queue or long-running worker is used.

### Immediate delivery and recovery

1. After a successful alert transaction commit, an optional HTTPS ingress receives
   a bounded, three-second request containing only `inventory_outbox_ready`.
   The ingress must acknowledge **durable acceptance**, then invoke the protected
   dispatcher in a separate request. It receives no inventory or customer data.
2. The returned authenticated customer page also prompts dispatch using a
   CSRF-protected POST scoped to the current owner and active business. It drains
   at most ten jobs in separate requests. This is a best-effort prompt trigger,
   **not a guarantee after browser closure**.
3. A configured recovery scheduler must independently invoke the bearer-protected
   worker. Each invocation claims at most one due job and sends at most one email.
   For bursts, the scheduler/queue consumer must drain with bounded sequential
   invocations until `idle`, with suitable overall limits, then poll again.

The protected worker is `GET` or `POST /internal/inventory-emails/dispatch`.
It requires `Authorization: Bearer <INVENTORY_EMAIL_DISPATCH_SECRET>`; the secret
must be at least 32 characters. Missing/wrong credentials return 404, disclose
no jobs and never dispatch. Only this non-cookie-authenticated route is CSRF
exempt. Customer notification/preferences/dispatch routes remain CSRF protected.
Configure both scheduling credentials and Vercel Deployment Protection access
through the scheduler's secure configuration, never URLs, public JavaScript or
source files.

**Nothing is provisioned or scheduled by this branch.** Browser-only operation
cannot meet unconditional immediate delivery/retry requirements. Before email
acceptance, configure a durable ingress and independent recovery scheduler, or
explicitly approve an alternative with equivalent prompt/recovery guarantees.
No paid service was added. An external queue is optional as a product choice,
but this generic ingress requires a separately configured durable consumer to
provide server-triggered immediate delivery. Vercel Cron is not required by the
code; it may provide recovery on a plan permitting sufficiently frequent calls.

Official Vercel cron documentation currently limits Hobby to daily execution,
while Pro/Enterprise permit once per minute. A daily schedule is insufficient
for the immediate-alert requirement. This workspace did not establish the
current project plan. No cron entry or Vercel plan/environment change was made.

### Claims, retries and SMTP guarantees

PostgreSQL uses `FOR UPDATE SKIP LOCKED`, followed by a guarded state UPDATE.
SQLite uses the same guarded UPDATE. Claim state and a random token are committed
before SMTP. A 300-second lease outlives the existing 30-second configured
function duration. Failed/abandoned leases recover on a later dispatcher call.
Final abandoned attempts become terminal failures. Completion must match the
claim token, so an expired worker cannot overwrite its successor's result.

Attempts default to five, configurable from 1–10 for newly queued jobs. Backoff
starts at 60 seconds by default and doubles to a 3600-second cap. Persistent
failure becomes `failed`; there is no infinite retry. Dispatcher exceptions log
job ID and exception **class only**, never provider exception text, recipients,
payloads or credentials. The unchanged SMTP transport has its existing socket
timeout; an invocation timeout leaves a durable recoverable lease.

Event-key uniqueness prevents duplicate jobs for the same committed transition.
Concurrent claims minimize parallel delivery. SMTP is **at least once** under
crash recovery: acceptance followed by a crash before recording `sent` can
produce another delivery. SMTP cannot provide exactly-once delivery here.
A crash before recording success cannot safely be treated as success.

## Configuration (names only)

| Variable | Default / purpose |
| --- | --- |
| `INVENTORY_EMAILS_ENABLED` | `false`; new inventory dispatch rollout gate |
| `INVENTORY_EMAIL_BASE_URL` | Empty; required exact HTTPS customer origin |
| `INVENTORY_EMAIL_DISPATCH_SECRET` | Empty; private worker bearer secret |
| `INVENTORY_EMAIL_MAX_ATTEMPTS` | `5`; 1–10 stored on newly created jobs |
| `INVENTORY_EMAIL_RETRY_SECONDS` | `60`; exponential-backoff base |
| `INVENTORY_EMAIL_TRIGGER_URL` | Empty; optional HTTPS durable ingress, no redirects |
| `INVENTORY_EMAIL_TRIGGER_SECRET` | Empty; ingress bearer secret, minimum 32 characters |

Retain existing SMTP and private R2/S3 variables. No production/staging variables
were changed. Pending jobs can be created while dispatch is disabled; before
activating delivery, review old pending jobs and decide whether historical alerts
should be delivered. No automatic deletion/replay command is provided.

Use `https://stock-bridge-staging.vercel.app` as the staging **configuration**
origin and the production customer origin only in its own environment. Templates
contain no hardcoded environment URL. Invalid origins do not consume attempts.
Do not infer environment from `VERCEL_ENV=production`: the isolated staging
project can also use that deployment target. Links go to Inventory/Restocking;
email text names the business and asks the customer to select it when another
business is active. No implicit GET business switching or internal IDs in email.

## Preferences and notification interactions

Extend the existing business-keyed preference table rather than duplicate it.
This preserves previously saved opt-outs and lets Plus businesses retain separate
settings. The five preferences default ON for missing/new preference records.
Existing explicit email False values remain False. Low/out in-app warnings cannot
be disabled. Essential account/billing emails do not consult these preferences.
The shared preference form is on Profile & settings and notification history.

Opening a notification uses a POST with CSRF, scoped by authenticated owner and
selected accessible business. It marks that row only, returns an authoritative
nonnegative unread count and a server-derived destination. JavaScript applies
read styling/badge state after confirmation and blocks repeated requests. Failure
preserves state and permits retry. The HTML POST fallback also works without JS.
Mark as Read, Mark All as Read, pagination and historical rows remain. Opening
the bell/history is read-only. No arbitrary client redirect is accepted.

## Business images

The original byte limit is exactly **5 MiB = 5,242,880 bytes**, identified in UI
as 5 MB (5 MiB). Flask permits 6 MiB multipart requests only on logo uploads;
other requests retain their existing 2 MiB cap. The server enforces the actual
file cap and checks extension, MIME and decoded JPEG/PNG/WebP format, single frame,
12-million-pixel limit, corruption/decompression warnings, EXIF orientation and
safe object keys. Stored output is re-encoded pixel-only WebP, maximum 1024×1024,
with aspect ratio/transparency preserved. Quality 85, then 75 when larger than
500 KiB; 500 KiB is a target, not a destructive quality cap.

Vercel's 4.5 MB function body ceiling cannot accept every raw 5 MiB upload.
The upload UI validates the original size/magic, rejects animated originals,
uses EXIF-aware browser decoding, optimizes to WebP and submits the smaller
payload. The server repeats all stored-image validation. JavaScript/current
browser support is needed for large originals on Vercel; raw no-JS upload above
the platform limit remains impossible and is not claimed supported. Server tests
accept valid JPEG/PNG/WebP at the exact 5 MiB boundary. No binaries enter SQL.

Existing private S3/R2 variables, key structure, replacement ordering and removal
are preserved. Replacement validates/uploads new data before switching the DB
reference, cleans a new orphan on DB failure, then cleans the former object after
commit. Removal clears the DB reference before object cleanup. Temporary deletion
failures log a generic warning; lifecycle/manual cleanup remains an operational
requirement and is not falsely described as a durable deletion retry queue.

## Migration and verification limits

`0016_inventory_email_outbox` follows `0015_notifications_business_logo`.
It adds the outbox and three preference flags and changes email defaults without
rewriting existing opted-out values. No product, sale, payment, subscription,
Lifetime, admin or image-reference schema is changed. SQLite upgrade/downgrade
preservation is tested. PostgreSQL SQL/locking compatibility is reviewed and
compiled; actual hosted PostgreSQL execution/concurrency is **not verified**.

Local tests mock SMTP and R2. Real hosted delivery, R2 replacement/removal,
notification viewport rendering at 375/390/430/768/desktop and platform timeout
behavior require authorized staging acceptance after configuration. VM/CSS/unit
checks are not represented as hosted visual tests.

Sources checked 2026-10-08:
- https://vercel.com/docs/functions/limitations
- https://vercel.com/docs/cron-jobs/usage-and-pricing
