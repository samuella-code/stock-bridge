# Staff products, expenses, reports and standard role defaults

Prepared from staging c88523e85d90c382689616088a127aa7e3caee8f. No schema migration is required. No hosted database command or deployment is performed by this patch.

## Workflows

Only the exact string STAFF_OPERATIONS_ENABLED=true enables the new routes. Sales and stock flags remain independent. Verified active Staff need a current paid Pro owner grant, current owned role permission and current eligible assigned business. Owner APIs retain their existing full access; Staff use dedicated routes scoped by membership and business, never a session-selected owner business.

| Permission | Enabled behavior |
| --- | --- |
| products_create | Create a product with bounded catalogue fields, selling price, opening quantity and opening purchase cost |
| products_edit | Select/edit active assigned-business products: name, SKU, category, unit, supplier, description, selling price and restocking settings |
| expenses_view | Read assigned-business expense descriptions, categories, dates, amounts and void status |
| expenses_create | Record an operating expense with current server date, bounded positive amount, category, description and note |
| financial_view | Read the existing financial totals for one assigned business over at most 366 UTC days |

Creation and editing do not imply each other. Create-only Staff do not see the editing catalogue; expense-create-only Staff do not see history. Product edits reject stock balances, opening quantity, purchase costs, business/owner IDs, archiving and unknown fields. Historical sale/restock costs are immutable; editing current selling prices does not rewrite old sale prices. Product fields use the existing validator and SKU uniqueness rules. No bulk import, upload, archive, stock adjustment, expense edit/void/backdate, billing or export workflow is enabled.

Product and expense writes acquire the common owner lock before rechecking current authority. Product edits also lock the product, compare an editable-field fingerprint and reject stale forms with 409. Submission references bind employee, owner, membership, business, operation and (for editing) product. Separate signed operation references expire after the existing one-hour limit. Existing TransactionSubmission storage accepts the new internal product_create/product_edit operation labels; the owner operation allowlist is unchanged. Identical retries cannot duplicate opening stock or expenses; changed payloads conflict. All new rows, stock openings, notifications, receipts and actual employee audit actor commit atomically. Owner expense creation now calls the extracted shared expense ledger with its existing date/validation policy.

Reports call app.main.reporting.financial_totals unchanged: sale-time price/cost snapshots, void exclusion and independent expense aggregation. They do not read another assigned/unassigned business, owner-wide aggregates, billing or exports. Financial-view-only roles may read this report without being granted other operational permissions. Product editing never displays prior purchase costs; expense viewing necessarily includes expense amounts.

## Agreed standard roles

Manager has all ten core workflow permissions, including financial_view. Salesperson/Cashier retains sales_view, sales_create and inventory_view. Inventory/Restocking Officer retains inventory_view, products_create, products_edit, restocking_create and restocking_view. No default grants staff_admin or records_export. Names do not grant authority; stored permissions do.

New teams use these templates. Existing teams are NOT silently rewritten on startup. The explicitly invoked staff-defaults-plan command previews built-in-role count, changed count, owners affected and a fingerprint. It rolls back and emits no emails or row contents. The separately approved staff-defaults-apply command requires that exact fingerprint, an active administrator ID and explicit acknowledgement. It locks owner rows in sorted order, rechecks the whole role snapshot, then replaces only the core workflow permissions on existing manager/salesperson/inventory_staff keys. It preserves names, archived status, custom role keys, memberships, business assignments and separately owner-granted team-administration/export permissions. Missing built-in roles are not recreated and archived roles are not reactivated. An immutable before/after role audit identifies the administrator. One commit applies all changes; any failure rolls back all changes. A stale fingerprint is rejected, not refreshed automatically.

The operator must review the exact target, source and dry-run result, reserve a maintenance window against role changes and approve applying the update on that target before invoking it. Production is not included in staging approval. No authorization document or ready-to-run hosted apply command is shipped. Owners can still customize their own roles afterward; this is a one-time standardization, not an override on every request.

## Verification and remaining gates

Offline tests use synthetic memory/private temporary SQLite with PostgreSQL, sockets, HTTP, SMTP, storage and subprocess execution blocked. They cover permission separation, role defaults, current authority revocation, operation/token replay, stale edits, field/amount/date limits, financial business scope/accounting, CSRF, rollback and audited fingerprint-bound backfill. Existing Staff, owner financial/workflow and receipt tests are exercised separately. Review exact logs and verification.json.

Actual PostgreSQL concurrency for new product/expense operations, defaults-update PostgreSQL behavior, full CI and browser/mobile checks remain unverified. This preparation does not commit, push, deploy, enable flags, update hosted role rows, migrate or install packages. Previously live staging sales/inventory/restocking are unchanged.
