# Import Products implementation and review

Status: implemented on `feat/import-products`, including the earlier barcode-removal work. Publication as a review PR and test preview was authorized after the implementation report. Production merge/deployment remains pending review. No production database access or changes for this task. A Git push can automatically trigger a Vercel preview.

## Existing architecture inspected

The existing `/products/import` GET/POST route in `app/products/routes.py` calls `read_spreadsheet`, `grid_to_rows`, `validate_rows` and `save_batch` from `app/products/catalogue.py`. CSV is UTF-8 and header-mapped. XLSX uses read-only openpyxl with expanded-size, single-sheet, formula, macro and external-link checks. The existing template download remains `/products/import/template`.

Previews use the existing URLSafeTimedSerializer, secret and salt. They carry user ID, business ID, draft rows and a unique nonce, are capped at 450,000 characters and expire after 20 minutes. Nothing is stored at preview time. Confirmation revalidates under a business write lock, creates Products and opening StockMovements, records a PRODUCT_IMPORT audit receipt, and commits once. The receipt prevents repeat confirmation.

This extension reuses those routes, validators, model construction, stock movement creation, transactions and receipts. There is no separate importer, database table, migration, paid service or new dependency.

## Files changed

- `app/products/product_lists.py` (new): shared deterministic paste/TXT parser and business-scoped existing-product suggestions.
- `app/products/routes.py`: source selection, signed editable review, row actions, confirmation validation and results.
- `app/products/catalogue.py`: case-insensitive identical-row detection and atomic receipt for an import with every row skipped.
- `app/templates/products/entry_choices.html`: Import Products label and description; Add a Product and Quick Add retained.
- `app/templates/products/import.html`: three source forms on the existing landing page.
- `app/templates/products/import_preview.html`: editable review cards, row actions, errors and existing-product suggestions.
- `app/static/css/catalogue.css`: responsive review field grid.
- `tests/test_product_lists.py` (new): parser, TXT, review, security, limits, rollback and regression tests.
- `tests/render_catalogue_dom.py` and `tests/catalogue_dom.cjs`: three-method landing page and editable-review paging checks.
- `docs/PRODUCT_CATALOGUE.md` and this document.

No new route or JavaScript/API dependency. `import-preview.js` continues paging 50 cards at a time and preserves edits in the existing DOM. No barcode feature is added.

## Supported inputs

1. Existing CSV/XLSX template imports, unchanged file validation and column mapping.
2. Pasted plain product names, one per non-empty line.
3. Headerless structured rows: `Product Name - Opening Quantity - Cost Price - Selling Price`, or the same four columns separated by commas or tabs. Hyphens must have spaces on both sides; hyphens within names are preserved. Quoted commas are supported by the standard CSV reader.
4. Copied comma/tab tables with recognizable headers: template headings plus Name, Quantity, Opening Qty, Cost and Selling aliases. Headers must be known and unique.
5. UTF-8/BOM `.txt` files using exactly the same parser as pasted text.

No arbitrary alignment spaces, currency-symbol conversion, thousands-separator inference, OCR or AI. If four positional columns cannot be determined confidently, the complete line remains the product name with a review warning; missing business values are not invented. Explicit header-mapped invalid quantities/prices are shown as validation errors. Empty lines are ignored but count toward the 1,000 physical-line limit. Text is limited to 2 MiB; the application's existing total request/form limits also apply and may reject oversized requests earlier with HTTP 413. Binary control characters, invalid UTF-8 and unsupported extensions are rejected.

## Review and duplicates

All nine template fields are editable: name, category, unit, opening quantity, cost, selling price, threshold, SKU and description. Category/unit are free-text inputs, consistent with existing product forms. Only name is required to identify a product. Existing safe defaults apply to blank fields: zero quantity, prices and threshold, and `unit`.

Every row offers Create as New, Remove / skip this row, and—when suggested—Use Existing Product. Identical rows, including case-only differences in names, are blocked until edited or skipped. No quantities are automatically combined. Similar names with differing product details are not automatically rejected. SKU remains uppercase and unique within the business, including archived products, and is checked again at confirmation.

Existing-product suggestions conservatively use exact case-insensitive names or exact normalized SKUs. They do not claim that loosely similar names are the same product. Suggestions include archived products with a label, are bounded, and never search another business. Create as New allows repeated names with a distinct/blank SKU. Use Existing skips the draft without changing any existing product, quantity, price or history. The selected existing ID is independently checked for business ownership on the server.

Success reports products created, rows skipped and existing products selected; counters survive correction/review retries. The result redirects to Products; View Products and Import More Products are also available in review. A no-product confirmation still saves an audit receipt, so its token cannot later be replayed to create products.

## Safety

- Existing login, access guard and CSRF protection remain unchanged.
- Signed draft verifies user and business, bounded original row count, valid row actions, and selected existing-product ownership.
- Browser changes to review fields are intentional; all are revalidated. Editing the signed token itself fails signature verification.
- New drafts contain an original issue timestamp; corrected previews preserve it, so corrections cannot extend the original 20-minute deadline. Existing valid legacy previews remain supported by the original serializer expiry check.
- Current quantity is never imported as an independent field: each new product starts with opening quantity and its recorded opening movement.
- Any critical failure rolls back products, movements and audit receipt. The business lock and receipt check remain inside the save transaction, including concurrent retries.
- Jinja escapes user text, and uploaded content is never executed.
- No changes to auth, email, Paystack/access/price, admin, reset, dashboard/report calculations, expense/sales/profit/restocking logic, schema or migration history.

## Automated verification

Commands (disposable test database only):

```sh
PYTHONPATH=. python -m pytest -q --disable-warnings --tb=short
PYTHONPATH=. python tests/render_catalogue_dom.py /tmp/stockbridge-import-products-dom
NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/catalogue_dom.cjs /tmp/stockbridge-import-products-dom
NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/mobile_ui.cjs /tmp/stockbridge-import-products-dom
git diff --check
```

DOM checks use the existing development-only jsdom installation. Tests cover simple and structured lists, separators, copied headers, TXT, unusual/hyphenated names, ambiguous input, duplicates, existing-product choices, SKU conflicts, defaults, invalid values, 1,000 products, limits, authentication/CSRF, tenant isolation, signature tampering, expiry after correction, replay after edits/all-skipped imports, rollback, opening history and editable CSV/XLSX review. The full suite includes unchanged payment/admin/email/reset/business workflow tests.

Final verification: **167 Python tests passed** in 60.74 seconds. Catalogue/review DOM checks and mobile-menu DOM checks passed; `git diff --check` passed. The suite emits existing deprecated UTC timestamp warnings. No physical phone test was performed.

## Manual phone checks after approval to publish a test preview

1. On Android and iPhone, open Products → Import Products. Confirm the three methods, and that Add a Product and Quick Add remain available.
2. Copy five names from Notes/WhatsApp, paste, review, change a price/category/unit, skip one row, and import. Confirm four new products and their opening history.
3. Paste `Coca Cola 50cl - 24 - 350 - 500`; confirm values before importing. Try a name with internal hyphens.
4. Paste a copied tab-separated table including Product Name, Quantity, Cost Price and Selling Price headers.
5. Upload a UTF-8 TXT note, CSV and XLSX. Confirm each uses the same editable review.
6. Try an existing product's name/SKU; check Use Existing skips it without changing stock. Try Create as New with a distinct SKU.
7. Correct a negative quantity or duplicate SKU; ensure no inventory is added until the complete confirmation succeeds.
8. Use over 50 rows, edit on the first page, move forward/back and confirm edits remain. Check fields/cards fit the screen without horizontal scrolling.
9. Refresh/re-submit a successful confirmation; confirm no duplicate products or opening movements. Cancel review and confirm nothing was saved.

No physical-phone camera test is relevant; this importer has no camera function. DOM checks do not replace the manual Android/iPhone checks above. No merge or deployment is authorized by this implementation task.
