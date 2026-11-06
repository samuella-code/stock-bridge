# Staff PostgreSQL Manager integration — no live approval

Checkpoint: feat/pro-staff-foundation, HEAD
934ea9b732489885bba6e609114da8dbbdba240f. This patch changes only Staff
verification tests/helpers/documentation and a separate external launcher copy.
Application, migration, dependency, billing/inventory harnesses and the original
launcher/ZIP remain unchanged. No PostgreSQL connection or application execution
has occurred during preparation.

## Review findings and integration decisions

The original18 executable cases cover owner behavior. Their module, fixtures,
schema evidence and case IDs are retained unchanged; run their mock regressions
and default skipping again against the new guard. New Manager cases are a separate
16-case module. Old schema1 approvals did not require Manager source ancestry:
all new modes now require schema2, exact staff_integration_commit934ea9b...,
scenario_revision=staff-manager-v2 and a NEW reviewed clean descendant source
commit with exact all-tracked-file/helper/launcher/package inventories. SHA934ea9b
itself lacks this uncommitted test patch and cannot authorize it. The existing
application_base_commit=b18fee6c... continues identifying the migration baseline,
not the integrated Manager source. No approval document is supplied or converted.

The new external launcher copy lives at
/workspace/scratch/6febe13e1c30/stockbridge-staff-manager-pg-launcher.
Do not overwrite /workspace/scratch/6febe13e1c30/stockbridge-staff-pg-launcher or
stockbridge-staff-postgres-launcher.zip. The new copy adds a manager stage and a
fifth exact read-only Git command: merge-base --is-ancestor
934ea9b732489885bba6e609114da8dbbdba240f HEAD. Exit1/missing ancestry fails closed.
No fetch/lazy fetch, ref/index writes, authentication, shell or other subprocess
is permitted. Existing bounded5s/output1MiB command policy and clean environment
are unchanged. Normal Git clone required; linked worktrees remain unsupported.

## Exact staged selection and helper budget

| Mode | Schema before/after | Selection | Preflights / helper requests |
| --- | --- | --- | --- |
| preflight | empty/empty | none | 1 / 2 |
| migrate-0017 | empty/0017 | migration through0017 | 2 / 4 |
| seed-baseline |0017/0017 | historical synthetic baseline | 2 / 4 |
| migration-rollback |0017/0017 | M00 | 2 / 4 |
| migrate-0018 |0017/0018 | M01 | 2 / 4 |
| staff |0018/0018 | original S01 +15 owner cases | 34 / 68 |
| manager |0018/0018 | G01..G06,C01..C06,B01..B04 | 34 / 68 |

Each stage requires separate approval and a separate bounded evidence-helper
session. Never combine staff+manager:68 preflights/136 requests would exceed the
existing64-preflight/128-request cap. No helper extension or automatic transition.
Manager deadline1800s; each race30s including5s shutdown reserve. Existing read-only
preflight/observer/verifier, exact local identity, timeouts and violation latch
remain enforced. The expanded case total is34, not34 PostgreSQL execution passes.

Manager execution may follow the retained foundation stage on the SAME proposed
disposable identity only after separate state review. Foundation adds rows to
original tables, so its old historical-baseline digest cannot be reused blindly.
Successful staff/manager stages emit STAFF END STATE SHA256 over all committed
public application rows. This is evidence only, not approval. The manager mode
requires a protected, reviewed starting_state_sha256, compares it on a fresh
read-only connection after schema/OID preflight, and refuses mismatch before
application creation/scenario dispatch. Failed stages do not emit success approval
or automatically retry. Synthetic rows and sequence gaps remain; no cleanup.

## Synthetic fixture and real entry points

Each Manager case owns a unique prefix and creates an owner, two owned permitted
businesses, a foreign identity/business, paid Pro subscription, explicitly owner-
granted Manager role (staff_admin+records_export, financial_view off), two verified
Manager users and an ordinary verified employee. G01/C03/B01 add a fourth employee;
C04/B02 start at four active roles. Managers are assigned only the first business.
No ORM instance escapes seeding. Clients log in through /auth/login with real CSRF;
every request to /staff/team/<owner_id> has independently obtained CSRF. Real
service/route auth is used, not mocked entitlement decisions. All email/provider
transports remain reject-and-record mocks; invitation delivery gate is disabled.
No operational Staff workflow or export endpoint is enabled.

| ID | Operation and expected final HTTP response | Required committed result |
| --- | --- | --- |
| G01 | Manager GET200; three forged scope POST400; foreign account GET403 | No hidden names or row/audit changes |
| G02 | Six self role/member/invite/revoke/restore requests400 | Identical committed baseline |
| G03 | Seven escalation/protected grant/owner-change requests400 | No authority, invitation, audit or role changes |
| G04 | Owner grants302; Manager edit denied400; owner removes302 | Exact role JSON grants/removal; two owner-actor audits |
| G05 | Actual export-plan service and disabled-operation assertions | Own business IDs/allowed plain fields only; no mutations or export |
| G06 | Manager invitation200 then five change/revoke/role CRUD302 | One invitation, employee revoked, seats unchanged; eight actual-Manager audits |
| C01 | Owner staff_admin removal first302; waiting Manager change403 | Permission removed, ordinary employee unchanged; owner audit only |
| C02 | Manager change first302; owner removal302 | Authorized change committed once before removal; Manager before/after +owner role audit |
| C03 | Two Managers compete for fifth seat200/400 | Exactly one disabled-delivery pending invitation; five seats; winning actor audits |
| C04 | Two Managers compete for fifth role302/400 | Exactly one new role; five active roles; winning actor audit |
| C05 | Owner moves Manager to business2 first302; stale employee change400 | Only Manager assignment changed; no employee mutation |
| C06 | Owner expands employee role scope to business2 first302; Manager shared-role edit400 | Owner assignments committed; shared role name/permissions unchanged |
| B01 | Manager invite rolls back400; waiting Manager invite200 | Baseline restored while B cannot commit; only B invitation/audits remain |
| B02 | Manager role create rolls back400; waiting create302 | Baseline restored; only B role/audit; five active roles |
| B03 | Owner permission removal rolls back400; waiting Manager invite200 | staff_admin retained, only B invitation/audits |
| B04 | Manager change fails after second audit400; waiting owner change302 | Assignment/role/audits rolled back before baseline inspection; only owner final change |

C01 is403, not400: team_manage catches the service denial, rolls back and rebuilds
the page context; lost staff_admin then fails delegated_context and aborts403.
C02 deliberately uses a no-email Manager change (302 redirect) so a post-commit
email/render race cannot make the first response timing-dependent. C05/C06 retain
Manager page authority but lose target/shared-role scope, so rejected POST is400.

## Overlap, rollback and fresh verifiers

Reuse existing Staff owner-lock observer; permit ONLY the exact canonical
/staff/team/<same-owner-id> path in addition to old paths. Independent clients,
SQLAlchemy scoped sessions and PostgreSQL worker PIDs; a third read-only observer
must see A in pg_blocking_pids(B). No guessed sleeps as lock proof. Existing bounded
waits, owned-driver cancellation and fatal unconfirmed shutdown remain.

For B cases, failure is injected after StaffAuditEvent insert (B04 on second insert
to prove actual assignment changes were pending). Require A's rollback event,
returned400 AND joined thread before inspecting committed baseline. B is held by
the before-commit barrier throughout inspection. Baseline uses a new read-only
row verifier and another new read-only audit verifier; both PIDs must differ from
workers/observer. At this barrier no run-owned writer can commit; the two snapshots
therefore inspect stable committed state. Generic AuditLog rows are included to
catch orphan audits. Final verification occurs after worker shutdown and reopens
fresh independent read-only verifiers. No raw tokens/credentials are printed.

Oracles require exact action/actor audit multisets, seat/role counts, invitation
grant/expiry/subscription fields, immutable membership anchors and unchanged
User/business/account/billing identity rows. Denials compare the complete scoped
row/audit snapshot. Sequence advances are intentionally not treated as transactional
rows. No new invitation acceptance semantics or renewal calculation is invented.

## Preserved isolation and remaining gates

Target remains a PROPOSAL: PostgreSQL16,127.0.0.1:55432,
DB/role stockbridge_test_7e10a9c42b6d830f, isolated cluster16/stockbridge-test.
No target has been provisioned/connected/verified here. Existing OS/HBA/backend/
scoped SQL evidence contracts stay byte-identical; no migration/SQL evidence
installation is performed. Package provenance, package/helper/source hashes,
actual Ubuntu identity/OIDs/schema manifests, protected helper sessions and
Staff-specific application-only isolation risk acceptance remain unapproved.
This Python policy is not an OS sandbox. IPv4 import correction, all socket/DB/
SMTP/provider/storage/subprocess blocks and failure latch remain active.

Only guarded mocks and collection/default skipping run now. They do not establish
actual driver/app startup, middleware/CSRF, ORM transactions, PostgreSQL locking,
rollback, SQLSTATE or paid-period correctness. Existing source import-classification
strictness remains; no previously rejected dependency import is exempted. Future
live compatibility and evidence capture need review before any stage authorization.
No database, migration, installation, real email/payment, commit/push/merge/deploy
or settings operation is authorized by this document. Billing/inventory work stays
paused and untouched.
