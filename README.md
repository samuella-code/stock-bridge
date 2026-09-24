# StockBridge

**Know what will sell. Restock before it’s too late.**

StockBridge is a beginner-friendly B2B SaaS MVP for Nigerian small retailers. It helps business owners record products, sales and expenses, understand their daily performance, detect low stock and receive simple restocking recommendations based on their own sales history.

## MVP goals

- Secure sign up, login and logout
- Business account setup
- Product and inventory management
- Sales recording with automatic stock reduction
- Expense tracking
- Dashboard for sales, expenses, gross profit, low stock and recent activity
- Simple restocking recommendations using average daily sales, lead time and safety stock
- Responsive, mobile-first UI for phones, tablets and laptops

## Technology

- Python 3.12+
- Flask
- Flask-SQLAlchemy
- Flask-Login
- Werkzeug password hashing
- SQLite for local MVP development
- Jinja templates
- HTML, CSS and minimal JavaScript
- Pytest for tests
- Managed PostgreSQL for production deployments

## Project structure

```text
stock-bridge/
├── app/
│   ├── __init__.py
│   ├── models.py
│   ├── auth/
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── main/
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── products/
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── sales/
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── expenses/
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   └── auth/
│   │       ├── login.html
│   │       └── signup.html
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── instance/
│   └── .gitkeep
├── tests/
│   └── __init__.py
├── .env.example
├── .gitignore
├── config.py
├── requirements.txt
└── run.py
```

## Four-week MVP roadmap

### Week 1 — Foundation and validation
- Interview at least 5–10 retailers.
- Confirm their current inventory workflow and biggest pain points.
- Set up Flask, database, authentication and the first dashboard shell.
- Agree on UI conventions and Git workflow.

### Week 2 — Inventory workflow
- Add products.
- Edit and delete products.
- Track stock, buying price, selling price, minimum level and supplier details.
- Add low-stock states.

### Week 3 — Money movement
- Record sales.
- Automatically reduce stock.
- Track expenses.
- Add daily sales, expenses and estimated gross-profit dashboard metrics.
- Add recent transaction history.

### Week 4 — Restocking and pilot
- Add average-daily-sales calculations.
- Add configurable lead time and safety stock.
- Calculate reorder point, days until stockout, reorder quantity and priority.
- Test critical flows.
- Deploy a pilot build and onboard initial retailers.

## Team role split

- **Backend:** Flask routes, authentication, business rules, restocking logic.
- **Frontend:** Jinja templates, forms, responsive dashboard UI and interactions.
- **Database:** SQLAlchemy models, migrations/schema decisions and data integrity.
- **UI/UX:** user flows, mobile layouts, empty states, form clarity and accessibility.
- **Testing & docs:** test cases, README, setup instructions and bug reproduction notes.
- **Business research:** retailer interviews, pricing feedback, competitor notes and pilot onboarding.

One person may hold more than one role while the team is small.

## Git workflow for beginners

Create one branch per task:

```bash
git checkout main
git pull
git checkout -b feature/authentication
```

Commit small, understandable changes:

```bash
git add .
git commit -m "Add user signup and login"
git push -u origin feature/authentication
```

Then open a Pull Request into `main`, let another teammate review it, fix comments, and merge only when the feature has been tested.

## First practical task

Today, do these three things:

1. Interview at least 3 real retailers and ask how they currently record sales, expenses and restocking decisions.
2. Clone this repository and run the starter Flask app locally.
3. Open the starter dashboard and review the UI together before adding product CRUD.

## Business direction

### Core customer

The first customer should be a small owner-operated retailer with roughly 20–500 SKUs who currently uses paper, memory, WhatsApp or spreadsheets and wants a simpler way to know what sold, what is low, and what to buy next.

Good early pilot categories include mini-marts, beauty stores, fashion/accessory stores, pharmacies where appropriate compliance is handled separately, and neighborhood convenience retailers.

### USP

StockBridge should not try to become a full enterprise ERP in the MVP. Its early promise is:

> **A simple retail control center that turns everyday sales and stock records into clear restocking decisions.**

### Revenue model

The first paid offer is permanent access for one business workspace after a single payment. The current ₦3,000 launch price is configurable and should be treated as an early-adopter offer while real retailers confirm willingness to pay. Future paid add-ons can cover extra branches, advanced reporting, onboarding or supplier services without taking away the lifetime features already purchased.

Future revenue opportunities may include higher subscription tiers, multi-branch support, supplier tools, procurement commissions, and integrations with licensed financial-service partners.

## Paystack payment setup

StockBridge creates Paystack transactions on the server and only grants lifetime access after verifying the reference, amount, currency and signed payment event.

1. Copy `.env.example` to `.env` and add your Paystack **test** secret/public keys. Never commit real keys.
2. Set `LIFETIME_PRICE_NAIRA` to the launch price (the starter value is `3000`).
3. In the Paystack test dashboard, set the webhook URL to `https://your-domain.example/payments/webhook`.
4. Run `flask --app app:create_app db upgrade` before starting the updated app.
5. Complete a Paystack test payment and confirm the business changes to `lifetime` / `active`.

Only switch to live Paystack keys after testing the full callback and webhook flow on HTTPS.

## Vercel deployment

The repository includes a Vercel-compatible Flask entry point. Production must
use managed PostgreSQL rather than SQLite. Follow
[`docs/VERCEL_DEPLOYMENT.md`](docs/VERCEL_DEPLOYMENT.md) for the database,
environment variables, migrations, Paystack webhook and verification steps.

### Investor-relevant metrics later

Track activation rate, weekly active businesses, sales transactions recorded per business, product records per business, 4/8/12-week retention, conversion from pilot to paid, monthly recurring revenue, churn, customer acquisition cost, gross margin and evidence that StockBridge reduces stockouts or improves purchasing decisions.

## Security baseline

- Never store plain-text passwords.
- Keep Flask secret keys outside Git.
- Scope all retailer data by business/user ownership.
- Validate quantities and monetary values server-side.
- Prevent users from selling more stock than is available unless backorders are deliberately supported later.
- Do not expose one business's data to another business.

## Next build target

The next implementation milestone after this scaffold is **authentication + business onboarding**, followed by **product CRUD**.

## Business activity and report calculations

Products begin with an opening quantity, which is recorded as an opening stock
movement. Subsequent quantity changes are recorded as sales, restock receipts,
or reasoned adjustments. Voiding a sale keeps its items and creates reversing
stock movements. Archiving a product requires zero remaining stock and keeps
its historical sales and receipts.

A customer checkout has one `Sale` and one `SaleItem` per product. Each item
saves its selling price and the product's latest known unit cost at checkout.
This is a **transaction-time cost snapshot** for estimated cost of goods sold,
not FIFO or weighted-average costing. A restock saves its own purchase unit cost
and changes the product's current unit cost for future sales. Historical sale
profit never changes when a later restock has a different cost. Older one-item
sales are converted to one-item transactions by migration 0007. Previously
manual stock edits are reconciled into the opening balance during migration.

- Revenue = sum of completed sale item quantities × saved selling prices.
- Estimated cost of goods sold = sum of completed sale item quantities × saved costs.
- Estimated gross profit = revenue − cost of goods sold.
- Estimated net profit = gross profit − recorded operating expenses.
- Inventory purchased = restock quantities × saved receipt costs, shown
  separately and **not subtracted again** from estimated net profit.
- Current estimated inventory value = active product quantity × latest unit cost.

Dashboard and report date filters use the stored UTC transaction timestamps.
Current stock and inventory value reflect the present inventory regardless of
the selected reporting period. Restock suggestions are estimates based on
sales in the past 30 days; no sales means no velocity estimate.

Before applying migration 0007 to a database with existing records, back up
that database, deploy the matching code and run `flask db upgrade`. The
migration preserves existing users, businesses, products, sales and expenses.
It has no automatic downgrade because a multi-item checkout and reasoned stock
adjustments cannot be expressed safely in the older schema.
