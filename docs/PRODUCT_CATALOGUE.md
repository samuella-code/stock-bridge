# StockBridge catalogue update

This extends the existing Flask products, Sale/SaleItem, Restock (batch_id), StockMovement and reporting flows. No payment, access, authentication, admin, reset or email implementation is changed. Existing quantities and historical transactions are retained.

## Product entry

- **Add One Product** retains opening stock, cost/selling prices, category, unit, SKU, description and settings. Optional Barcode is new. Save Product opens details; Save & Add Another opens a fresh form; Save & Scan Next returns to barcode lookup.
- **Quick Add Products** accepts 1–100 products together, reuses the last category/unit in new rows, supports removal before submission, and preserves fields after validation errors. Opening stock and its movement are recorded even for zero quantity.
- **Import Spreadsheet** downloads a CSV template that can be opened in Excel. CSV and XLSX are supported. Only Product Name is required. Blank prices, opening quantity and threshold default to zero; Unit defaults to `unit`. Upload → validate → review → Confirm Import. Invalid batches cannot be confirmed. Up to 1,000 rows, 2 MB compressed upload, 10 MB expanded XLSX, one worksheet, no macros/external links/formulas. Larger catalogues use multiple batches. Header names match the template; unknown/duplicate headers are rejected. SKU/barcode cells in Excel must be Text to preserve leading zeros. Blank lines are skipped, but count towards the physical row limit; error numbers match the spreadsheet's physical rows.
- Previews are signed with the existing application secret, expire after 20 minutes, and bind to both user and business. They are not stored in cookies or the serverless filesystem. Preview tokens over 450 KB are rejected. Confirmation revalidates identifiers under a business write lock and commits all products, movements and an audit receipt together. Reusing a confirmed preview does not add products twice. Re-uploading a file makes a new preview: populated SKU/barcode duplicates are rejected; names alone are not unique, so review files without identifiers carefully.
- Duplicate SKU/barcode checks include archived products. SKU remains uppercase. Barcode matching is exact and case-sensitive. Products without either identifier continue to work. Both identifiers are unique per business, not globally.

## Barcode and transaction baskets

Scanning uses the native BarcodeDetector API for supported EAN/UPC/Code128/Code39/ITF formats. No paid service or external product catalogue is called. HTTPS and compatible hardware/browser support are required. Permission is requested only after a user taps Scan Barcode; streams stop on close, cancel, detection, failure or page exit. Unsupported browsers and denied permissions have manual entry. USB/Bluetooth scanners that type into a field also work.

Product barcode lookup opens an existing active or archived product; unknown codes offer Add New Product with the barcode preserved. Archived matches can be restored, rather than duplicated. Sales/restocks only select active products. Unknown transaction barcodes direct users to add the product in Products first. Scanning provides an identifier, not a product name or Nigerian prices.

Sales and restocks use a debounced server search (name, SKU, barcode, category) with at most 15 results. A basket shows availability, quantity, editable unit price/cost, line totals and overall total. Re-selecting a product focuses its existing quantity instead of creating a duplicate line. Each transaction supports up to 100 products. Existing multi-item Sale and batch_id Restock storage are reused. Server validation remains authoritative; sale conditional updates prevent overselling. Postgres locks products in ID order to coordinate sales/restocks and retain the cost snapshot appropriate to that transaction. All records and movements commit together or roll back together.

Latest received purchase cost remains the inventory valuation basis; sales retain their own historical unit cost and price. Restocks preserve each receipt's cost. Inventory purchases remain separate from operating expenses. Adjustments continue to require a reason and create history; current quantity cannot be changed through Edit Product. Details now show barcode, SKU, movement time and sale/receipt references. No unreliable running-balance column was added.

## Catalogue navigation and mobile layout

Products remain database-paginated at 20/page. Search adds barcode; existing category, in/low/out/archived filtering remains, and the archived-filter bug is corrected. Sorts add cost, inventory value, oldest and low-stock priority. Search/filter/sort parameters persist through product pagination. Low-stock product rows link directly to restocking with that product selected.

Restocking insights paginate 20 products, aggregate sales only for those products, and support search. Histories remain paginated. Catalogue cards, Quick Add forms, transaction baskets and labelled history cards have narrow-screen styles and touch targets. The four product-entry choices remain visible for new users.

## Migration and indexes

`0012_product_catalogue` follows `0011_payment_receipt_email`. It adds nullable `product.barcode`, business/barcode uniqueness, and composite business/active/name and business/active/category indexes. Existing SKU uniqueness is reused. The additive upgrade leaves existing records and quantities unchanged; SQLite uses the project's batch migration pattern. Downgrade removes the new barcode field and therefore loses newly entered barcodes: prefer reverting application code while retaining the additive schema.

Exact barcode lookup uses the unique composite index. The two non-unique indexes match active-catalogue ordering and category queries. Substring name/SKU/barcode searches do not become index seeks merely because a B-tree exists; at 5,000 products they remain bounded, tenant-scoped scans with small responses. No unmeasured trigram extension or global full-text index is introduced. Pagination/counts and SQL aggregates prevent loading lifetime transactions or all products into the browser. Bulk creation uses one product flush plus movement insertion, instead of flushing each product separately. Production latency and simultaneous writers on Neon still need measurement; local tests are not a production benchmark.

## Verification

Python suite:

```sh
python -m pytest -q --disable-warnings
```

JavaScript syntax:

```sh
node --check app/static/js/barcode.js
node --check app/static/js/basket.js
node --check app/static/js/quick-add.js
node --check app/static/js/import-preview.js
```

DOM workflow checks against real rendered templates (optional development dependency only):

```sh
npm install --prefix /tmp/stockbridge-dom-tests jsdom@26.1.0
PYTHONPATH=. python tests/render_catalogue_dom.py /tmp/stockbridge-dom-html
NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/catalogue_dom.cjs /tmp/stockbridge-dom-html
```

Coverage includes entry with zero/nonzero stock, Save & Add Another, Quick Add, CSV/XLSX preview/confirmation, duplicate identifiers, physical error rows, malformed/oversized files, formula/XSS protection, token tampering/expiry/replay, CSRF, a 1,000-row import, 5,000-product pagination/bounded selectors, active/archived lookup, business isolation, price bounds, historical cost preservation, stock histories and dashboard/report agreement. Injected failure on the second insert verifies rollback for sales, restocks, Quick Add and imports. Existing payment/admin/security/email/reset tests remain in the full suite.

The supermarket test adds Coca-Cola 100, Fanta 80, Bread 30 and Indomie 200, adds an unknown barcode product, records one three-item sale, bulk-restocks three products, records damage and an expense, and verifies stock/movements/revenue/cost/profit/report consistency.

DOM checks exercise search-to-basket, totals, duplicate scan selection, manual scanner fallback, unknown-code product creation link, Quick Add reuse/removal, preview paging and labelled mobile cards. They simulate dialog/browser APIs; they are not real camera or visual viewport tests. Real mobile visual and camera verification remains required because browser installation in this workspace failed certificate validation and an alternate download was truncated.

## Review and deployment

Do not merge until the target database and backup/restore point have been verified. Do not reset the database or customer data. Keep database URLs/secrets out of terminal output, code and chat.

This repository previously ran `flask db upgrade` during every Vercel build, while its preview integration was connected to the production Neon resource. `scripts/vercel_build.py` now runs that existing migration command only when `VERCEL_ENV=production`. Preview/local builds do not migrate any database. A preview requires a **dedicated preview database**, its normal environment variables and an explicit migration there before product routes can work. Do not run a preview migration using a production URL.

For local review, use a disposable local database configured through the project's existing environment precedence, not production credentials:

```sh
git switch feat/scalable-catalogue
python -m pip install -r requirements.txt
python -m flask --app app:create_app db upgrade
python -m flask --app app:create_app db current
python -m flask --app app:create_app run
```

For eventual production release: verify the actual production PostgreSQL target and recoverable backup, install requirements, then run the same migration command in an environment securely configured for that verified target. Expect `0012_product_catalogue (head)`. Merge only after schema verification, or let the guarded production build run the additive migration before serving this code. Review the build logs and public health check. Do not copy a database URL into chat. No new email/payment/barcode API keys are required.

No production migration, customer-data modification, reset or live transaction is performed as part of this implementation. No production deployment or merge is performed. The existing live version remains unchanged pending release.
