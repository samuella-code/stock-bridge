# StockBridge product catalogue

The existing Flask/SQLAlchemy catalogue reuses Product, Sale/SaleItem, Restock (batch_id), StockMovement and reports. This update removes barcode functionality without changing authentication, email, payments, lifetime access, admin, reset safeguards, expenses, dashboard or reports.

## Three product-entry methods

- **Add a Product:** one product with name, SKU, category, unit, opening quantity, cost/selling prices, low-stock threshold, supplier, description and existing restocking settings. Save Product opens details; Save & Add Another opens a fresh form. Editing does not overwrite current stock.
- **Quick Add Products:** 1–100 products per batch, including SKU, category, unit and stock threshold. Add/remove unsaved rows, reuse category/unit, retain input after validation errors, and save every product and opening movement together.
- **Import Products:** CSV/XLSX, pasted names/structured lists and UTF-8 TXT through the same editable signed review; up to 1,000 physical rows per batch; 2 MB uploaded/10 MB expanded XLSX; one worksheet. Product Name is required. Blank quantities/prices/threshold default to zero; Unit defaults to `unit`. Upload/paste → validate → edit review → Import Products. Formula-like values, macros, external workbook links, malformed/oversized files and unknown/duplicate headers remain rejected. Format SKU as Text to retain leading zeros.

Old templates containing a single optional `Barcode` column remain compatible: the column is accepted case-insensitively and discarded before preview, duplicate checks and saving. Its values never become SKU values. Ordinary shared workbook security checks still apply, including formula rejection. The downloadable template omits this column. Existing products are never overwritten by imports. SKU duplicates, including archived products, are rejected within each business; SKU remains uppercase. Identical product rows are rejected even without a SKU.

Import previews remain signed with the existing secret, bound to user/business, expire after 20 minutes and are capped at 450 KB. Confirmation revalidates under a business write lock and commits products, opening movements and the audit receipt together. Reusing the same confirmed preview cannot duplicate products. Previously issued, valid previews containing the retired identifier field are safely normalized without writing that field. Names are not unique: conservative current-business name/SKU suggestions help review repeats; Use Existing skips without overwriting. See `docs/IMPORT_PRODUCTS.md` for syntax, row actions and tests.

## Sales and restocking

Search products by name, SKU or category. The debounced, business-scoped lookup returns at most 15 active products; its only supported query parameter is `q`. Old identifier-only query parameters are rejected with HTTP 400 rather than returning unrelated products. The retired scanner page returns HTTP 404.

Sales and restocks keep their multi-product baskets, availability display, quantity editing, editable transaction price/cost, line/overall totals, duplicate-selection handling and up to 100 products per operation. Server validation, tenant isolation, conditional inventory updates, product locks in ID order and atomic commits/rollbacks are unchanged.

Historical SaleItems preserve selling price and cost snapshots. Restocks preserve each receipt's purchase cost. Current inventory valuation uses latest received unit cost; operating expenses are separate from inventory purchases. Stock adjustments require a reason and create movement history. Products can be archived after remaining stock has been resolved through recorded activity; historical records remain and products can be restored.

## Catalogue and mobile use

Products paginate at 20/page; search by name, SKU, category or supplier. Category, in/low/out/archived filters and all existing sorts remain. Filters persist across pages. Restocking insights paginate 20 products with bounded sales aggregation; histories remain paginated. Mobile cards, labelled tables, touch controls and the dismissible navigation drawer remain.

No scanner button, camera dialog, camera permission request, scan-next action, barcode field, barcode search or decoder is shipped. No external product recognition provider is enabled.

## Schema safety

Migration `0012_product_catalogue` has already reached production. It is unchanged: never downgrade or delete it to remove this feature. It contains useful business/active/name and business/active/category indexes, which remain intact.

The nullable legacy `product.barcode` column, its mapping and business-scoped constraint remain temporarily. Business workflows neither read nor write it. Removing a field from the product form must not erase a stored value during an edit. There is no new migration, column drop, database reset or data cleanup in this update. Future permanent removal requires a separate reviewed forward migration and a fresh count/export/backup plan.

## Verification

```sh
python -m pytest -q --disable-warnings
PYTHONPATH=. python tests/render_catalogue_dom.py /tmp/stockbridge-no-barcode-dom
NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/catalogue_dom.cjs /tmp/stockbridge-no-barcode-dom
NODE_PATH=/tmp/stockbridge-dom-tests/node_modules node tests/mobile_ui.cjs /tmp/stockbridge-no-barcode-dom
node --check app/static/js/basket.js
node --check app/static/js/quick-add.js
node --check app/static/js/import-preview.js
```

DOM checks require the development-only `jsdom@26.1.0` dependency; no barcode decoder/pixel-generation dependency remains. They cover Quick Add reuse/removal, searchable sale/restock baskets, totals, duplicate selection, preview paging, labelled mobile cards, mobile menu dismissal/focus containment and desktop transition.

The Python suite covers product/opening stock, SKU protection, Quick Add validation, CSV/XLSX imports including legacy-column compatibility, signed preview safety/replay, CSRF/XSS, 1,000-row import, 5,000-product pagination, name/SKU/category search, stock filters, business isolation, three-item sales, overselling, bulk restocking, historical prices/costs, stock movements, adjustments, dashboard/report agreement and injected mid-operation rollback. Tests also verify absence of scanner UI/routes/assets, retained legacy data and preserved catalogue indexes. All existing payment/admin/security/email/reset tests remain in the full suite.

## Review and eventual deployment

Do not merge or deploy before reviewing these changes and the keep-column strategy. The implementation remains local; pushing a branch to the connected repository can automatically create a Vercel preview, so publishing also waits for review.

No production migration is needed: production is already at `0012_product_catalogue`. Do not downgrade. After approval, publish the reviewed branch, review its diff and test the preview, then merge only with approval. The existing guarded Vercel production build runs `flask db upgrade`, which has no new revisions to apply. Preview builds do not run migrations. Preview environments must use an appropriate dedicated database; do not run a preview migration against production.

For disposable local testing after publishing the reviewed branch:

```sh
git switch feat/import-products
python -m pip install -r requirements.txt
python -m flask --app app:create_app db upgrade
python -m flask --app app:create_app db current
python -m flask --app app:create_app run
```

Expected migration: `0012_product_catalogue (head)`. Configure the existing environment precedence for a disposable local database before these commands. Never print/share/commit a database URL. There are no new dependencies or keys. Follow the detailed review record in `docs/BARCODE_REMOVAL.md`.
