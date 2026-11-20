# Admin notifications and owner reminders — review implementation

Base: `3da5c9e026b99a3d6c819cd9d6f1dadf6197cc69`.
Branch: `feat/admin-notifications-reminders-review`.

## Behaviour

Notifications uses a green All clear banner only when all four decision counts
are zero: repeated sign-in failures (five within 15 minutes), failed payments,
payments pending over two days, and inconsistent successful-payment metadata.
Both subscription and historical payment decisions are covered. Worth knowing
shows owner trial and renewal decisions separately. The retired business-marker
warning and raw payment references are not restored.

Email is the only channel. WhatsApp controls, numbers and draft links are excluded
until StockBridge has an official number. Alert preferences default off and are
stored per admin; reminder timing switches default off and are shared platform
settings attributed to the admin who saved them. Saved choices do not enable
transport. Auth changes invalidate saved consent. An admin must be verified,
unsuspended and satisfy the existing MFA policy.

The two send panels show masked audience emails, individual checkboxes, five
message placeholders, a self-only test and an optional win-back feedback question.
The audience is paged in groups of 25. Users without a verified email, employees,
admins, suspended owners/businesses, legacy access and opted-out owners are
excluded. Trial messages require a started trial and no chosen subscription.
Manual audience windows are seven days before through 30 days after the relevant
end date. Renewing owners and subsequent paid/scheduled periods are excluded
from win-back. Default wording reflects whether access has already ended.

The six WAT calendar-day timings are day 5 (five elapsed days after trial start),
trial end, trial end +3; paid end -7, paid end, paid end +3. The seven-day contact
cap applies across both reminder kinds and prior generic admin customer messages.
An admin can override only frequency, with a recorded reason of 10–300 characters.
Automatic messages cannot override. Consequently some later timings will be
skipped. Existing unrelated transactional billing and staff emails are unchanged.

Claims serialize on the existing owner write lock, persist the contact cap and a
unique actor/request fingerprint, and commit before transport. A second durable
compare-and-set consumes each SMTP attempt. Replays, failed attempts and unknown
outcomes never automatically retry. The final send checks current eligibility,
email identity, admin consent, deployment gates and automatic switches again.
Unknown/failed claims retain the cap conservatively. The final owner lock is held
through transport; account changes committed before it suppress the message.
Provider acceptance and database commit cannot be made one atomic transaction;
unknown is terminal to avoid duplicates after interruptions.

Every reminder email appends a signed stop-reminders link. GET only displays a
confirmation. POST requires CSRF and records the opt-out; neither overrides nor
automatic timings bypass it. Tokens are purpose-bound, email-bound and expire
in one year. Optional win-back answers are once per delivery, validated against
four choices, and aggregated by received date in Platform reports. Signed links
are removed from admin message-review pages. Public confirmation pages load only
same-origin assets and preserve same-origin Referrer policy for HTTPS CSRF checks.

Activity records claims/results and links each completed attempt to an admin-only
message detail with actor, masked recipient, date, wording and override reason.
Unknown interrupted attempts remain visible from the send result panel. Internal
signed URLs are never included in the Activity display.

## Independent delivery approval

All new flags default off. Existing inventory and weekly report approvals are
unchanged and cannot enable this feature. Before transport can run, independently
approve and configure only the staging Preview `release/staging-phase6` scope:

- `ADMIN_REMINDERS_EMAIL_ENABLED=true`
- `ADMIN_REMINDERS_APPROVED_SHA`: exact reviewed deployment SHA
- `ADMIN_REMINDERS_ALLOWED_RECIPIENTS`: explicit comma-separated lowercase emails
- `ADMIN_REMINDERS_ORIGIN=https://stock-bridge-staging.vercel.app`
- SMTP host and sender must already be configured
- Positive Vercel project, repository, branch, Preview environment and SHA identity
  must match. Production is rejected.

Automatic delivery also requires `ADMIN_REMINDERS_AUTOMATION_ENABLED=true`.
The bounded dispatcher is `flask admin dispatch-reminders --limit 20`, or
`GET /admin/internal/reminders/dispatch` authenticated using a separate Bearer
`ADMIN_REMINDERS_DISPATCH_SECRET` of at least 32 characters. The endpoint processes
at most one attempt per call. No worker, cron, environment variable or credential
has been created or changed for this feature. No command above was run against
staging or production.

Admin failed-payment/security emails are opt-in, at most one attempt per admin,
alert type and WAT calendar day. The daily summary is eligible during 08:00–08:59
WAT. A future scheduler should poll in this hour (and periodically for reminders).
All use the same independent gate and per-admin consent; stopping optional emails
also disables these admin alert choices. A saved switch does not imply delivery.

## Release requirements and evidence limits

Migration `0022_admin_reminders` follows `0021_admin_weekly_reports` and creates
six new tables; no existing billing or role privilege changes. Its upgrade and
downgrade are exercised only on a disposable SQLite database. PostgreSQL DDL is
compiled without connecting. Concurrent claim coverage uses independent SQLite
sessions; this does not prove PostgreSQL lock behaviour.

Do not deploy this code before the migration is explicitly approved and applied:
Notifications and report feedback projections query the new tables. Deployment,
migration, independent mail approval, a dedicated scheduler credential and cron,
and a separately approved allowlisted end-to-end email test remain release work.
No live database, provider or SMTP operation is part of this review.

The self-contained preview has four views: All clear, Needs attention, Trial
reminder and Win-back message. All names/counts are placeholders; submitting only
shows a local preview status. Chromium layout automation was blocked by a
workspace socket restriction; 360/768/1280 visual acceptance still needs browser
review. No browser verification or screenshot is claimed.

## Reference layout follow-up

The supplied notification/reminder images guide the presentation: stacked
Worth knowing cards and Important events on the left, alert preferences on the
right; two explanation cards above a send panel/wording preview; six automatic
timings below the composer. Timings moved from Notifications to the reminder
page, reusing the same fields, CSRF protection and save endpoint. Notifications
links to these settings. Shared admin search is present on both pages.
WhatsApp remains excluded. Delivery services, audiences and approval gates are
unchanged by this presentation update.
