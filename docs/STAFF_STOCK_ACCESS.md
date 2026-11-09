# Staff inventory and restocking

Prepared from staging merge a7593dc9475a85cea94b867290b92ca7fcb1c1e3. This extension is disabled by default and does not add a schema migration.

## Enabled surface

Only the exact string `STAFF_STOCK_ENABLED=true` activates these routes. `STAFF_SALES_ENABLED` remains independent. Routes are under `/staff/memberships/<membership_id>/businesses/<business_id>/inventory` and `/restocking`.

| Permission | Capability |
| --- | --- |
| inventory_view | Read active product names, units and current stock in assigned businesses; literal search and 30-item pagination |
| restocking_view | Read assigned-business receipt IDs, timestamps, product names, quantities and units; 20-item pagination |
| restocking_create | Select up to 50 active products, enter received quantities and new purchase costs, and record one atomic receipt batch |

These permissions are independent. Create-only employees see the minimal catalogue needed to record goods, without receipt history. View-only employees have no submission form. Historical costs, selling prices, profit, reports and exports are never loaded into these Staff views, even when financial_view is assigned. New purchase costs are write inputs, not permission to inspect prior costs. Supplier and note inputs are stored but not exposed through Staff receipt history.

Product creation/editing, stock adjustments, expenses and financial reports remain unimplemented for Staff. Existing Manager/Inventory role permissions for later workflows do not activate them. Homepage pricing and plan entitlements are unchanged.

## Authority and transaction behavior

Every request checks verified active employee identity, current owned role, current paid Pro owner grant and eligible assigned business. Staff-only accounts cannot use owner routes. URL membership/business bindings are authoritative; session-selected owner business and submitted owner/user/business IDs cannot widen scope.

A write obtains the existing common owner lock before reading current authority, claiming a submission, and locking products in ID order. Owner-lock acquisition expires earlier ORM reads so a waiting writer rechecks revocation, permission removal, assignment and plan changes. Staff sales and owner sale/restock writes use this same lock order.

Restock references have a separate signing salt and bind actor, owner, membership, business, operation and version, with the existing one-hour expiry. Owner and Staff sales references cannot be used for restocking. Receipt uniqueness uses the actual employee actor. Identical replay cannot increment stock again; changed payload returns 409. Unknown/incomplete receipts fail closed.

The existing owner restock ledger is extracted into app/restocking/ledger.py and shared: receipt rows, purchase-cost snapshots, stock increases, product purchase-cost update, movements, owner notifications/outbox entries and audit actor all commit together. No transport is sent by the ledger. Errors roll the entire transaction back; the caller owns commit/rollback. Owner behavior is preserved, including its replay response and history.

Staff quantities must be positive whole numbers, inputs are bounded, purchase costs finite/nonnegative/two decimal places, products active and owned by the bound business. Overflow, duplicate products, mixed-business batches, backdating and direct stock/price/authority overrides are rejected. The conditional stock update retains its overflow check. Responses use the existing no-store and same-origin CSRF headers; state changes require CSRF and never use GET.

## Verification and release

Disconnected tests permit only memory/private temporary SQLite and block PostgreSQL, sockets, HTTP, SMTP, storage and subprocess execution. Coverage includes independent permissions, default/malformed flags, old forms after revocation/plan changes, cross-business/member scope, safe SQL projections, invalid input, CSRF, token binding/expiry, duplicate replay, atomic rollback, actor attribution and owner-before-receipt/ledger ordering. Owner restock/shared receipt/Staff sales regression tests are also run.

PostgreSQL race behavior for the new Staff restock route and browser/mobile layout remain unverified. Review source and exact test output before a separately approved commit/release. Do not enable this flag on hosted staging or production as part of source review. No live authorization document or automatic database operation is supplied.
