# Transaction idempotency foundation and sales and restocking integration

Migration `0017_transaction_submission` follows the single existing head
`0016_inventory_email_outbox`. Apply it in a separately approved migration window
before any workflow uses the service. No migration or hosted connection was run
for this implementation. The staging build migration guard is unchanged.

Issue a signed token per fresh form with `issue_token(user_id, business_id,
operation)`. Operations are sale, restock, adjustment and expense. Tokens carry a
random 256-bit identifier, scope and version; their lifetime is one hour. Never
log tokens. They supplement, and do not replace, CSRF, authenticated ownership,
active-business selection and entitlement checks. Business IDs must be obtained
from authorized server context, not accepted unchecked from a form.

After authorization and validation, call `claim` before inventory locks or any
mutation. It returns `(receipt, replay)`. For replay return the original internal
`result_reference` through a server-selected redirect without repeating business
mutations. Changed contents raise `SubmissionConflict` (future routes should map
to 409). For a new claim perform business mutations, call `complete` with a safe
internal reference, then commit ONCE. The service does not commit, retry or own
rollback. Every failure requires the caller to roll back the whole transaction.
A session commit guard rejects unfinished service claims. Do not use nested
transactions to claim receipts or manually commit partial mutations.

PostgreSQL INSERT ON CONFLICT DO NOTHING arbitrates the unique scoped key. A
concurrent insert waits for the first transaction. After its commit it reads the
original completed receipt; after rollback it can insert its own claim. SQL
failures, serialization errors and timeouts propagate without retry. Existing
incomplete receipts fail closed; never take them over automatically. Actual
PostgreSQL concurrency execution remains PENDING: no isolated PostgreSQL test
resource or local server is available. SQLite tests establish functional behavior
only, not PostgreSQL concurrency safety. Use two independent PostgreSQL sessions,
hold the first uncommitted claim, then attempt the second; test both first-commit
and first-rollback outcomes before hosted rollout.

Canonical payloads contain only JSON strings, integers, booleans, null, lists and
string-keyed dictionaries. Floats/Decimal/datetime require explicit normalization.
Money must be fixed-scale decimal strings, dates ISO strings; sort multi-product
lines by product ID before hashing. Preserve list semantics otherwise. Include
all business intent, normalized text, reason and explicit/default date choice.
Do not include CSRF tokens, credentials or raw request bodies. Only the SHA-256
hash is stored. A versioned canonical wrapper is used; do not change conventions
for issued tokens. For dynamic prices record intent (explicit price versus
use-current-price); claim BEFORE resolving current values. Store the resolved
price only on the original business transaction. Replay must not resolve it again.

Expired tokens are rejected even for completed receipts: this initial foundation
supports original-result replay within the one-hour token window only. After that,
use authenticated transaction history/manual reconciliation, never automatically
issue a new key for an uncertain operation. Retain receipts indefinitely for now;
no cleanup command is supplied. Future cleanup must retain tombstones or prove
removed keys can never become acceptable again. No historical transaction backfill,
model changes to business records, or automatic receipt deletion is included.
Destructive downgrade is refused because removing receipts reopens replay risk.

Future integrations must preserve submitted tokens on validation errors and
uncertain responses, reject missing keys, map conflicts safely, verify result
ownership, and never automatically retry uncertain financial operations. The foundation is now integrated into sales and restocking creation. Adjustments
and expenses remain outside this phase.

## Sales deployment dependency

Do not deploy the sales and restocking integration until migration 0017 is safely applied and
verified under separate approval. No hosted migration or deployment was performed.
All sales creation uses POST /sales/; Dashboard Record Sale and basket entry use
that form. Missing/invalid/expired tokens return 400 with history/refresh guidance;
changed payload returns 409. Validation returns the form with the same token and
owned basket rows. Browser Back restores the submit button without changing the
token. Successful replay redirects only to the server-selected sales history and
identifies the original sale. No automatic retry or browser redirect is accepted.
Explicit price intent differs from default price intent, even if the amounts match.
Legacy direct POST clients must fetch a form token; there is no token-free bypass.

## Restocking integration

The sole restocking creation endpoint is POST /restocking/receive. The basket,
Dashboard Restock link, and product-selected ?product_id= form share it. Settings
POSTs do not create restock transactions and are unchanged. Each fresh form gets
a user/business/restock token; validation re-renders owned basket rows, supplier,
notes and the original token. Browser Back restores the button without changing
the token. Invalid/expired tokens return 400 and history/refresh guidance; changed
contents return 409. Successful replay uses the stored original batch UUID,
redirects only to restocking history, and never regenerates the batch or changes
stock, cost, movements, notifications or outbox records.

Canonical restock intent contains sorted product IDs, integer quantities, fixed
two-decimal explicit unit costs, normalized supplier and note. Existing restocking
requires explicit unit_cost; blank costs remain invalid. UI prefilled product costs
are submitted as explicit values; no server-resolved default cost is introduced.
The token must be claimed before product locks/stock validation and completed
with the batch identifier before the shared commit. Failures roll back all writes
without retry. Apply and verify migration 0017 under separate approval BEFORE
deploying this workflow. Actual PostgreSQL concurrency remains pending; no hosted
resources were used.

## Inventory adjustments and expenses (Phase 5D.4)

The product detail adjustment form and `/expenses/` creation form issue separate
signed operation-scoped tokens. Validation redisplays submitted values and the
same token. Claims precede stock mutations/expense insertion, and receipts commit
with stock movements, threshold notifications/outbox jobs or the expense audit.
Replays use the original business-scoped movement/expense reference and never
apply mutations or resolve blank date defaults again. A blank date is canonical
`null`; explicit dates, trimmed effective notes, reasons/categories and product
identifiers remain part of intent. Adjustment quantities normalize to integers;
expense amounts normalize equivalent decimal spelling without rounding user
intent. Existing expense precision/validation behavior is preserved.

Expired tokens require checking the relevant history before refreshing; no
automatic financial retry occurs. New tokens allow legitimate identical entries.
No other adjustment or expense creation endpoints exist; product opening stock,
expense voiding, and sale/restock movements remain separate existing workflows.

**Hosted deployment dependency:** apply and verify `0017_transaction_submission`
through a separately authorized migration process before deploying these routes.
The staging migration-skip guard is unchanged. PostgreSQL concurrent claim
verification remains pending on an isolated test database; SQLite regressions
are not evidence of PostgreSQL concurrency execution.
