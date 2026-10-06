# Task #7 local review

Branch: `feat/public-auth-billing-redesign`  
Base: `61423977b6e235d2ed0e94de186c8ba060d4dcae` (`feat/onboarding-guidance`)  
Final local commit: the commit containing this report (run `git rev-parse HEAD`).

Local implementation only. No merge, push, staging deployment or production deployment was performed for Task #7. The last #6.1 staging deployment remains unchanged.

Full automated suite: **417 passed** (84.09 seconds; existing-style datetime deprecation warnings remain).
JavaScript: **34 assertions passed** — customer UI 11, onboarding 12, public UI 11.
Dependency check and whitespace validation: PASS.

## Results and evidence

PASS below means local automated checks and source verification, **not hosted or browser visual acceptance**.

| Check | Result |
| --- | --- |
| Homepage | PASS — workflow, illustrative dashboard, feature sections, pricing, comparison, 12 FAQs and CTA destinations |
| Create Account redesign | PASS — matching entry layout, existing email fields retained, provider availability clearly shown |
| Sign In redesign | PASS — matching layout, existing login fields and recovery links retained |
| Google implementation | BLOCKED for real provider acceptance; real OIDC code implemented and signed-token/fake-exchange tests PASS |
| Apple implementation | BLOCKED for real provider acceptance; real code implemented and signed-token/fake-exchange tests PASS |
| Email/password auth regression | PASS |
| Forgot Password | PASS, including existing reset and email notification regression tests |
| OAuth account linking | PASS — existing account password required, including verified matching provider email |
| OAuth duplicate prevention | PASS — database uniqueness and rollback assertions, stable subject returning login |
| OAuth security tests | PASS — state, expiry, replay, issuer, explicit audience, authorized party, signature, nonce, Google PKCE, redirect configuration, CSRF boundaries, token non-exposure, account restrictions |
| Pricing | PASS — existing server-owned prices, Basic/Plus only, real annual savings |
| Billing & Plan | PASS — current records, dates, statuses, history and management retained |
| Monthly/Yearly toggle | PASS — Python rendering and JavaScript assertions |
| Subscription Summary | PASS — account, plan, interval, allowance, price, amount due and authoritative paid-period timing |
| Paystack handoff | PASS using mocked existing service; no real checkout/payment performed |
| Trial | PASS — existing verified-email eligibility and start-once function retained |
| Basic | PASS — monthly/yearly pricing and one-business regression |
| Plus | PASS — monthly/yearly pricing and two-business regression |
| Legacy Lifetime | PASS — preserved display, optional Plus and fallback regression |
| Plus business switching regression | PASS — existing complete multi-business and onboarding suite |
| Responsive implementation | PASS — responsive source implemented; visual acceptance outstanding |
| 375px visual verification | BLOCKED |
| 390px visual verification | BLOCKED |
| 430px visual verification | BLOCKED |
| 768px visual verification | BLOCKED |
| Desktop visual verification | BLOCKED |
| Database migration added | YES — `0014_social_identity`, parent `0013_account_billing` |

Browser attempt: the cloud browser rejected `http://127.0.0.1:5058/` with `net::ERR_BLOCKED_BY_CLIENT`. No substitute deployment, browser automation outside the supported browser tool, or simulated mobile PASS was used. Viewport-specific browser acceptance must happen when the local app can be reached through the supported tooling, or after separately authorized staging deployment.

## Configuration names and exact staging callbacks

Google:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`

Register and set the staging redirect URI to:

`https://stock-bridge-staging.vercel.app/auth/google/callback`

Apple:

- `APPLE_CLIENT_ID` — Services ID for web authentication
- `APPLE_TEAM_ID`
- `APPLE_KEY_ID`
- `APPLE_PRIVATE_KEY` — Apple ES256 private key; PEM newlines or escaped `\n` supported
- `APPLE_REDIRECT_URI`

Register and set the staging return URL to:

`https://stock-bridge-staging.vercel.app/auth/apple/callback`

Configure Apple's web domain as `stock-bridge-staging.vercel.app` under its appropriate App ID/Services ID. The `/auth/apple/callback/complete` route is an internal same-origin continuation, **not** the provider-registered return URL.

Keep separate provider registrations/credentials for each environment. No production OAuth credentials or callback URLs were configured. Store secrets using environment settings; never paste them into chat. Existing `SECRET_KEY`, HTTPS secure session and SMTP configuration remain necessary. Provider buttons are disabled with an availability explanation when required configuration is missing; configured buttons submit real CSRF-protected initiation forms.

Provider acceptance is BLOCKED because provider credentials are unavailable. Apple account/Services ID/key setup, real authorization and the browser's actual Lax-cookie behavior still need hosted acceptance. Google provider consent/redirect setup and real authorization need hosted acceptance. No real provider success is claimed.

Apple private relay email is accepted when confirmed verified by the signed token. Apple's raw first-authorization `user` object is never trusted for identity or email; customers enter their display/business names in the completion form. Returning identities work without repeat name/email. Register the existing SMTP sender with Apple's relay service if messages to relay accounts are needed.

Unverified provider email does not create or link an account: it fails safely and offers the existing email signup/verification path. New verified social signup sets the existing `email_verified_at` field, creates the same first business/account billing structure and invokes the existing `start_trial`. Returning logins never reset a trial. Existing passwords stay unchanged. A social-only account can establish a usable password via the existing verified-email password-reset flow before linking another provider with the same email. Automatic email-only merging is deliberately avoided.

## Security and migration

`SocialIdentity` stores user ID, provider, stable provider subject and created time. Unique `(provider, provider_subject)` prevents an identity belonging to two users; unique `(user_id, provider)` prevents conflicting identities for one provider. No access, refresh or ID tokens are stored in this table or the Flask session.

Authlib 1.8.0 handles the code exchange, state, Google S256 PKCE and JWKS signature/claim validation. Application options explicitly constrain issuer/audience and reject missing or opted-out nonce. Google accepts its documented issuer variants. Apple uses a short-lived ES256 client-secret JWT and signed RS256 ID tokens; PKCE is not advertised for Apple by this implementation. Provider endpoints are server-owned HTTPS addresses. Redirect callbacks are exact HTTPS environment configuration, never derived from Host/next/browser inputs. Post-login destinations are fixed internal endpoints.

Apple's cross-site form POST first returns a no-store same-origin continuation form containing only code/state/error, with escaping, restrictive CSP and no-referrer. The same-origin POST supplies the existing Lax session cookie, then validates its state and nonce before code exchange. It does not switch the application's session cookie to SameSite=None. Only the provider callback/continuation are exempt from generic form CSRF because they have OIDC state protection; start, completion/linking and all billing mutations retain normal CSRF protection.

User creation, first business, billing row and identity are one database transaction. Unique conflicts during flush or commit roll back all new workspace writes. Session context is cleared before login. Social auth cannot authenticate admin-enabled, admin-role or suspended accounts; existing admin routes remain untouched. Protocol debug logging that would expose PKCE verifier details is suppressed. Customer error messages never include provider/library exception text.

Migration is additive. Local tests upgrade the complete migration chain to 0013, preserve an existing user/password, upgrade to 0014, insert an identity, downgrade only 0014 back to 0013, verify the user/password remains, then upgrade again. Downgrade removes social identities only; it cannot be used to erase or downgrade billing history (0013's existing destructive-downgrade prohibition stays intact). The established Lifetime migration test now asserts the new head and identity table **in addition to** its existing Lifetime/payment/admin preservation checks. No existing assertion was removed.

Before a later authorized deployment, apply `flask db upgrade` to the isolated staging DB and verify isolation first. Keep providers unavailable until migration and provider configuration are ready. No migration was applied to the actual staging or production DB in Task #7.

## Billing behavior preserved

`PLANS` remains the source of all prices and allowances. Review GETs validate Basic/Plus and monthly/yearly server-side; submitted price/allowance/provider-code fields have no authority. Existing protected checkout/change POSTs remain the final service entry points and revalidate choices.

An initial purchase or same-interval Basic → Plus upgrade shows the full subscription amount due today. An interval change/downgrade shows zero due today and the existing paid-period end as scheduled start; it confirms through the existing change service, not a fake immediate checkout. No tax, fee, proration credit or invented renewal date is added. Existing cancel, card, pending-checkout, status verification and webhook logic remains intact. Lifetime Basic purchase is not offered. No stock, sales, COGS, expense, profit, restocking, inventory-history, business-ownership, entitlement, trial, Lifetime or Paystack core service was edited.

## Fixes during implementation

- Explicit audience allowlisting prevents accepting a token for a different client even when its `azp` matches.
- Explicit nonce check rejects nonce opt-out and missing nonce.
- Non-ASCII/oversized state is rejected safely instead of producing a string-comparison server error.
- All social signup flush/commit conflicts roll back the entire workspace.
- Apple's same-origin continuation preserves the existing Lax cookie policy.
- Public CTA links get actual flex/tap-target styling, public interval controls get their own styles and inputs get label spacing.
- Retained the existing accurate homepage stock wording to preserve its regression assertion.

No existing business-rule bug or browser-discovered UI bug was found. The listed changes are hardening/corrections of the new implementation.

## Pre-production checklist and stop boundary

- Task #7 real Google and Apple authorization, cancellation, returning login, private relay and linking acceptance.
- Task #7 real desktop and 375px/390px/430px/768px visual checks: menu, forms, cards, comparison, FAQ, alerts and overflow.
- Task #6/#6.1 blocked hosted fixtures and actual mobile acceptance remain outstanding.
- **Task #5 hosted PostgreSQL concurrency: BLOCKED/outstanding**. Local SQLite concurrency tests do not satisfy hosted PostgreSQL acceptance. Verify Basic/Plus business limit locking, duplicates, orphans, partial writes and rollback only against the isolated staging PostgreSQL DB.

| Explicit change flag | Value |
| --- | --- |
| HOMEPAGE REDESIGNED | YES |
| GOOGLE AUTH IMPLEMENTED | YES |
| APPLE AUTH IMPLEMENTED | YES |
| DATABASE SCHEMA CHANGED | YES — additive local identity migration |
| BUSINESS LOGIC CHANGED | NO — existing business/entitlement/calculation rules |
| PAYSTACK CORE LOGIC CHANGED | NO |
| TRIAL RULES CHANGED | NO |
| LIFETIME RULES CHANGED | NO |
| PRODUCTION MODIFIED | NO |
| PRODUCTION DATABASE MODIFIED | NO |
| PRODUCTION SUBSCRIPTION FLAGS CHANGED | NO |
| LIVE PAYSTACK MODIFIED | NO |

STOP after local review. Do not independently deploy to staging, merge or deploy production.

## Files changed

- `.env.example`
- `app/__init__.py`
- `app/auth/social.py`
- `app/main/routes.py`
- `app/models.py`
- `app/static/css/entry.css`
- `app/static/js/apple-return.js`
- `app/static/js/public.js`
- `app/subscriptions/routes.py`
- `app/templates/auth/apple_return.html`
- `app/templates/auth/login.html`
- `app/templates/auth/signup.html`
- `app/templates/auth/social_finish.html`
- `app/templates/base.html`
- `app/templates/components/public.html`
- `app/templates/home.html`
- `app/templates/subscriptions/billing.html`
- `app/templates/subscriptions/review.html`
- `config.py`
- `docs/PUBLIC_AUTH_BILLING_REVIEW.md`
- `migrations/versions/0014_social_identity.py`
- `requirements.txt`
- `tests/public_ui.cjs`
- `tests/test_billing.py`
- `tests/test_entry_redesign.py`
- `tests/test_social_auth.py`
- `tests/test_social_migration.py`

## Staging OAuth completion regression

The HTTPS completion POST failed with Flask-WTF's `The referrer header is missing.`
The completion page deliberately sends `Referrer-Policy: no-referrer`, while the
global HTTPS CSRF middleware also requires a same-origin Referer. Local HTTP
tests with CSRF disabled did not exercise that conflict.

`social_finish` now replaces that route's automatic Referer check with explicit
Flask-WTF `validate_csrf` validation of the signed, expiring form token against
the current session. Invalid tokens still raise the normal CSRFError. Global
CSRF settings, email/password authentication, OAuth start protection and callback
state/nonce/signature/issuer/audience validation are unchanged. Provider identity
and email still come only from the validated, session-bound pending proof. The
proof's expiry, password-confirmed linking, session rotation, identity uniqueness
and fixed post-login destinations remain unchanged. Apple uses the same protected
completion form, while its state-protected return bridge is unchanged.

Fourteen additional HTTPS regressions run with CSRF and strict SSL checks enabled:
Google/Apple signup, password-confirmed linking and returning login without
Referer; forged identity fields; missing/invalid/expired/other-session CSRF;
missing/expired/other-session pending proof; completion and callback replay;
invalid OAuth state; and unchanged email-login/OAuth-start CSRF policy. The
signup regressions fail against the original implementation and pass with the
fix. Existing signed JWT validation tests remain part of the full suite.

No provider credentials or platform settings are changed by this fix. Staging
retesting must use the existing isolated project/database; no payment is needed.
