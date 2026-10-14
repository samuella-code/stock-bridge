# Customer navigation tour

The seven-step tour is separate from business setup_progress. It never follows
workflow links or creates business transactions. It appears on an eligible
customer's dashboard: verified, non-admin, unsuspended, with no product, sale,
restock or expense records across their businesses. Existing populated accounts
are not enrolled. A started tour remains resumable if business activity is added.

State is account-scoped, held in existing AuditLog lifecycle events:
CUSTOMER_TOUR_PROGRESS (description is the zero-based step), CUSTOMER_TOUR_SKIPPED,
and CUSTOMER_TOUR_FINISHED. No new preference schema or migration is required.
These events MUST be retained for the lifetime of the account: audit cleanup
must exclude them. If audit retention is introduced, migrate this state to a
proper durable preference before deleting these events. Do not use billing or
notification preferences for tour state.

Transitions use authenticated identity, CSRF, a user-row write lock and expected
audit version. Stale transitions fail; repeated terminal actions cannot restart.
There are no restart/replay endpoints or controls. Escape interrupts rather than
skips; server-confirmed progress resumes on the next dashboard visit. Failed
network responses do not claim completion; refresh obtains authoritative state.
No tour text or customer input is stored, only lifecycle actions and step numbers.

On mobile the tour opens the existing navigation, highlights its actual links,
and closes only navigation it opened. Its native dialog traps focus; Escape
restores navigation focus. No business links are followed automatically.
Native mobile dialog/keyboard behavior and PostgreSQL locking must still be
verified separately. Unit tests and SQLite do not establish native browser or
PostgreSQL concurrency behavior.
