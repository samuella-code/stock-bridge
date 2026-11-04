# Manager delegation — review patch, not deployment approval

Based on feat/pro-staff-foundation, commit
6a4593c61c852440dfb1fdc2cbf7c7b6b87eba62, preserving the uncommitted UI polish.

## What is available

The owner can explicitly grant staff_admin (employee/role/assignment management)
and records_export to an account-scoped shared role on Team management. The role
name Manager alone grants nothing. Existing and newly initialized Manager
starting templates are NOT automatically upgraded. The three starting templates,
five occupied employee seats, five active roles and owner exclusion are unchanged.
An ordinary edit on the older owner form preserves existing sensitive authorities;
the new owner authority checkboxes explicitly add/remove them.

A delegated Manager's authority requires an active verified, unsuspended staff
membership, current paid Pro owner entitlement, the existing verified renewal-
continuity rules and currently permitted assigned businesses. Same authoritative
service, same captured UTC instant; no duplicate subscription rules or request
cache. Each mutation takes the existing owner row lock and re-reads authority
before edits. Billing and delegation therefore serialize on the same owner lock.

Managers may invite/remove ordinary employees, resend invitations, assign roles
and businesses, and create/edit/archive ordinary roles only within their own
permissions and business scope. Both the previous and proposed target state must
fit. They cannot change their own access, permissions or assignments, the owner,
privileged roles/employees, or grant staff_admin/records_export even if they hold
those authorities. Only the owner may restore access after a Pro coverage gap.
Shared-role checks include ALL historical/expired references conservatively, not
only currently visible employees: any reference outside authority blocks editing.
Removing a role still requires atomic replacement for affected active members
and unexpired pending invitations, with history preserved. Hidden/unmanageable
records are omitted entirely, including their names, email and business labels.
Global seat and active-role counts are shown for enforcing the existing limits.

The new /staff/teams and /staff/team/<owner_id> routes use explicit account context,
login and existing global CSRF, private/no-store and security-header protections.
Old owner /staff/manage remains owner-only. Services enforce the boundaries too;
a forged POST cannot rely on what was hidden in the UI. Unknown actions/authority
fields reject. AuditLog.actor_id is the acting user; StaffAuditEvent.owner_id stays
the owning account. Delivery-result audit preserves the initiating actor too.
Invite acceptance continues auditing the accepting user. No raw tokens enter audit.

## Exports and financial information

records_export alone is insufficient: sales_view, inventory_view, restocking_view,
expenses_view or financial_view is also required for the requested dataset. The
future export plan intersects requested business IDs with the actor's permitted
scope and applies explicit field allowlists. Cost, revenue/profit, prices and
expense amounts are excluded without financial_view. Unknown datasets/fields or
an empty authorized business/field set reject. This conservative field boundary
will need product review before operational export implementation.

No Staff export endpoint/serializer/query/file generation is added. can_export
always returns False; export_records raises. can_operate remains False. Existing
owner workflows are unchanged. Billing, subscriptions, voids and manual stock
adjustments remain owner-only. No operational Staff workflow has been enabled.

## Migrations, compatibility and PostgreSQL follow-up

No schema/migration changes: new authority strings use StaffRole.permissions JSON;
existing0018 already supports them. No backfill or automatic grants. An older
application version will fail closed validating these new permission strings;
do not roll back code blindly after owners save new authorities. Review any
future rollback/permission removal separately. No migration has been executed.

Staff-specific PostgreSQL tests/launcher and all Phase9 helpers remain unchanged
and paused. The existing18 live cases have NOT been rerun and do not establish
this delegation patch's PostgreSQL correctness. Before enabling this patch on a
hosted environment, separately authorize disposable tests for:
- Manager invite/create at fifth-seat/fifth-role boundaries under the owner lock.
- Revocation/permission removal, Pro expiry/downgrade and business locking racing
  Manager invite/change/resend/shared-role edits: no stale authority after lock.
- Shared-role scope expansion/assignment and role removal racing acceptance.
- Failed delegated writes roll back records and both audit stores; committed
  mutations and delivery outcomes retain actual actor IDs.
- Owner operations and unchanged18-case migration/constraint/seat regressions.
Future source/launcher hashes and case selections require separate review; do not
reuse an old live authorization or silently extend its helper budget.

## Verification boundary

Only database-free policy tests, disconnected real-function tests, static template
checks, compilation and existing invitation DOM checks are authorized now.
Disconnected tests reuse actual function bodies with explicit in-memory fakes;
no Flask application/ORM session/SQLite/PostgreSQL is created. They are not HTTP,
transaction rollback, lifecycle-receipt or PostgreSQL execution evidence. Real
middleware/CSRF, concurrent serialization and end-to-end ORM behavior remain
unverified for this patch and require separately authorized disposable testing.
No new packages, real emails/payments, hosted settings or deployments.
