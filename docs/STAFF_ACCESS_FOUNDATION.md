# Staff Access foundation — review only

This patch adds memberships, invitations, explicit assignments and owner-only
management. No operational staff workflows are enabled. Owner-defined organization
roles are separate from platform User.role. Three editable starting templates:
Manager, Salesperson/Cashier and Inventory/Restocking Officer. Multiple people
may share one role. Each membership has an optional duty label. Names and duty
labels never confer authorization. Financial visibility defaults off for all
starting templates and custom roles unless explicitly selected by the owner.

## Owner-defined roles and permission catalogue

Roles use stable keys scoped by owner, with composite foreign keys from both
memberships and invitations enforcing the same account in the database. Custom
keys are random opaque IDs, never derived from the name. Only a current paid Pro
owner may create, rename, edit or remove roles. Each write shares the existing
owner lock, refreshes time, rechecks entitlement and commits/rolls back atomically.
There are two separate limits: five active roles and five occupied employee
seats per owner. The owner is excluded from both. The three starting templates
consume three role slots, initially leaving room for two custom roles. Renaming/
editing does not consume another slot. Archival frees a role slot while retaining
history. Template initialization and every role-count/creation check occur under
the same common owner lock; no unlocked role creation path is exposed. Roles
consume no employee seats. Starting
roles are inserted only on an explicit owner mutation; GETs do not write. Removed
starting roles are not silently recreated. Duplicate names do not merge roles.

The server accepts only these permission keys (a unique list, no arbitrary flags):
- sales_view: view recorded sales
- sales_create: record sales
- inventory_view: view products and stock
- products_create: create products
- products_edit: edit product details
- restocking_create: record restocking
- restocking_view: view restocking receipts/history
- expenses_view: view operating expenses
- expenses_create: record operating expenses
- financial_view: view financial reports (off by default)

Manager starts with all nine operational keys and no financial_view.
Salesperson/Cashier starts with sales_view, sales_create and inventory_view.
Inventory/Restocking Officer starts with inventory_view, products_create,
products_edit, restocking_create and restocking_view.
A Restocking-only custom role uses exactly inventory_view, restocking_create and
restocking_view, without product creation/editing, sales, expenses or finance. Optional duty text such as “Morning delivery”
is descriptive only. All catalogue flags are stored foundation configuration;
can_operate still returns False for EVERY operation. No existing transaction,
report, export or inventory route is opened to staff by this patch.

Billing, subscriptions, invitations, seat management, role management and access
removal are owner-only. Voids, manual stock adjustments and exports remain
owner-only initially. These capabilities are absent from the catalogue and any
attempt to store them is rejected. Financial permission cannot delegate billing
or organization control. The old per-person Manager finance flag is removed
from this unapplied schema; finance configuration belongs to the shared role.

Editing a shared role changes configuration for every assigned membership and
pending invitation. Acceptance resolves the current owned role, not a stale
permission snapshot. Live operational enforcement remains a later patch and must
check membership eligibility, business authorization and current role permissions.
Unknown/archived roles and malformed permission data fail closed. No cross-request
role/entitlement cache is used. Names such as “Owner” grant no owner rights.

Removing an assigned role requires an explicit different, active role from the
same owner. All active memberships (including currently blocked ones) and all
unexpired pending invitations are reassigned together before archival. Any failure
rolls back the whole transaction. Grants, assignments and occupied seat counts do
not change; reassignment cannot restore expired/downgraded access. Unassigned
roles may be removed without replacement. Removed rows are archived instead of
physically deleted, preserving expired/revoked/accepted history and audit linkage.
Role creation/edit/removal, full before/after permissions and affected record
reassignments are written to structured StaffAuditEvent JSON with short AuditLog
descriptions. Raw tokens and credentials remain excluded.

### Boundaries for the later operational patch

Product creation and product-detail editing are separate permissions: neither
implies the other, restock recording or manual stock adjustment. The later patch
must use field allowlists: product editing must reject direct stock-quantity,
opening-balance and historical cost changes, rather than mass-assigning submitted
fields. Stock mutations must use separately authorized transaction workflows;
manual adjustments remain owner-only. Historical sale/restock costs are immutable.
Product editing grants no access to aggregate financial reports, all-business
figures, billing or exports. Sensitive product fields must be filtered according
to the later reviewed visibility policy; products_edit alone is not unrestricted
financial visibility.

Restocking receipt/history viewing is separate from recording a restock. It must
be limited to currently assigned, permitted businesses and authorized receipt
fields; it does not grant general financial reports, exports or receipt editing/
voiding. Receipt totals/cost visibility must be explicitly reviewed in the later
workflow patch. These boundaries describe required future enforcement, not live
staff functionality: every operational staff route remains disabled now.

Existing customized role rows are never automatically expanded when defaults
change. Only newly initialized templates receive the new defaults. Owners may
explicitly add catalogue permissions through audited role edits. No schema or
migration change is needed for additional validated JSON permission keys; the
existing additive migration remains unapplied.

## Seats and transactions

Five seats per owner, excluding the owner. Active membership rows and unexpired
pending invitation rows consume seats. Revoked members and expired/revoked/
accepted invitations do not. Acceptance atomically converts a pending seat to
an active membership. A duplicate email cannot reserve another active/pending
seat for the same owner. Membership history is retained via a unique owner/user
row, invitation records, immutable audit events and assignment history snapshots.

Every invite, accept, role/assignment change, revoke and reconfirm action takes
the same owner's User-row no-op UPDATE lock used by billing before seat/token
state reads. Re-read state after obtaining the lock; no automatic retries.
Owner routes commit all related changes together or roll them back. Exceptions
cannot leave a new account, seat, assignment or acceptance half committed.
The current time is refreshed after waiting on the owner lock before validating
expiry, entitlement and token lifetime. There is no cross-request permission cache.

PostgreSQL READ COMMITTED row-lock serialization must be tested independently.
SQLite sequential tests and the bounded two-thread mock ledger are NOT evidence
of real database concurrency. The mock cases cover invite/invite, accept/invite,
invite/accept and duplicate accept, and the actual owner-lock SQL construction.
The test/helper set does not resume or modify the paused Phase 9 harness.

A separately authorized disposable PostgreSQL run is required for the last-seat
race, concurrent fifth-role creation/template initialization, role archival vs
creation, resend vs acceptance/revocation/role reassignment, duplicate delivery
claim and delayed delivery completion vs replacement, invitation-to-membership
conversion race, duplicate acceptance, expiry
while a worker waits, first-worker rollback and billing downgrade vs acceptance.
Use independent sessions/backends, bounded statement/lock timeouts, lock overlap
observation and fresh committed-state verification. No such run is authorized
or performed here. Actual production concurrency correctness remains unverified.

## Entitlement and restoration

Management requires the acting owner's current paid Pro access, verified email,
account eligibility and authoritative business entitlements. Only owned,
currently permitted, non-suspended businesses can be assigned. Suspended slots
are not reallocated. Evaluate the same rules again when reading membership
assignments, also checking the employee's identity, suspension and verification.

An explicit owner grant/restoration anchors current authorized Pro access and
records a grant timestamp, original Pro-period ordering, a receipt-event
watermark and its initial coverage end. Subsequent coverage is NOT inferred from subscription ID or mutable
current_period_end. It is extended only by verified successful subscription
payments joined to their immutable payment-period receipts (matching owner,
subscription, business, amount, provider, currency and original interval).

Receipts come from the existing authoritative confirm_payment flow; this patch
does not modify payment processing or call a provider. It pages payment reads
and bounds receipt-key lists to 200. Staff GETs remain read-only. Period-chain
reachability permits adjacent/overlapping paid Pro periods, including replacement
subscription IDs, but payment must have been made on/before the already-covered
boundary. A period gap or a payment made after the boundary cannot bridge expiry.
A newer successful non-Pro period after the explicit grant blocks it even when
an older Pro subscription becomes the winning fallback. A delayed receipt for an
intervening non-Pro period also blocks access; a delayed historical Basic period
older than the original granted Pro period does not count as a new downgrade. Owner restoration
creates a fresh anchor and acknowledges already-known lifecycle evidence.

Delayed on-time payment delivery is distinct from a late payment: access stays
blocked while verification evidence is unavailable; once an immutable receipt
proves an on-time, uninterrupted paid period, it can continue automatically.
Late payment after actual coverage ended requires explicit owner restoration,
even if the invoice period is backdated to look adjacent. Out-of-order/duplicate
periods cannot add time twice. Conflicting period replay is rejected by existing
billing validation. Missing/malformed/foreign receipts cannot extend Pro; unknown
recent non-Pro success fails closed. Historical data without original receipts
is never guessed or backfilled. No claim is made about arbitrary manual database
rewrites outside the authoritative billing paths.

Normal evidenced continuous renewals no longer need owner reconfirmation. An
actual paid gap or downgrade still does. Revoked members need a new invitation.
Active blocked members and unexpired pending invitations still consume seats.
Invitations retain their original <=48h, paid-period-capped expiration; continuity
does not extend their lifetime. Management, including revocation/restoration,
still requires current Pro. Operational staff workflows remain disabled.

## Invitation transport and identities

Inspected app/email_service.py: the common SMTP transport uses a 15-second socket
timeout and sanitizes exception diagnostics. Existing transactional customer_url
uses request/host information and is not used for staff links. Inspected the
inventory outbox's separate enable/origin/claim/retry controls; none are changed
or reused. The historically reported staging_email_policy.py is not present in
this checkout. No claim is made to have inspected a missing file.

### Proposed staff-specific gate (implemented disabled by default)

STAFF_INVITATION_EMAIL_ENABLED defaults to the empty string; only exact "true"
can activate delivery. Empty/"false" means disabled; malformed values fail closed.
Four new configuration declarations do not change any environment setting:
- STAFF_INVITATION_EMAIL_ENABLED
- STAFF_INVITATION_EMAIL_APPROVED_SHA: separately approved full lowercase commit SHA
- STAFF_INVITATION_EMAIL_ORIGIN: exact https://stock-bridge-staging.vercel.app
- STAFF_INVITATION_EMAIL_ALLOWED_RECIPIENTS: exact lowercase comma-separated test
  recipients, no wildcards or whitespace. Recipients outside the list are suppressed.

Initial live gate permits ONLY positively identified staging project
prj_yeCWXPd44jShVUGHAgJu9khMjOMv, VERCEL=1, VERCEL_ENV=preview,
VERCEL_TARGET_ENV=preview, branch release/staging-phase6 and an exact deployed
SHA match. Link origin is pinned HTTPS and never derived from Host headers.
Production delivery is intentionally unavailable through this initial gate; its
policy requires separate review. No configuration values or credentials are
printed. Existing SMTP configuration must also be separately approved/configured.
This patch enables nothing. Test-only synthetic dictionaries exercise the gate
with mocked transport; no real SMTP/email call occurs during verification.

### Sending, failure and explicit resend

The owner enters an employee email, role and business assignments; the employee
need not already exist. After committing the digest invitation and seat, the
request claims delivery under the owner lock, rechecks Pro, expiry, role and
assignments, then commits before SMTP. Only the exact entered/approved recipient
receives the link. The owner sees fixed status labels, never the raw link.
No pending token, body, URL or raw secret is persisted, queued or placed in audit,
logs, browser storage or signed session. Staff pages remain private/no-store,
no-referrer, self-only CSP, with no remote fonts/assistant/activity dispatch.

Delivery states are pending/sending/sent/disabled/configuration_required/failed/
unknown/suppressed. "sent" means SMTP acceptance only, not confirmed inbox delivery.
False transport returns are failed; raised errors are conservatively unknown
because the server might already have accepted the message. Never automatically
retry an uncertain send. A failed/disabled/uncertain pending invitation still uses
ONE seat; owner revocation releases it. Digest-only storage means a lost raw token
cannot be recovered for delivery. The owner must explicitly resend/replace.

Resend takes the common owner lock, rejects accepted/revoked invitations, revokes
the old pending invitation and creates a fresh digest/token in one transaction.
It revalidates the current owned role and assignments, Pro, identity and five-seat
limit. A prior occupied seat is replaced, not added. Expired invitations do not
reserve a seat; replacement can fail if five other seats are occupied. Failure
rolls back old revocation as well. All old links are invalid immediately after
replacement commits. Expiry is newly capped to <=48h/current paid period, not a
secret extension of an already delivered token. Invites/resends remain owner-only
and CSRF protected. Existing acceptance rechecks lifecycle/identity independently.

An active sending claim blocks resend for two minutes. A crashed/overdue claim is
not automatically retried/cleaned up: owner replacement after that window creates
a different token. A delayed old SMTP result cannot update or revive a replaced
invitation. An old email might still arrive, but its link is unusable. SMTP and SQL
cannot form one atomic transaction: a crash after SMTP acceptance but before
status persistence leaves sending/unknown. The UI and explicit replacement path
handle this uncertainty without promising exactly-once delivery. Application/
provider/proxy logging policies must exclude mail/POST bodies; arbitrary upstream
logging is outside the app's guarantees. No automatic retries or outbox extension.

### Acceptance and independent email verification

Store only a SHA-256 digest of a cryptographically random 256-bit token. The
email link secret is a URL fragment, never a server path/query. Actual invitation
JS moves it to the acceptance POST and removes the current fragment via history
replacement; no browser storage. Opening/reviewing does not accept. CSRF remains.
Possession of the emailed invitation is NOT independent email verification.
New users retain email_verified_at unset and no staff access until the EXISTING
signed email-verification flow succeeds. Acceptance creates no Business,
AccountBilling or trial and does not automatically send a verification/welcome
email or enable SMTP. Existing explicit verification resend remains subject to
its existing controls; the staff invitation gate does not authorize that separate
transport. If separate verification delivery is unavailable, access stays blocked.
Staff social linking cannot bypass this check. A leaked link must be revoked/
replaced, not displayed again or replayed.
An existing verified invited identity must authenticate to accept an existing account;
the form cannot overwrite that account's credentials. New identities set their
own 12–128-character confirmed password, hashed with existing Werkzeug policy.
Acceptance creates no Business, AccountBilling or trial. A new/previously empty
identity is staff_only; platform role remains user. New sessions are cleared
before logging the accepted identity in. Admin/suspended identities cannot accept.

Existing owners may accept another owner's invitation, but retain only their
independent owner rights. Staff membership adds no access to the employer's
transactions/business switcher. New staff-only accounts are denied operational
customer routes, owner onboarding and checkout regardless of membership state.
start_trial also rejects staff-only identities, including social/verification
entry points. Existing owner/admin/payment behavior remains otherwise unchanged.
Revocation is checked per request, not by deleting a user or globally logging out
accounts that may belong to other organizations. History is not deleted.

## Schema and audit

Additive Alembic revision 0018_staff_foundation follows 0017_transaction_submission:
user.staff_only defaults false for existing users; six new tables contain
owner roles, memberships, digest invitations with fixed delivery status/attempt
timestamp, explicit assignment associations and structured staff audit metadata. Role/state,
unique token, owner/user uniqueness, foreign keys and self-membership checks are
included. No hosted or local Alembic migration was applied. Tests create only
disposable in-memory schema and inspect upgrade operations through mocks.
Automatic destructive downgrade raises rather than deleting staff history;
production rollback requires an explicit reviewed archival/migration plan.

Audit invitations, acceptance, before/after role/assignment changes, revocation
and restoration. Generic AuditLog entries retain short fixed descriptions within
the existing 300-character column. A linked StaffAuditEvent stores structured
metadata in native SQLAlchemy JSON (PostgreSQL JSON), without truncation or
changing existing audit behavior. Maximum legal integer IDs, five assignments,
maximum role key, 100-character duty label and extreme valid UTC timestamps are
covered; payloads exceeding 300 characters persist correctly in structured
storage. PostgreSQL DDL is compiled/inspected only, not executed. Events contain fixed identifiers, roles, permission flags,
assignment IDs and grant expiry/generation metadata. Never log raw tokens,
passwords, emails, URLs or free-text labels. Current labels remain in the owner
record. Organization roles do not mutate platform User.role.

## Acceptance still required

Review migration on a disposable PostgreSQL target, run genuine concurrency
scenarios with separate authorization, and check live request/access-log policy
at the hosting/proxy layer (the application cannot control arbitrary upstream
request-body logging). In-app logs/audit exclude secrets. Do not log POST bodies.
Physical mobile/desktop checks of forms, labels, focus and empty states remain.
The static preview contains synthetic people, businesses and unusable tokens;
forms/requests are blocked and no application/server/database is imported.

No Staff operational workflows, Staff finance views, seat billing, price changes,
commits, pushes, deployments or hosted database access are part of this patch.


## Staging target evidence and PostgreSQL preparation

Read-only Vercel deployment/project evidence on 2026-10-29 identifies
 dpl_2eTEGeWRcjHzzAHdGyUrfXucJwpT as READY, exact staging project,
release/staging-phase6, commit b18fee6c1a9aec56fb9c92756f4c39ff2c82ba2c,
and target staging. The public system-variable contract distinguishes VERCEL_ENV
(production/preview/development) from VERCEL_TARGET_ENV (also custom names).
The original staff gate required preview/staging. The 2026-11-06 deployed
release uses the standard preview target, confirmed by its successful exact
Paystack identity gate. Staff delivery now requires preview/preview only;
preview/staging is rejected. Project, branch, approved SHA, pinned origin and
recipient allowlist remain mandatory.
Runtime values were not queried and custom-environment lookup returned 404,
including with the project team scope: runtime exposure/overrides remain an
activation prerequisite. Missing/conflicting/production identities fail closed.
Project, branch, approved SHA, exact origin, recipient checks and disabled defaults
are unchanged. No live flag or deployment was changed.

See STAFF_POSTGRES_VERIFICATION_PLAN.md and ../tests/staff_postgres_scenarios.json
for the implemented18 default-skipping scenarios, exact proposed target, future
command contract and blockers. New Staff-specific guard, fixtures, schema identity,
evidence and race adapters plus a separate external launcher are prepared for
review. Existing Phase9 helpers and billing launchers remain unchanged. Only
mock/collection/skip checks have run; PostgreSQL execution remains unverified.
