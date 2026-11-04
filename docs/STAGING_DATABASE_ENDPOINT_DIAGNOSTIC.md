# Temporary startup database endpoint evidence

This diagnostic has no HTTP endpoint, CLI database operation or background task.
Its output is visible only through existing protected Vercel runtime-log access.
Authorization relies on control of deployment configuration and project logs;
it is not customer or business-administrator authorization.

Disabled by default. The explicit opt-in setting is
`STAGING_DATABASE_ENDPOINT_DIAGNOSTIC_ENABLED=true` (exact spelling/value).
No environment configuration or deployment is authorized by this document.

All runtime metadata below must be present with exact values:

| Variable | Required value |
| --- | --- |
| `VERCEL` | `1` |
| `VERCEL_PROJECT_ID` | `prj_yeCWXPd44jShVUGHAgJu9khMjOMv` |
| `VERCEL_ENV` | `preview` |
| `VERCEL_TARGET_ENV` | `preview` |
| `VERCEL_GIT_REPO_OWNER` | `samuella-code` |
| `VERCEL_GIT_REPO_SLUG` | `stock-bridge` |
| `VERCEL_GIT_COMMIT_REF` | `release/staging-phase6` |

Missing/conflicting metadata or a disabled flag produces no diagnostic record.
Do not spoof metadata to bypass a rejection. Confirm system-variable availability
before separately authorizing a staging deployment. Production is rejected.

At WSGI startup, inspect the first nonempty database variable in the existing
order: `NEON_DATABASE_URL`, `NEON_POSTGRES_URL`, `DATABASE_URL`. Never fall back
from an invalid nonempty higher-priority value. Normalize only the application's
existing postgres/postgresql driver prefixes, then require an exact match with
Flask's effective `SQLALCHEMY_DATABASE_URI`.

Recognized endpoints are the direct and `-pooler` host forms of
`ep-lingering-cherry-b8rquuz8.c-14.us-east-1.aws.neon.tech`. This endpoint was
independently mapped to staging project `empty-recipe-08350132`, branch
`br-orange-rice-b8mzibdh`. The diagnostic does not contact Neon or verify that
mapping again. Unknown hosts, nonstandard ports, ambiguous/malformed URLs,
fragments, duplicate query keys, and query options other than `sslmode` and
`channel_binding` fail closed. An endpoint change requires renewed review.

One attempt per Python process emits at most one warning-level JSON record,
containing only `selected_variable` (an allowlisted name or null),
`endpoint_matches_staging` (boolean), and `database_identity_verified` (always
false). No credentials, URLs, hosts, database names, query data, environment
values or exception messages are logged. A false match is not authorization to
connect. A true match proves configuration consistency only: no SQL, database
connection, actual server identity check, email call or outbox operation occurs.

Changing flags within an already-started process does not rerun the diagnostic.
Fresh serverless processes may each emit one record. Normal application startup
is unchanged apart from this opt-in evidence check; this is not an offline mode
for the rest of StockBridge.

Do not enable inventory email dispatch during diagnostic use. Separately verify
read-only database access and inspect the outbox backlog before any delivery
activation. Remove the temporary hook/module after the investigation, under
separate review. Runtime configuration changes, deployment and removal have not
been performed as part of local implementation.

## Final safety review

The WSGI hook contains ordinary diagnostic import/execution exceptions without
logging them. This includes failed log handlers: there is no fallback logger,
exception text or traceback. Real Flask initialization errors remain outside
that boundary and retain their existing behavior. Process termination signals
are not swallowed. An absent record remains inconclusive, including after an
unexpected diagnostic failure; absence is never positive endpoint evidence.

Vercel's system-variable reference documents VERCEL, VERCEL_ENV,
VERCEL_TARGET_ENV, VERCEL_PROJECT_ID, repository owner/slug and commit ref as
available at build and runtime when system-variable access is enabled. Both
Preview environment fields must equal preview. Repository metadata also depends
on the deployment's Git provenance. No check was removed or made optional.

Reference checked 2026-10-22:
https://vercel.com/docs/environment-variables/system-environment-variables

This local review does not establish the active deployment's exposed variables
or the project's system-variable setting. If any field is unavailable, stop:
request owner verification of supported system-variable exposure and deployment
provenance, or an independently reviewed platform-attested identity mechanism.
Do not add hardcoded replacement metadata. Any settings change or fresh staging
deployment requires separate approval. A local environment export cannot prove
what the running deployment received.

## URL hardening (2026-10-23)

Validate the raw authority against the exact lowercase direct/pooled host list,
optionally followed by literal :5432. Encoded host characters, alternative port
spellings, empty ports and multiple hosts are rejected.

Credentials require a nonempty username and password. Validate percent escapes
and decode credential/path components once using strict UTF-8. Printable escaped
password delimiters (including @, /, :, ?, # and %) are accepted as password data,
consistent with SQLAlchemy's parser. A doubly encoded password escape remains a
literal percent sequence after one decode; it is never decoded recursively.
Decoded controls, invalid UTF-8 and ambiguous raw delimiters are rejected.

Usernames and database names deliberately accept only ASCII letters, digits,
underscore, hyphen, dot and tilde after decoding. Encoded separators, whitespace,
controls and nested escapes in these identifiers fail closed. This supports
standard Neon/StockBridge identifiers but is intentionally not a validator for
every legal PostgreSQL identifier. Noncanonical names require separate review;
the application configuration itself is not changed.

Only literal sslmode=require/verify-ca/verify-full and
channel_binding=require/prefer/disable are accepted. Options may be absent or
appear once each in either order. Encoded keys/values, blank values, malformed
query separators, duplicate options and unknown options fail closed. Fragment
markers, including an empty fragment, are rejected. Absence of SSL options does
not assert transport security: the diagnostic never opens a connection.

Tests compare accepted encoded password representations with the installed
SQLAlchemy make_url parser without creating an engine or opening a connection.
The metadata gates, configuration precedence, startup containment and output
fields are unchanged.
