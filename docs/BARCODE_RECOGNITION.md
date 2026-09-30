# External barcode recognition — review before production

Implemented against existing StockBridge routes, scanner, catalogue and forms.
No schema migration, new dependency, API key, paid account or subscription.
Production must not be enabled until the PR and provider/licensing plan have
been reviewed. No production settings or data were changed for this work.

## Provider research (30 September 2026)

| Provider | Scope | Allowance / price | Reuse and operational findings |
| --- | --- | --- | --- |
| Open Facts universal API | Open Food Facts, Open Beauty Facts, Open Pet Food Facts, Open Products Facts | Free read access without a key. Current documentation: 15 product reads/minute/IP. | Community-maintained; no accuracy/completeness guarantee or commercial SLA. ODbL database, DbCL individual contents, CC BY-SA images. Attribution and share-alike conditions apply. Non-food APIs are experimental. |
| UPCitemdb | General UPC/EAN retail reference data | EXPLORER: 100 combined requests/day, 6 lookups/minute, no signup/key. DEV $99/month; PRO $699/month. | Broad categories, but no independently verified Nigerian coverage or freshness guarantee. Terms license access for customer's operations; SaaS redistribution, persistent caching and image rights need provider clarification before implementation. Not integrated. |
| Barcode Lookup | General retail product names, categories, descriptions, images | Starter $99/month for 5,000 calls; larger plans $249/$499/$949. Requires account/key; free test account requires signup. Terms cap API at 100 calls/minute absent written consent. | No guarantee of a match. Terms require deleting supplied data, including caches/backups, at termination. Not integrated or purchased. |

Sources:
- https://openfoodfacts.github.io/openfoodfacts-server/api/
- https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/scanning-cosmetics-pet-food-and-other-products/
- https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/license-be-on-the-legal-side/
- https://opendatacommons.org/licenses/odbl/1-0/
- https://opendatacommons.org/licenses/dbcl/1-0/
- https://www.upcitemdb.com/api/
- https://www.upcitemdb.com/wp/docs/main/development/plan/
- https://devs.upcitemdb.com/signup
- https://www.barcodelookup.com/api
- https://www.barcodelookup.com/terms-and-conditions

No trustworthy Nigerian/African coverage percentage was established. Do not
advertise complete local, cosmetic, household or pharmacy coverage. Pharmaceutical
identity/dosage/authenticity is not verified by this feature. A barcode response
is a suggestion to check against the actual packaging, never a guarantee.
No live lookup claim is made for 5449000000996 or the supplied 5285001825028;
automated tests use mocked providers. Availability must be tested separately.

ODbL permits commercial use subject to its conditions. Attribution is displayed
next to suggestions. The one-hour reference cache is bounded, ephemeral and
separate from all customer data. No public aggregate catalogue, bulk import from
the provider or persistent shared reference database is created. This separation
is not a blanket exemption from ODbL: systematic extraction and public derivative
databases can trigger share-alike/access obligations. Review the accumulated
reference reuse and attribution plan before enabling for customers; never solve
licensing requirements by disclosing private customer inventory. Product images
are deliberately not requested/displayed/rehosted: image and packaging rights
need separate review. Contact/use-case registration is encouraged by Open Facts;
read operations do not need an API key. No form/email was submitted.

## Configuration and release

`BARCODE_EXTERNAL_LOOKUP_ENABLED` defaults to `false`. Set it to `true` in an
isolated review/test environment to try the feature. No secret is required.
Set it back to `false` to disable external requests while retaining catalogue
matching and manual entry. Tests explicitly enable it and mock all network calls.

Do not merge/deploy to production before review and tests. Once approved: merge
the PR, enable the variable for the intended environment, deploy through the
existing Vercel Git integration, confirm Ready and test on real phones. No `flask
db upgrade` is needed. This change leaves Paystack, admin, auth, email, reset,
inventory calculations and existing records alone.

The adapter uses Open Facts API v3 `product_type=all`. Only validated numeric
GTIN-8/12/13/14 codes with valid check digits are transmitted. Custom identifiers
continue working locally; unsupported identifiers fall back to manual entry.
Leading zeros are preserved; equivalent padded GTINs in provider responses are
accepted. Returned codes must match, and a nonempty product name is required.
Brand/pack size go into the existing editable description; categories map only
to a short allowlist (Drinks, Food, Personal care, Household, Pet food), otherwise
stay blank. Unit stays user supplied. Missing data is never guessed.

## Flow and privacy

1. Scan, upload a photo, or enter a barcode manually.
2. Search only the signed-in business, including archived products. Existing
   product returns immediately with no external request. Archived products must
   be restored through the existing product screen before sale/restock.
3. For an unknown GTIN, the server checks its public-reference cache and then
   the provider. It returns only normalized descriptive fields/source/licence.
4. Preview the reference, choose Review & Add Product, correct the information,
   enter stock/cost/selling price/threshold/unit, and explicitly save. The Add
   form also has Identify barcode, and scanning there identifies it automatically.
   Suggestions fill only blank descriptive inputs, preserving typed information.
5. Save & Scan Next returns to the existing scanner page. Save creates opening
   stock through the existing transaction; recognition itself makes no DB writes.
6. No match, malformed/oversized response, timeout, rate limit or disabled service:
   preserve the barcode and allow manual creation. Products without barcodes
   remain supported. Photos stay on the device through the existing decoder.

Sales/restock baskets still search local inventory only. Unknown scans offer Add
Product, where recognition can help; they never create inventory or transaction
items from external data. The in-progress basket is not automatically carried
through navigation to Add Product; the user must return/rebuild it if they leave.

Only the GTIN and normal HTTP request metadata go to the provider. No customer
email, business name, user ID, pricing, stock, transactions or Paystack data are
transmitted. URLs are constructed server-side. HTTPS redirects are allowed only
to the four fixed Open Facts hosts on port 443, with no URL credentials. Timeout:
3 seconds per HTTP operation; response cap: 64 KiB. No raw provider errors are
shown. Jinja escaping / DOM textContent keep descriptions from becoming HTML.

Cache: at most 512 entries/process, successes 1 hour, misses 5 minutes, no failure
caching; no prices/stock/private data. Process-local rate budgets: 6 uncached
requests/user/minute and 10/process/minute; provider 429 opens a 60-second circuit.
These are **not distributed limits** across Vercel instances/IPs. Before scaling
traffic, use a shared rate limiter/cache or obtain provider capacity guidance;
do not assume per-process limits guarantee the provider's IP quota. No new paid
infrastructure or database schema was added for this review implementation.

## Validation and phone testing

Implementation validation: 140 Python tests passed, including 20 new recognition
tests; catalogue, mobile and new recognition DOM suites passed. The supplied
photo regression passed at 0, 10 and 90 degrees. These results use mocked HTTP
responses and local real image pixels, not a live provider or physical phone.

Run `python -m pytest -q --disable-warnings`. Provider calls are mocked in
`tests/test_recognition.py`. Run the existing catalogue/mobile DOM suites after
rendering fixtures, plus `tests/recognition_dom.cjs`. Scanner decoding uses the
unchanged pinned vendor library; the previous real-photo regression still applies.

In an enabled review environment:
- Scan a barcode already saved in your business: it should open that product.
- Scan a real packaged food/cosmetic with no existing record: a provider match
  should show a reference preview and source, then require your prices/stock.
  5449000000996 is a candidate, not a guaranteed live match; compare the response
  with the actual packaging. Use another provider-listed barcode if absent.
- Try a local custom identifier such as `SB-DEMO-UNKNOWN`: no external lookup,
  manual fallback with barcode preserved. A valid but unlisted GTIN may likewise
  return no match; there is no guaranteed permanently unknown GTIN.
- Edit suggested name/category, enter your own unit, quantity/cost/selling price,
  Save & Scan Next; verify one product/opening movement and return to scanner.
- Scan it in Sales and Restocking: it should select your saved inventory record.
- Deny camera permission: manual entry and Choose File remain available.
- Test iOS Safari and Android Chrome on physical devices before release. Desktop
  DOM tests do not establish hardware-camera or provider-network reliability.
