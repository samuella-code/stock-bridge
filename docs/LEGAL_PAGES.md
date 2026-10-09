# Task 11: draft legal pages

Base: 472642ab12f4fd690e93f76bf5ab4346dcc5e679. Presentation-only change; no migration, entitlement, authentication or checkout changes.

Routes: /privacy, /terms, /refund-policy, /subscription-terms, /contact. Separate legal blueprint avoids verified business-area requirements for anonymous visitors. Existing global account/host restrictions are retained. Legal pages use an independent public HTML shell with existing homepage branding, public site navigation and footer styling; authenticated dashboard chrome is never inherited. No new JavaScript, provider calls or dependencies. Prices come from the existing PLANS source through public_plan_prices.

## Implementation audit

- Signup creates trial-eligible account billing only during subscription rollout. Verified email/social signup starts one trial; start_trial uses a conditional database update. Return login does not reset dates.
- Recurring cancellation stops renewal and keeps verified paid-through access. Same-interval Basic to Plus uses a new immediate full-price checkout; other supported replacements defer to paid-period end and require provider authorization. No custom proration.
- Lifetime evidence remains protected; optional Plus falls back to Lifetime. One-business selection locks the second business without deleting records.
- Account profile supports name/business-name corrections; no complete self-service deletion or personal-data export workflow was identified. Cancellation is not deletion. Existing administrative/reset scripts are not represented as customer privacy tools.
- SMTP sender settings are operational configuration, not an approved public support contact. Do not infer a support email from them.
- Hosted integrations: Vercel, Neon, private S3/R2 logos, Paystack, SMTP and Google OAuth. Base template requests Google Fonts. Models include payment/provider authorization metadata, notifications, per-business preferences and durable email outbox.
- Apple stays hidden. No new consent checkbox.

## Owner and Nigerian counsel approval required before public release

1. Legal operator name, legal address where required, eligibility/minimum age and contractual authority.
2. Operational general/billing/refund/privacy contact, verified delivery and request workflow.
3. Refund eligibility, windows, exceptions, renewal/duplicate charge handling and dispute escalation. No refund/no-refund rule is presumed.
4. Retention schedule, backup deletion limitations, identity verification and request response commitments.
5. SMTP provider identity, provider processing locations, lawful bases and international-transfer safeguards.
6. Intellectual-property licensing, suspension/termination notices and appeal procedures, warranty/liability wording, applicable law and forum.
7. Approved effective date, change notices and scope of contractual Lifetime commitments.

Every page is prominently marked draft/not effective. No placeholders masquerade as working contacts. Do not deploy these drafts to production as final legal terms. No uptime, certification or financial guarantee is introduced.

Reference reviewed: Nigeria Data Protection Commission, https://ndpc.gov.ng/download/nigeria-data-protection-act-2023 and https://forms.ndpc.gov.ng/dsar-request/ (2026-10-09). Legal review must determine applicability, conditions and any statutory duties; these sources do not certify StockBridge compliance.

## Verification

Run pytest including tests/test_legal_pages.py and existing authentication, subscription, admin and notification regressions. Existing JavaScript assertions remain unchanged. Actual 375/390/430/768/desktop visual checks must be reported separately; DOM simulation is not browser viewport evidence.

## Release boundary

Local commit only. Owner/legal review comes first; request approval before push/staging deployment. No database, storage object, billing flag or Paystack configuration change is needed.

## Executed presentation checks

24 new legal-route/integration tests pass in isolation. Existing JavaScript suites: 232 assertions pass; catalogue and mobile-menu DOM suites also pass. Playwright is present, but launching Chromium fails because its executable is not installed (chromium_headless_shell-1234). Actual 375, 390, 430, 768 and desktop viewport checks are BLOCKED; no browser installation or hosted verification performed. Changed-file credential-format scan and git diff --check pass. Public security header behavior matches existing authentication pages; existing additional headers apply to admin routes and were not altered.

Final complete Python regression: 616 passed, 0 failed (120.23 seconds; existing deprecation warnings remain). No existing tests removed or weakened.

## Design refinement

Legal shell no longer extends the authenticated base. Public pages use a green-and-white two-column policy reader, wrapping policy navigation with exactly one aria-current=page marker, skip link and visible focus styles. On smaller screens navigation moves above the article. Contact guidance is grouped into four semantic sections without adding a form or contact claims; its existing sentences and warnings remain. The other four policy bodies are unchanged byte-for-byte. The dashboard base, authentication, billing code and database schemas are unchanged. Browser launch was attempted again: Chromium executable is unavailable, so actual 375/390/430/768/desktop visual and overflow verification remains BLOCKED.

Refinement verification: 53 targeted legal/customer UI tests passed; complete Python suite 627 passed, 0 failed; 232 JavaScript assertions and catalogue/mobile DOM suites passed. No existing tests removed. Credential-format scan and diff validation passed. Six files changed; local commit only.
