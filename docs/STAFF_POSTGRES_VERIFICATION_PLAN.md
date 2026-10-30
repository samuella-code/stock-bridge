# Staff Foundation PostgreSQL verification — review plan, not execution approval

## Checkpoint and scope

Local checkout: /workspace/scratch/6febe13e1c30/stockbridge-staff-review
Branch: feat/pro-staff-foundation
HEAD: b18fee6c1a9aec56fb9c92756f4c39ff2c82ba2c
Origin: https://github.com/samuella-code/stock-bridge.git
The reviewed 26-file patch matched its recorded hashes before this step. It stays
unstaged/uncommitted. Source authorization cannot be satisfied by HEAD alone:
application/tests/migration differ from HEAD. A future separately approved commit
and exact runner/helper/migration hashes must bind any live authorization.

Implementation checkpoint (mock-only): the18 scenario IDs now have executable
implementations in tests/test_postgres_staff_foundation.py and Staff-specific
support files. Default collection/execution imports no application and skips all18
without mutation authorization. A separate four-file external Staff launcher is
prepared; no live authorization document exists. Existing Phase9 helpers and all
billing launchers remain unchanged. No database connection, provisioning, migration,
SQL-helper installation or live scenario execution occurred. Mock results and
skips are not PostgreSQL correctness evidence.

## Staging contract evidence and narrow correction

Read-only connected Vercel deployment inspection confirmed:
- Deployment dpl_2eTEGeWRcjHzzAHdGyUrfXucJwpT, READY.
- Project prj_yeCWXPd44jShVUGHAgJu9khMjOMv (stock-bridge-staging).
- Branch release/staging-phase6; SHA b18fee6c1a9aec56fb9c92756f4c39ff2c82ba2c.
- Deployment target staging; project latestDeployment confirms that target.
- Team scope team_Fm51JiuXIjmYvXGnpUyBuytR.

Official source, inspected 2026-10-29:
https://vercel.com/docs/environment-variables/system-environment-variables
VERCEL_ENV has the system values production/preview/development; VERCEL_TARGET_ENV
also carries custom names. Thus target=staging must not be compared to preview.
The narrow code change is ONLY VERCEL_TARGET_ENV == staging. The existing
VERCEL_ENV == preview restriction is retained; no production fallback or OR-list
is added. Exact project, VERCEL=1, branch, approved full SHA, HTTPS origin,
recipient allowlist and default-off flag are unchanged.

Evidence limits: deployment API target is NOT a runtime environment dump. The
runtime VERCEL_ENV/VERCEL_TARGET_ENV values and system-variable exposure/overrides
were not probed. The custom-environment API returned 404, both without scope and
with the known project team. Do not interpret 404 as permission to create/change
an environment. If runtime metadata is absent/different, the gate still rejects;
a separately reviewed non-secret runtime identity check is required before actual
activation. No environment secret was retrieved or printed. No runtime endpoint,
flag activation, redeployment or new live SHA approval was created.

## Exact proposed target — does not exist by this plan

Use a NEW database and restricted login on the existing isolated Ubuntu cluster,
subject to fresh read-only identity checks and separate provisioning approval:

| Field | Proposed value |
| --- | --- |
| PostgreSQL | 16, existing dedicated cluster 16/stockbridge-test |
| Listener | literal IPv4 127.0.0.1:55432 only |
| Data directory | /var/lib/postgresql/16/stockbridge-test |
| Server executable | /usr/lib/postgresql/16/bin/postgres |
| Staff run ID | 7e10a9c42b6d830f |
| Database | stockbridge_test_7e10a9c42b6d830f |
| Login/owner role | stockbridge_test_7e10a9c42b6d830f |
| Application execution user | normal Ubuntu user gamp, UID to be freshly verified |
| Test authentication | SCRAM-SHA-256, explicit protected password input |

The existing billing database/role stockbridge_test_9ca65feb59b0ca68 remains intact.
No old role is granted access to the new DB and no paused data is reused. Verify
new role owns ONLY this DB, has no memberships and has none of superuser,
createdb, createrole, replication or bypassrls; LOGIN only. No postgres/root app
execution, broad monitoring membership or privilege escalation. No cluster/HBA/
listener/global networking changes are authorized. If existing HBA cannot safely
support this exact new identity, stop for a separate reviewed scoped proposal.

The current terminal has no psql, pg_ctl, pg_lsclusters or docker in PATH
(read-only inventory only). It is not the Ubuntu computer; Ubuntu tooling,
cluster health, OIDs, actual listener/process identity and helper availability
are NOT freshly verified. No suitable live target is currently established here.

## Reuse inspected; compatibility gaps that block execution

Read-only inspection covered docs/ISOLATED_POSTGRES_TESTING.md,
 tests/support/isolated_postgres.py, postgres_preflight.py,
 postgres_connections.py, postgres_concurrency.py, ubuntu_postgres_identity.py,
 ubuntu_os_evidence.py, ubuntu_database_evidence.py, the inventory concurrency
module, migration env/0017/0018, Staff models/services/routes and relevant fixtures.
The other checkout's postgres_run_guard.py and billing fixtures were read only as
reference; no old test/launcher was run or edited.

Keep and reuse these safeguards:
1. validated_test_url: explicit disposable ACK/run ID and postgresql+psycopg only;
   literal loopback, exact DB/role, no query/libpq overrides, no application URL
   fallback or Vercel environment. Ubuntu mode pins IPv4:55432 and cluster ACK.
2. Live OS evidence pins Ubuntu cluster/path/executable/PID/start time/listener,
   authenticated bounded helper peer UID and fresh protected backend evidence.
3. Read-only preflight checks restricted role/DB ownership/OIDs, SCRAM/HBA, only
   expected schemas/extensions/relations/indexes and revision. It rechecks server
   identity before/after each check; failures never elevate privileges.
4. connection_policy: connect timeout 5s; worker statement/lock limits 10s/5s;
   observer read-only with statement limit 3s; require_auth=scram-sha-256 and fixed
   pg_catalog,public search path. Fresh verifiers are also explicitly read-only.
5. The concurrency harness already tracks separate worker connections, bounded
   observation/cancellation/join, releases hooks in finally, and fatally exits on
   uncertain shutdown. It does not retry, reset, truncate or drop the database.

Do not reuse unchanged components where they are incompatible:
- Both Windows and Ubuntu migrated validators hardcode 0017. 0018 verification
  would reject. Do NOT spoof alembic_version, relabel a 0018 snapshot as 0017 or
  remove revision/relation checks.
- Existing scoped SQL reader/function is bound to database/run 9ca65feb59b0ca68.
  It cannot authorize this new target. A separately reviewed Staff-specific copy
  must pin the new name/OIDs/body/settings/grants, in the NEW database only.
- postgres_run_guard.py is absent from the Staff base, present in the billing
  checkout and pins billing source/cases/0017. Do not copy its approvals or alter
  its guards to force Staff through it.
- concurrent_posts only supports receipt/void/product locks. Staff serializes at
  UPDATE user SET last_activity_at=user.last_activity_at WHERE user.id=... .
  A Staff owner-lock selector and precommit/rollback instrumentation are needed;
  do not use an unrelated lock selector or claim contention from mere overlap.
- Existing billing launchers pin billing source commit/tree, target and modes.
  They cannot launch this uncommitted Staff tree. The archived portability copy
  was inspected for CLI shape only; it is not proof of the latest Ubuntu copy.
- Existing SQLite app fixture creates/drops all tables and disables CSRF during
  create_app(TESTING=True). NEVER repoint that fixture to PostgreSQL. New Staff
  fixture must explicitly re-enable CSRF after app creation and never create_all,
  drop_all or inherit a hosted URL.

No shared Phase9 helper changes are made. Implemented review scope:
- tests/test_postgres_staff_foundation.py: 18 gated live cases, default skips before
  app import/connection, stage-based selection below.
- tests/support/postgres_staff_fixtures.py: own fixtures, raw-SQL baseline/seed,
  immutable plain-data tokens/IDs, service/HTTP helpers and fresh assertions.
- tests/support/postgres_staff_guard.py: staff-only authorization/manifest/source
  checks; reuse validated_test_url and connection_policy unchanged.
- tests/support/postgres_staff_identity.py: explicit schema0017/schema0018 validator
  using the existing collection/evidence rules, with exact approved revisions and
  relation/index sets. Share authoritative identity validation through a separately
  reviewed adapter design; no cloned weakened checks or revision spoofing.
- tests/support/postgres_staff_races.py: adapt bounded owner-lock observation and
  rollback/precommit events without changing the paused orchestrator's behavior.
- tests/test_postgres_staff_guards.py: mock-only import/authorization/isolation/
  default-skip/timeout/compatibility regressions before any live approval.
- /home/gamp/stockbridge-staff-verification/{launch.py,live_guards.py,test_live_guards.py,RUNBOOK.md}:
  separate reviewed adaptation of the existing portable launcher, never overwrite
  the billing files. Each mode dispatch remains separately authorization-gated.
- tests/support/postgres_staff_evidence.py: a Staff-specific copy of the scoped
  SQL reader/installer specification bound to the new target; never installed
  automatically. Existing protected OS helpers are hash-bound and unchanged.

## Source, process and connection isolation contract

Install guards before repository/dependency import. Clean env allowlist, -I -S -B,
no plugin autoload, no .pth execution or dotenv discovery. Reject DATABASE_URL,
NEON_*, PG* and inherited Vercel/provider/mail credentials. Read password through
protected getpass input in the launcher, never shell args, chat, manifest or logs.
Do not serialize raw connection strings or invitation/CSRF tokens into evidence.

Require a separately reviewed committed source AND explicit source file hashes,
exact migrations through0018, helper/launcher/package hashes, exact target/OIDs,
stage, selected IDs and mutation inventory. Manifest booleans cannot substitute
for live target evidence. No affirmative authorization document is generated now.
Package installation/provenance remains unresolved; pip check alone is not source
provenance. Prior billing risk acceptance does not automatically authorize Staff.

Driver/SQLAlchemy/raw sockets must permit only the validated IPv4 DB tuple and
independent connections to it, plus the authenticated approved Unix evidence
socket. Reject alternate drivers, DNS, libpq overrides, alternate databases,
timeout overrides, SMTP, HTTP/payment providers and object storage. Subprocesses
remain blocked except exact reviewed read-only Git/evidence commands needed by
source/OS verification. Optional IPv6 capability probing may use the previously
reviewed exception-safe capability flag correction; never allow the socket bind.
Latch prohibited attempts even if swallowed; abort before next stage/connection.

Read-only wrappers for preflight/observer/verifiers; explicit read-only transaction
assertion each connection. Write-enabled connections only for the approved
migration or exact synthetic scenario. No shared ORM sessions/models between
workers. Python guards are not an OS sandbox. OS outbound enforcement remains
unverified; Staff requires its own explicit isolation-policy/risk decision before
execution. Do not set a guessed network approval flag or change global networking.

## Starting data and assertions common to every scenario

Capture UTC T once per scenario and freeze Staff service/role/route/delivery clocks
consistently. One verified Pro owner, owned permitted businesses, AccountBilling
pointing to the primary business and active paid-Pro period [T-1d,T+30d) are
synthetic. The owner subscription/entitlement paths are real; no provider call.
Each case has a distinct synthetic owner and unique email/reference namespace.
Foreign/suspended/locked records for isolation checks are separate seeded IDs.

Install thread-safe mail/payment mocks before app creation. Mail transport must
never touch SMTP; provider/HTTP/storage calls latch failure. Keep staff delivery
gate disabled during PG races, so successful invite/resend routes return HTTP200
with disabled status and one STAFF_DELIVERY_RESULT audit. Seed acceptance tokens
through the real service under the owner lock, carry them only as private immutable
strings, and persist only digests. No VERCEL env injection: URL validation rejects it.
Actual gate identity combinations are verified separately in mock-only tests.

Login with independent clients using actual auth/CSRF flow; don't share cookies,
request contexts, SQLAlchemy sessions or live ORM objects between workers. New
identity acceptance remains unverified; existing identity cases authenticate the
verified invited account. CSRF is explicitly ON after create_app. Set exception
handling for injected ValueError to follow the normal400 rollback route. GETs
remain read-only, staff operations remain denied, no auth/payment/HMAC bypass.

Fresh verifier uses a NEW read-only connection/transaction AFTER both workers
finish/commit or the specified rollback barrier. Record exact rows/digests/states,
assignment sets, active role count and occupied seat count. Compare audit JSON
payloads/deltas, not only response statuses. Never print raw link/CSRF/password
payloads or exceptions. Sequence IDs can have gaps after rollback: assert actual
rows, not contiguous sequence numbers. Repeat preflight after each case; do not
silently reconnect/retry uncertain work.

## Exact proposed 18-case matrix

Statuses below derive from app/staff/routes.py: role save/remove success302;
invite/resend status-page200; acceptance302; ValueError/KeyError400; integrity
conflict409 (not expected to replace controlled service checks). An unexpected
409/500 is a failure to investigate, not an alternate pass. Status lists bind
named operations, not arbitrary winner order unless explicitly stated.

| ID | Synthetic start / entry points | Controlled overlap and exact committed expectation |
| --- | --- | --- |
| M00 | Schema0017 + preservation baseline; programmatic Alembic0018 inside one explicit transaction | Inject error after user column/new table DDL; real rollback completed. Fresh verifier sees exact0017 revision, original columns/data, no six Staff tables/new column/audits. No downgrade/reset. |
| M01 | Same unchanged0017 baseline after M00 | Apply0018 once, commit. Exact revision0018; old rows/IDs/costs/payments/legacy/access/audit descriptions unchanged. Every existing User gets staff_only=false; six new Staff tables empty. |
| S01 | Schema0018; own synthetic owner/employee/business/role | Inspector vs0018 models for new columns/types/defaults/nullability/PK/FK/unique/check/indexes. Actual max JSON audit >300 persists; legacy300-char description unchanged. Separate transactions reject foreign role, duplicate owner/user membership, token digest duplicate, self-membership, invalid state/delivery labels and overlength data. Catalogue semantics remain service-validated, not a nonexistent DB JSON-key constraint. |
| R01 | New owner, zero roles/seats; two POST/manage role_save | A holds owner lock, B demonstrably waits. Both302. Three templates initialized once +two distinct custom roles =5 active. Exactly5 STAFF_ROLE_CREATED audit rows/JSON. No employee seats consumed. |
| R02 | Owner has4 active roles; two role_save | A then B serialized. One302, one400;5 active roles, exactly1 new role/audit. Failed request adds no audit. Rename/edit at5 does not increment; archive+new allocation stays5 with history retained. |
| I01 |4 active members, no pending invite; two POST/manage invite, different emails | One200/one400; active4,pending-unexpired1,occupied5; one STAFF_INVITED +one disabled DELIVERY audit. One digest/assignment set, no new staff User/Business/AccountBilling. |
| I02 |4 active members+one pending fifth seat; accept versus fresh invite | Accept302, fresh invite400 in either lock order. Active5,pending0,occupied5; old inviteaccepted, exactly1 STAFF_ACCEPTED audit, no successful invite/delivery event. |
| A01 |3 active members+one pending for existing verified user; two accept POSTs | One302/one400. One membership added, inviteaccepted, occupied4, one STAFF_ACCEPTED; no new User/Business/billing/trial. Both clients authenticate the same invited identity independently. |
| A02 | Same count but invited identity absent; two new-user accept POSTs | One302/one400. Exactly1 staff-only User with email_verified_at NULL and1 membership; no Business/AccountBilling/trial/user BillingEvent. No operational access; one STAFF_ACCEPTED; digest single-use. |
| X01 | Pending invite occupies a seat; resend owns lock first | Resend200, old accept400. Oldrevoked,newpending; exactly one occupied pending seat, no new membership. Digests differ; old token rejected. Audit deltas: INVITE_REPLACED1,INVITED1,DELIVERY_RESULT1. |
| X02 | Same start; accept owns lock first | Accept302,resend400. Originalaccepted, one membership/no pending replacement. Occupied count unchanged; only STAFF_ACCEPTED1. |
| D01 | Pending invite on active role A, replacement B owned/active; role_remove first | Remove302,accept302. Aarchived, invite and new membership useB; assignments/grant retained, active role count decreases1. Audit: ROLE_REASSIGNED(invitation)1,ROLE_REMOVED1,STAFF_ACCEPTED1. |
| D02 | Same start; acceptance first | Accept302,remove302. New membership reassignedB; accepted invitation retains historicalA, Aarchived. Audit: STAFF_ACCEPTED1,ROLE_REASSIGNED(membership)1,ROLE_REMOVED1. No extra seat. |
| D03 | Assigned role A, no replacement; remove races accept | Remove400,accept302. A staysactive; inviteaccepted and membership roleA; only STAFF_ACCEPTED1; no role removal/reassignment audit. |
| F01 |4 active members; A invite fails after assignment/audit flush, B invite waits | A400 rollback confirmed; hold B before commit and independently verify baseline4/no A writes. Then B200 commits one pending,occupied5, only B INVITED+DELIVERY audits. |
| F02 | Pending invitation, identity absent; A acceptance fails after User/membership/audit flush, B accepts | A400 rollback confirmed while B held precommit; baseline token stillpending and no A User/membership/audit. Then B302 creates exactly1 unverified staff-only User/membership, no owner entities/trial, one ACCEPTED audit. |
| F03 | Pending invite; A resend fails after old revocation/new digest flush, B accepts old token | A400 rollback confirmed; baseline oldpending/digest/assignments intact, replacement absent while B precommit. B302 accepts original; exactly1 ACCEPTED audit, no committed REPLACED/INVITED/delivery audit fromA. |
| F04 | Pending assigned role A and owned replacementB; removal fails after reassignment/audit flush, B accepts | A400 rollback confirmed; before B commit roleA active and original assignment unchanged, no new role audit. B302 accepts roleA; occupied seats unchanged and only ACCEPTED audit. |

Each case records initial/final exact records and audit baselines; never assume a
particular auto-increment value. Migration baseline includes original owner,
business, AccountBilling trial/legacy fields, RecurringSubscription periods,
verified successful Payment, BillingEvent, Product, Sale/SaleItem historical cost,
Expense, TransactionSubmission and a300-character AuditLog. Use reviewed raw SQL
or reflected0017 tables BEFORE0018: importing current ORM User would request
staff_only too early. Compare complete canonical row maps before/after, excluding
only the expected new User.staff_only column. No real customer data is copied.

## Lock/rollback instrumentation and deadlines

Reuse the bounded orchestrator design, adding a precise Staff owner-UPDATE selector
and before-commit hooks in a separate Staff adapter. Track driver backend PID from
checkout (not only the lock statement); require distinct A/B/observer/verifier PIDs.
Hold A after its owner UPDATE, start B, observe pg_blocking_pids(B) contains A
before releasing A. pg_stat_activity checks are limited to this run's PID set.
Same-role IDs alone or elapsed time is not lock proof.

For F01-F04: observe B waiting first; deliberately raise ValueError only after
specified real writes/audit flushes. Await A's actual transaction rollback AND
worker return. Then hold B after acquiring the owner lock but before commit.
Only now open a fresh verifier and assert baseline committed state. Inspecting
before rollback or after B commits would not prove atomicity. Release B only
following successful baseline assertions. If rollback/worker state is uncertain,
fail fatally and preserve everything; never retry it.

Worker connect5s, statement10s, lock5s; observer statement3s and READ ONLY;
case wall-clock30s with last5s reserved for release/cancel/join. Bounded lock
observation must finish within its lock-timeout window. No daemon escape, fixed
sleep assumption or auto retry. If any worker/observer/cancel thread remains alive,
pytest exits2; no post-case query/next scenario is issued. Retain sanitized PID/
lock/event timeline/exit evidence and inspect later under separate approval.

## Future execution stages/commands — ALL NOT EXECUTED

These commands are a future sequence only, NOT executed. The files now exist but
cannot pass live authorization with the current dirty/uncommitted source. Review
the entire patch, approve a new source commit and exact source/helper/launcher/
package hashes, establish target identity and give separate stage approval first.
Reuse the dedicated venv only after source/ABI/provenance approval. No installation
is included. Password is securely prompted, never an environment export/argument.
Authorization paths contain no credentials; do not reuse billing approval files.

0. Code-only gate: guarded mock tests, collection expected18 and default skipping
   expected18, before app import/DB connection. Collection/skips are not PG passes.
   Collection/skip checks run only through the external no-connection runner:
   python -m pytest -q tests/test_postgres_staff_guards.py
   python -m pytest --collect-only -q tests/test_postgres_staff_foundation.py
   python -m pytest -q tests/test_postgres_staff_foundation.py
   Run through the reviewed external no-connection runner, not an inherited shell.
1. Separate approval: create only the proposed database/restricted role, verify
   privilege/HBA/identity; install only the new target's scoped evidence if needed.
   SQL passwords must use protected operator input, not SQL files/history/chat.
2. Normal-user read-only empty preflight (new Staff launcher adaptation):
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py preflight --authorization /home/gamp/stockbridge-staff-evidence/empty.json
3. Separate migration approval through0017; no current-model seeding:
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py migrate-0017 --authorization /home/gamp/stockbridge-staff-evidence/migrate0017.json
   Then separately authorized reflected/raw-SQL baseline fixture writes:
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py seed-baseline --authorization /home/gamp/stockbridge-staff-evidence/baseline.json
4. Separately approved M00 rollback test at0017, then stop/read-only verify:
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py migration-rollback --authorization /home/gamp/stockbridge-staff-evidence/m00.json
5. Separately approved M01 upgrade0018, then read-only schema/data verification:
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py migrate-0018 --authorization /home/gamp/stockbridge-staff-evidence/m01.json
6. Approve exact remaining IDs S01,R01,R02,I01,I02,A01,A02,X01,X02,D01,D02,D03,F01,F02,F03,F04:
   /home/gamp/stockbridge-billing-venv/bin/python -I -S -B /home/gamp/stockbridge-staff-verification/launch.py staff --authorization /home/gamp/stockbridge-staff-evidence/staff.json
   Launcher must invoke pytest -x -q tests/test_postgres_staff_foundation.py with
   only the stage-approved selection. No automatic transition between stages.

All live commands above use explicit interpreter/launcher paths, with no PATH
fallback. The proposed test module contains all18 cases but stage selectors partition them;
normal default run skips all18. Recorded collection matches all18 specifications
before a live authorization can name them. No application CLI or unguarded flask
command substitutes for these stage gates.

Evidence-helper budget from implemented dispatch and fixture call sites (not live timing):
- Empty preflight:1 preflight/2 requests,60s launcher deadline.
- Through0017:2 preflights/4 requests; baseline seed:2/4; M00:2/4; M01:2/4.
  Separate bounded sessions for each, no inherited leftovers/auto extension.
- S01+15 concurrent/rollback cases: module entry+exit2 plus2 per case32 =34
  preflights/68 requests; at most1800s. Fits existing64-preflight/128-request cap.
  Any extra preflight, collected case or helper request changes this budget and
  needs review BEFORE execution. Do not install/restart/extend a helper on failure.

## Expected writes and cleanup decision

Authorized later, not now: CREATE ROLE/DB for the new identity; exact scoped SQL
schema/function/grants if required; reviewed migrations0001..0017; synthetic
preservation baseline inserts; attempted0018 DDL rolled back M00; committed0018
DDL M01 (User column, six tables/indexes/constraints/alembic_version); synthetic
per-case owners/businesses/Pro accounts/subscriptions, roles, memberships, invitation
digests/assignments and JSON/generic audit rows; deliberate invalid writes rolled
back in separate transactions. No real provider charge, HMAC bypass, SMTP, email,
product/sales operational staff workflow or existing billing DB mutation.

Only connection/session shutdown is automatic. No drop/delete/truncate/reset,
CASCADE, migration downgrade, cleanup fixture, re-seed or uncertain-operation retry.
On first failure (-x), stop issuing requests, close/cancel only this run's tracked
connections, confirm bounded shutdown, retain DB/role and all committed synthetic
state. Retain sanitized commands/outputs/exit codes, hashes, OIDs, schema snapshots,
row digests and lock/rollback timeline. Never retain raw tokens/passwords/DSNs,
CSRF bodies, SMTP bodies or provider payloads.

Even success retains the DB. Cleanup is a later separate approval after evidence
review: verify exact target name+OIDs/process identity, no active run connections,
remove only the new database then its sole owner role (and new scoped helper
artifacts when no longer used). Never touch old billing DB/role,16/main, cluster
config or shared services. No cleanup command is approved/generated for execution
here. Once separately authorized and revalidated, the only proposed destructive
SQL is DROP DATABASE stockbridge_test_7e10a9c42b6d830f (without FORCE), followed
by DROP ROLE stockbridge_test_7e10a9c42b6d830f after proving no other ownership.
Do not execute that SQL now; do not use DROP OWNED/CASCADE or terminate unrelated
sessions to force cleanup.

## Next review gate

Review the prepared Staff adapters, executable cases, complete source hashes and
separate external launcher. No live approval is generated. A separately approved
clean source commit, approved package provenance/inventory, verified disposable
Ubuntu target/OIDs/schema inventories and protected evidence installation/session
are still required before even a read-only live preflight. Staff-specific acceptance
of application-only isolation is separate from the paused billing decision.
Migration, baseline seeding, M00, M01 and remaining16 cases need separate approvals;
never authorize them as an automatic chain. Current workspace lacks PostgreSQL
server/client/container tooling; no Ubuntu filesystem access is claimed.
