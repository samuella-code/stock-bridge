# Phase 5D.5A: isolated PostgreSQL preparation

Status: environment blocked; concurrency tests have NOT executed. This document
is a plan for separately approved Phase 5D.5B, not authorization to provision.

## Inspection findings

The existing functional fixtures use explicit in-memory SQLite and call
`create_all` / `drop_all`. They must NOT be reused by swapping in a customer DB
URL. Application configuration prefers `NEON_DATABASE_URL`, then
`NEON_POSTGRES_URL`, then `DATABASE_URL`, and otherwise SQLite. The concurrency
harness must bypass that selection with an explicitly validated test URL.
`psycopg[binary]` is already a dependency. No postgres/initdb/psql, Docker/Podman,
local server directory or explicit isolated-test database setting was available.
The migration head is `0017_transaction_submission`, parent
`0016_inventory_email_outbox`. It must be tested on the new empty disposable
instance only; receipt migration downgrade intentionally refuses destruction.
No hosted migration is permitted by this plan.

## Recommended environment and setup (requires approval)

Prefer a disposable PostgreSQL container on a machine that already supports
Docker, such as the user's laptop. Choose a supported pinned PostgreSQL image
and record its version/digest. Bind to loopback on a nonstandard unused port,
use ephemeral storage without persistent customer mounts, and generate a random
16-hex-character run ID. Create only a dedicated database and non-superuser
owner role named `stockbridge_test_<run ID>`; this role must have no ownership or
privileges in any other database. Keep bootstrap superuser credentials out of
application fixtures. Stop/remove only the exact container created by this run.

Alternatively, after explicit approval, install local PostgreSQL tooling and
create a temporary initdb cluster with a unique data directory and loopback
port. Neither option is possible with currently installed tooling. Creating a
new isolated Neon resource would require separate explicit approval and a
reviewed hosted-target guard; the current guard deliberately rejects ALL hosted
URLs. No existing staging/production Neon resource is an alternative.

Set only these harness variables in the test process (never Vercel):
- `STOCKBRIDGE_TEST_POSTGRES_ACK=disposable-local-only`
- `STOCKBRIDGE_TEST_POSTGRES_RUN_ID=<16 random lowercase hex characters>`
- `STOCKBRIDGE_TEST_POSTGRES_URL=<postgresql+psycopg URL using the dedicated role/database and literal loopback address>`

Do not print the URL/password. Do not load dotenv in the test launcher. Do not
inherit libpq `PG*` overrides or Vercel deployment identity settings. Explicitly
configure the Flask test app with this URL, test-only signing credentials,
mocked payment/SMTP/R2 transports, disabled external dispatch triggers, and
appropriate synthetic entitlements. Keep CSRF enabled for HTTP test cases.

## Isolation gates before any mutation

`tests/support/isolated_postgres.py` validates configuration without connecting.
It rejects absent acknowledgment/run ID/URL, SQLite, every hosted hostname,
shared default port 5432, generic database/role names, URL query overrides,
libpq overrides and hosted deployment identity. It never consults application
DB defaults. This is necessary but NOT sufficient proof of isolation: a
loopback port could be a tunnel or an existing server.

The future provisioning harness must record an ownership manifest containing
its exact container/cluster ID, generated run ID, dedicated port and temporary
storage location. Verify that the port belongs to that newly created local
instance, not a tunnel. On first connectivity, read `current_database()`,
`current_user`, server version and role privileges; verify dedicated names and
non-superuser ownership. Refuse unknown/pre-existing instances. Only after these
gates may it migrate the empty database and seed synthetic users/businesses.
Assert migration revision is exactly 0017. Every setup, migration, seed and
cleanup path must revalidate the target and ownership manifest. Existing
customer fixture helpers with unrestricted drop_all must not be called.

Prefer one disposable database per test run; reset only the verified owned test
instance, never arbitrary SQL selected by a supplied URL. If identity verification
fails, skip cleanup and report the leftover test instance for safe manual review.
The guard currently has no mutation entry point; the future runner must enforce
these gates rather than assuming importing this helper protects unrelated tools.

## Deterministic concurrency design

Use independently authenticated Flask test clients in separate worker threads,
with independent request/app contexts and actual PostgreSQL connections. Never
share a SQLAlchemy session, live model instance or outer fixture app context.
Record backend PIDs and require them to differ. Get signed form tokens and CSRF
cookies before starting; clone the same authenticated session cookie into each
independent client as needed, but retain separate client objects.

Use a two-worker barrier immediately before POST. To prove overlapping claims,
hold worker A immediately after the receipt insert using test-only instrumentation
(no production route edits). Start worker B, observe its PostgreSQL lock wait
using a third read-only connection / pg_blocking_pids, then release A. For sale
void, hold A after acquiring the sale row lock; observe B waiting for that lock.
A barrier alone or sequential responses is NOT proof of a lock race.

Bound every barrier/event wait at 5 seconds, connect timeout at 5 seconds,
lock_timeout at 5 seconds, statement_timeout at 10 seconds and each scenario
at 20 seconds. Release held workers in finally blocks. On timeout fail the test,
cancel/close only that run's own connections and report safely; never retry an
uncertain financial operation. Await worker termination before owned-instance
cleanup. Instrumentation must not include SQL bind values, cookies or tokens in
logs. Observe stock/counts from a fresh verification connection after commits.

| Workflow | Same token concurrent success assertions |
| --- | --- |
| Single sale | One Sale, one SaleItem, one sale movement, one completed receipt; stock decremented once; revenue/COGS once |
| Multi-product sale | One Sale and receipt; one item/movement per product; correct historical prices/costs and totals; deterministic product locking |
| Single restock | One Restock, original batch identifier and receipt; stock increased once; one restock movement |
| Multi-product restock | One batch/receipt; one Restock and movement per product; preserved supplier/cost/note; one batch notification/email |
| Positive/negative adjustment | One adjustment movement/receipt; stock changes once; negative stock prevented; one eligible threshold alert/outbox job |
| Expense | One Expense/receipt and EXPENSE_CREATED audit record; expense totals/net profit change once |
| Concurrent sale void | Stock restored once; one void movement per item; single void timestamp/reason; notifications/outbox unchanged; no new submission receipt (void uses sale row locking, not submission-token integration) |

For each token-protected workflow also execute concurrent same-token changed
payload: one accepted transaction and one 409, regardless of which request wins.
Two different tokens with identical intent must create two legitimate
transactions when stock suffices. Verify same-token replay after later stock or
price changes returns the original result. Inject a failure after mutations but
before commit: receipt, transaction, movements, audit and notification/outbox
changes all roll back; an explicitly initiated same-token retry may then succeed.
Run the first-worker rollback case while the second waits to verify conflict
handling after rollback. Verify cross-user/business rejection and CSRF/entitlement
gates on PostgreSQL too. Capture baseline notification/outbox counts after seed;
assert only event-appropriate deltas, never send actual emails or payments.

Sale void changed-token tests do not apply: it has no submission token. Instead
use two authorized void requests with differing reasons and assert exactly one
winning reason and restoration. No new void idempotency integration is approved.

## Dependencies and current evidence

No new Python dependency is required for the proposed harness: pytest,
SQLAlchemy and psycopg are already installed. Container runtime/server tooling
and an approved disposable instance are missing. An optional testcontainers
package is unnecessary if lifecycle management uses an explicit small harness.
Only pure guard unit tests have run. SQLite coverage and PostgreSQL SQL
compilation remain functional/structural evidence, not concurrency verification.

## Phase 5D.5B existing Windows service preparation

An explicit existing-service mode now statically accepts ONLY this target:
`127.0.0.1:5432`, database `stockbridge_disposable_test`, role
`stockbridge_test_user`. The existing disposable-instance mode remains intact.
This static acceptance is NOT permission to mutate and is not runtime identity
verification. The restricted role's password remains local and must never be
included in reports, manifests, shell arguments or committed files.

Additional process-only configuration (set only after approval):
- `STOCKBRIDGE_TEST_POSTGRES_MODE=existing-local-service`
- `STOCKBRIDGE_TEST_POSTGRES_SERVICE_ACK=postgresql-x64-17:stockbridge_disposable_test:stockbridge_test_user`
- The existing acknowledgment, explicit URL and random run ID remain mandatory.

The read-only preflight can later be invoked from the Windows testing worktree:
`python -m tests.support.postgres_preflight`. It has no migration/seed/cleanup
code and runs SQL inside a read-only transaction with bounded timeouts. It checks
OS evidence BEFORE connecting. It does not import Flask or application Config.
The current environment cannot execute Windows inspection, and this preparation
phase has not executed preflight against any database.

OS evidence uses CIM and active Windows firewall policies, without modifying
anything. Listener PID must be a postgres.exe child of the expected service's
pg_ctl.exe; executable paths and the service data directory must be available
and match the PostgreSQL 17 installation. Missing paths/permission failures are
BLOCKED, not guessed from names. Wildcard listening (`listen_addresses=*`) is
accepted only with complete effective firewall evidence: all three profiles
must be enabled/default-inbound-block, and no potentially applicable inbound
allow rule may expose PostgreSQL beyond loopback. Unknown protocols, symbolic
port selectors, address selectors or incomplete evidence fail closed. This
conservative audit can block a safe but complex firewall policy; report the
exact category for separate review rather than edit settings or bypass it.

Database evidence verifies both connected/session identities, ownership,
PostgreSQL major 17, server address/port, role attributes, memberships and
ownership of other databases. It checks schemas, extensions and relations,
data-directory agreement, listening settings and password-authentication HBA
rules. Reading data_directory or pg_hba_file_rules may be denied to the
restricted role. Do not grant broad monitoring/superuser privileges: this is a
remaining read-only evidence blocker requiring a separately approved solution.
Complex HBA selectors also require review. Password authentication by itself
is not evidence that a wildcard listener is safely firewalled.

The empty phase refuses existing relations or migration records. The migrated
phase requires an approved exact relation inventory and revision
`0017_transaction_submission`; it does not apply that migration. The prepared
HTTP suite is skipped by default BEFORE connection/app creation. Running it
later requires BOTH `STOCKBRIDGE_TEST_POSTGRES_MUTATIONS_ACK=approved-phase5d5b-synthetic-only`
and `STOCKBRIDGE_TEST_POSTGRES_MANIFEST=<local approved JSON file>`.
The manifest must contain run_id, database, role, database_oid, role_oid, revision, relations (fully
qualified tables AND sequences), and approved_synthetic_mutations=true. It must
be produced/reviewed after an explicitly approved migration; no manifest is
created by this preparation. No secrets belong in it. A boolean manifest field
is acknowledgment only, never a substitute for fresh runtime verification.

The runner checks initially empty application tables, revalidates identity
before each synthetic fixture, and uses actual email/password authentication
and CSRF for each independent client. Every test uses a unique synthetic
business/user; the module deliberately leaves synthetic records rather than
resetting tables or automatically deleting a database. Subsequent runs refuse
pre-existing application rows. Cleanup needs a separately authorized scoped
procedure. No unrestricted drop_all/create_all or existing SQLite fixtures are
used. External HTTP, SMTP and S3 transports are blocked by mocks.

Prepared scenarios: 21 creation races (seven single/multi and increase/decrease
variants times same-key, changed-intent and distinct-key), seven forced rollback
and explicit retry cases, and one concurrent multi-product sale void. Actual
PostgreSQL lock contention is observed for same-key/changed-key races and voids;
distinct-key races establish independent backend PIDs and overlapping execution.
The observer uses a separate read-only connection. No worker retries uncertain
operations. Every barrier/hold, SQL wait, observation and join is bounded; worker
connections alone may be cancelled on timeout. These cases are preparation,
not executed concurrency evidence. Additional adversarial interleavings (first
claim rolls back while another waits), entitlement/isolation PostgreSQL cases,
and comprehensive history-field assertions may be expanded after review.

Before any mutations: approve the implementation, review successful Windows
read-only preflight, then separately authorize migration of ONLY the dedicated
empty database and verify 0017, approve the relation manifest, and explicitly
authorize synthetic tests. Neither the empty state nor a passing guard test
supplies that authorization. Production/staging safeguards and deployment
migration control are unchanged.

Database/role OIDs from read-only preflight are bound to the approved migrated
manifest and rechecked before seeding. A recreated database with the same name
is not automatically accepted. For initial authorized execution use pytest
`-x` so a failed/timeout scenario stops further mutations; there is no automatic
cleanup or retry of an uncertain result.
