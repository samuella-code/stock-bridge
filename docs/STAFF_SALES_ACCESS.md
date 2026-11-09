# Staff sales: review candidate

Base release: `9be3d6afe6660d8160a5c8e3072f956f9aa0a60c`.
This candidate has not been committed or released. No schema migration is needed.

## Scope

Only exact `STAFF_SALES_ENABLED=true` enables the new surface. Missing, false,
boolean or malformed values deny reads and writes and hide navigation links.
Existing owner routes retain their owner-only scope. The generic foundation
`can_operate` helper remains closed; this stage uses a sales-specific authority
adapter and does not enable other operational workflows.

`sales_view` permits recorded sale items, selling prices, totals, payment method,
date, void status and notes only in explicitly assigned, currently permitted
businesses. It does not grant a sale form. `sales_create` permits recording sales
using active catalogue products and their current selling prices. The minimal
sale catalogue includes product name, unit, selling price and available quantity
so the employee can complete the sale without an inventory management endpoint.
It does not grant sales history unless `sales_view` is also present.

Staff cannot override prices, enter backdated sales, void sales, edit products,
restock, record expenses, view cost/profit reports, export or use owner business
switching. A form records at most 50 product lines. Product search is server
rendered and shows at most 50 active products; search before filling the sale.

## Authorization and transaction rules

Every request checks verified active employee identity, suspension/admin status,
live membership, owned active role, assigned business, current owner paid Pro,
business eligibility and continuous grant coverage. No role name, duty label,
session-selected owner business, submitted owner ID or hidden form value grants
authority. Write requests acquire the existing common owner lock and reload
authorization before receipt/product locks. A rollback rechecks authority before
rendering any data. Role changes, revocation and assignments use this same lock.

Owner sale creation and owner restocking now acquire the owner lock before receipt/product locks too,
avoiding a reversed order between Staff authorization and owner audit updates.
The shared ledger writes the sale, item price/cost snapshots, conditional stock
decrements, stock movements, notifications and actual actor audit entry without
committing. Each caller completes its receipt and commits all writes together.

Staff submission tokens have their own signing salt and bind employee, owner,
membership and business, with the existing one-hour lifetime. Receipts use the
actual employee ID, never the owner's ID. The existing shared claim/complete
machinery retains canonical payload hashing, conflict detection, replay results
and pending-receipt commit rejection. Permission is rechecked even for a replay.
An unchanged retry does not repeat stock movements, notifications or audit writes.
Changed payloads conflict. Lost/unconfirmed results require owner review, not an
automatic new submission. Cost snapshots remain internal; the Staff history query
does not load cost/profit columns.

Normal CSRF tokens, strict HTTPS same-origin checks, CSP, frame protection and
private/no-store remain in force. No new API, browser storage or external scripts
are introduced. Error messages do not include raw exception details or tokens.
Invitation email and on-screen notices reflect the flag without granting access.

## Validation and release gates

Disconnected integration tests use disposable SQLite and block PostgreSQL,
network, SMTP, storage, subprocess and dotenv. Owner sale/idempotency/workflow
regressions and other transaction receipt callers must pass alongside Staff
permission, lifecycle, tenant, signed-token, rollback, audit and HTTPS tests.
SQLite does not prove PostgreSQL blocking or concurrency.

After code review and a separately approved clean commit, run only the necessary
PostgreSQL follow-up on an approved isolated target: simultaneous identical Staff
submissions, Staff-versus-owner sale/restocking stock contention, and permission/revocation change
contending with Staff creation. Assert one receipt/result, no overselling, defined
lock serialization, rollback preservation and actual actor attribution. Reuse the
existing isolation infrastructure; do not reset earlier records or automatically
retry. Connection/live test approval remains separate.

Commit, push, merge, deployment and environment activation require their own
approval. Keep the flag off on hosted environments until the approved release.
Then review a Staff view-only account and a create-only account on desktop/phone
against one assigned business, and confirm the owner can see the resulting sale.
No production deployment, paid provider operation or email sending is part of
this candidate. Inventory-email work remains paused.
