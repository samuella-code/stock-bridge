# Barcode removal review

## Inspection and production observation

Baseline: main `a5edd1fd65b4c4f1de66433ee0372ea43084eaf1`. Local branch: `chore/remove-barcode`. The unmerged external recognition PR #23 and camera PR #24 are excluded; do not merge those superseded barcode additions into this removal.

The repository-wide inspection found:

| Component | Existing barcode use | Removal strategy |
| --- | --- | --- |
| `app/models.py` | Nullable Product field, business/barcode constraint | Retain inert mapping/schema to preserve data |
| `migrations/versions/0012_product_catalogue.py` | Barcode field/constraint plus two useful catalogue indexes | Leave applied history completely unchanged |
| `app/products/catalogue.py` | Validation, duplicate checks, import header, product search | Remove business use; ignore old optional import column |
| `app/products/routes.py` | Creation/edit, scan-next redirect, barcode lookup and scan route | Keep SKU/text lookup; remove scanner route/branches |
| `app/static/js/barcode.js` | Camera/native/fallback/photo/manual scanner and lookup | Remove file |
| `app/static/js/vendor/` | Pinned ZXing decoder, README and two licenses | Remove all four barcode-only files |
| `app/static/js/basket.js` | Barcode selection in sale/restock baskets | Keep text-search basket and totals |
| `app/static/css/catalogue.css` | Scanner styling and four entry columns | Remove scanner rules; use three choices |
| `app/templates/products/scan.html` | Scanner/unknown-code page | Remove file |
| Product templates | Form/details/index/Quick Add/import/preview/entry/basket | Remove barcode fields/display/search/buttons |
| Sales/restocking templates | Scanner script inclusion | Keep basket script; remove scanner script |
| `tests/test_catalogue.py` | Barcode-specific assertions mixed with business regression tests | Convert business cases to SKU; add removal/legacy-safety coverage |
| `tests/catalogue_dom.cjs` | Scanner behavior mixed with basket tests | Keep search/basket/import/mobile assertions |
| `tests/mobile_ui.cjs` | Scanner lifecycle mixed with menu tests | Keep all menu assertions; remove scanner-only tests/dependencies |
| `tests/render_catalogue_dom.py` | Scanner page/fixtures | Keep disposable business template renderer |
| `tests/barcode_photo.cjs` | Optional real-photo decoding regression | Remove file |
| `docs/PRODUCT_CATALOGUE.md` | Scanner instructions/deployment/testing notes | Replace current instructions with supported workflows and retention policy |

Read-only check on Neon project `stockbridge-production`, branch `main`, Primary compute, database `neondb`, on 2026-09-30 UTC:

```sql
SELECT (SELECT version_num FROM alembic_version) AS migration,
       count(*) AS products_total,
       count(*) FILTER (WHERE barcode IS NOT NULL AND btrim(barcode) <> '')
         AS products_with_barcode
FROM product;
```

| Migration | Products | Non-empty barcode values |
| --- | ---: | ---: |
| 0012_product_catalogue | 8 | 0 |

This is a point-in-time observation, not a claim that every preview/shared database contains no identifiers. No customer details or credentials were retrieved. No production writes, resets, migrations or deletions were performed.

## Data and migration decision

Keep the nullable legacy column, mapping and existing constraint for now, even though this production snapshot contained no non-empty values. Preserve both catalogue indexes and SKU uniqueness. No forward migration is needed because no schema change is being made. Do not downgrade, delete or rewrite 0012. A future physical column removal requires separate approval and a reviewed forward migration; keeping the column protects other shared environments and any values added since this check.

Product creation, editing, Quick Add and imports ignore submitted retired identifier fields. Edits never overwrite retained values. Lookup responses do not expose them and search does not query them. Old CSV/XLSX `Barcode` headers are accepted case-insensitively and ignored; values are not validated as identifiers, shown in preview, saved, checked for duplicate barcodes, or copied into SKU. Shared file/formula security rules remain. The old scanner route/assets are absent; old unsupported lookup query parameters return HTTP 400.

## Features preserved

- Three entry methods: Add a Product, Quick Add Products, Import Spreadsheet.
- Save Product and Save & Add Another; opening stock and movement records.
- Quick Add capacity of 100, row controls, validation and atomic save.
- CSV/XLSX capacity of 1,000 physical rows, limits, preview/confirm/replay safeguards, SKU validation and atomic import.
- Business-scoped name/SKU/category search, filters, sorts and 20-product pagination; 5,000-product regression remains.
- Multi-item sales with transaction-time selling price and cost snapshots, inventory reduction, movements, overselling protection and rollback.
- Bulk restocking with historical receipt cost, inventory increase, movements, locks and rollback.
- Product archive/restore, stock adjustment/history, expenses and dashboard/report calculations.
- Mobile navigation close/backdrop/Escape/focus behavior and responsive catalogue/baskets.
- Authentication, verification, branded emails, Paystack, ₦3,000 access, Admin Portal and customer-reset safeguards.

No shared Python dependency was removed. `openpyxl` and `defusedxml` still support spreadsheet import.

## Review/test/release

Full test and file results are recorded below after verification. The removal is included in the `feat/import-products` review branch. Publication as a review PR/test preview was authorized after the implementation reports. Production merge/deployment remains pending review. Do not merge PR #23 or #24 as part of this release.

Once approved: publish the reviewed removal branch, review/test the preview, then obtain merge approval. Production remains at migration 0012; no manual migration or database reset is required. The existing production build's upgrade command will have no new migrations. See `PRODUCT_CATALOGUE.md` for exact local commands and environment precautions.

Manual checks after an approved preview is available:

1. On mobile, confirm the three product-entry options and absence of scanner/camera controls.
2. Create one product, use Save & Add Another, and edit details without changing current stock.
3. Quick Add several products; add/remove rows and trigger a validation error.
4. Download the template; import CSV/XLSX with SKU, review and confirm. Try an older template with the ignored optional column.
5. Search by name/SKU/category; filter in/low/out/archived, sort and page.
6. Record one multi-product sale and one bulk restock in a dedicated test business; verify totals, stock and history.
7. Confirm dashboard/reports and archive/restore still behave normally.

## Verification results

- Full Python suite: **124 passed**, 13,729 existing deprecation warnings, 47.92 seconds; no failures.
- Catalogue DOM workflow suite: passed (Quick Add, searchable sale/restock baskets, totals, duplicate selection, preview paging, mobile cards).
- Mobile navigation DOM suite: passed (dismissal, focus containment, desktop transition).
- Syntax checks: basket.js, quick-add.js and import-preview.js passed.
- `git diff --check`: passed.
- The migration regression preserves existing product stock, admin credentials/security version, successful payment record, retained nullable column/constraint and both useful catalogue indexes.
- The 5,000-product and 1,000-row import regressions remain and passed; Quick Add still accepts 100 and rejects 101 without partial changes.
- Protected-path diff check: no changes to migrations, auth, payments, admin, email service, main/dashboard/reports, expenses, sale/restock routes, config, requirements, build/reset scripts or customer-reset tests.
- No browser/camera test is required for the removed feature. Physical mobile layout checks remain a manual review step; DOM checks are not an on-device visual test.

## Exact file changes

Modified:

- `app/models.py`
- `app/products/catalogue.py`
- `app/products/routes.py`
- `app/static/css/catalogue.css`
- `app/static/js/basket.js`
- `app/templates/base.html`
- `app/templates/products/basket.html`
- `app/templates/products/detail.html`
- `app/templates/products/entry_choices.html`
- `app/templates/products/form.html`
- `app/templates/products/import.html`
- `app/templates/products/import_preview.html`
- `app/templates/products/index.html`
- `app/templates/products/quick_add.html`
- `app/templates/restocking/index.html`
- `app/templates/sales/index.html`
- `docs/PRODUCT_CATALOGUE.md`
- `tests/catalogue_dom.cjs`
- `tests/mobile_ui.cjs`
- `tests/render_catalogue_dom.py`
- `tests/test_catalogue.py`

Added:

- `docs/BARCODE_REMOVAL.md` (this inspection/data-safety/review report).

Removed:

- `app/static/js/barcode.js`
- `app/static/js/vendor/README.md`
- `app/static/js/vendor/ZXING-BROWSER-LICENSE.txt`
- `app/static/js/vendor/ZXING-LIBRARY-LICENSE.txt`
- `app/static/js/vendor/zxing-browser-0.2.1.min.js`
- `app/templates/products/scan.html`
- `tests/barcode_photo.cjs`

## Publishing commands — only after review approval

This branch contains a local commit; it is not yet on GitHub. From the repository checkout containing that commit:

```sh
git switch chore/remove-barcode
git push -u origin chore/remove-barcode
```

That push may automatically create a Vercel preview. Open a PR targeting main; review the diff and run the listed manual checks using an appropriately isolated preview database. Merge only after explicit approval. Vercel then deploys main through the existing Git integration. Verify Ready status, expected commit and supported business pages. No extra production migration command is required for this removal, and there is no new revision beyond `0012_product_catalogue`.

If using the supplied local patch in a separate checkout, first ensure main is current and clean, create a review branch from main, then apply it with `git am /absolute/path/to/StockBridge-remove-barcode.patch`. Resolve/review any conflicts rather than forcing application. Do not publish, merge or deploy before approval.
