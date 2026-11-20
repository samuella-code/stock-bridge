# Admin business details — review patch

This changes the Business details page and the existing business-status form.
No migration is required. Existing admin authentication, MFA, CSRF, protected
account rejection and compare-and-set suspension guards are retained.

## Access policy for team review

The explanation reflects existing guards; this change does not broaden access.

- A business suspension keeps its records and does not cancel billing.
- Owners cannot open that business's ordinary stock, sales, restocking,
  expenses or Reports screens while suspended. If the owner account remains
  active, sign-in, billing and other permitted businesses remain available.
- Employees lose operational access to that business through the existing
  business entitlement and assignment checks. Other valid assignments remain
  subject to their current role, owner plan and verification requirements.
- Reactivation removes only the business suspension. It does not reactivate a
  suspended owner, restore removed employees, renew an expired plan or bypass
  a business selection/plan limit.
- Administrators retain the existing dedicated read-only inspection route.

This wording is ready for the project team's review; no external team sign-off
is claimed and no access-policy change has been made.

## Status changes and notices

The existing POST route still uses desired_state, expected_state,
expected_suspended_at, reason, confirm and CSRF. The visible checkbox confirmation
is replaced by an exact business_name comparison on both client and server. A
hidden confirm value retains the existing form contract. The business row is
locked before validating its current name. The existing conditional update and
audit transaction still reject stale or repeated requests.

Reasons are a server-validated list; an optional note is limited to 200
characters. Both are included in the audit description, with the signed-in
administrator as actor. Email is sent through the existing status-email service
only when email_owner is checked, and only after the status/audit commit succeeds.
An email failure never rolls back the saved status and is not automatically
retried. Internal notes/reasons are not added to the owner email. The UI reports
whether the existing service confirmed handoff; inbox delivery is not guaranteed.

## Read projections and activity

Plan and business status use the shared entitlement evaluator. Team assignments
are constrained to the same owner and business; names and roles are shown without
exposing full employee emails. Owner email reveal and messaging reuse existing
audited, CSRF-protected admin actions. The win-back button prepares an editable
message in the existing composer and never sends on click.

Recent payments require the business and owner to match; references are shortened
for display with full values available only through the authenticated Copy action.
Counts and last-record hints are business scoped; voided sales/expenses are excluded.

The latest 120 matching audit records are projected into up to eight recent lines.
Repeated read-only openings by the same actor, business and target are grouped
within five minutes of the newest opening, even when other events interleave.
Grouping is display-only: raw records, their timestamps and the Activity view are
preserved. Internal account numbers in read-only descriptions are replaced by a
readable sentence naming the business; the owner's linked name is in the info card.

## Disconnected verification

Tests use disposable SQLite and mocked transport/provider calls. Coverage includes
plan/renewal presentation, masked owner email, team/payment scoping, voided counts,
read-only grouping boundaries, wrong/missing names, allowed reasons and note length,
CSRF/admin guards, repeat/stale requests, optional notices, transport failure and
reactivation preserving owner restrictions. SQL statements compile for PostgreSQL
without connecting to it. Existing staff access regressions cover suspended business
rejection. Browser checks use real Flask-rendered test HTML and the existing CSP at
360, 768 and 1280px; no hosted database or live messages are used.
