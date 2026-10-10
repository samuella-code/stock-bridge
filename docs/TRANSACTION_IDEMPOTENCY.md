# Transaction idempotency foundation (not integrated)

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
ownership, and never automatically retry uncertain financial operations. The
foundation does not change any existing route, form, calculation or behavior.
