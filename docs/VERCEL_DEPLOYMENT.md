# Deploy StockBridge on Vercel

StockBridge can run on Vercel as a Flask function, but production data must use
managed PostgreSQL. Do not use SQLite in production because Vercel function
storage is temporary.

## 1. Create the production database

Create a free PostgreSQL project with Neon and copy its **pooled** connection
string. Keep it private. It normally begins with `postgresql://`.

## 2. Import the GitHub repository into Vercel

1. In Vercel, choose **Add New > Project**.
2. Import `samuella-code/stock-bridge`.
3. Select the branch containing these Vercel files (or merge it into `main`).
4. Leave the root directory as the repository root.

Vercel detects `wsgi.py` as the Python/Flask entry point.

## 3. Add production environment variables

In **Project Settings > Environment Variables**, add these variables for
Production (and Preview only when you deliberately want preview deployments to
use a database):

| Variable | Production value |
|---|---|
| `SECRET_KEY` | A new random value of at least 32 bytes |
| `DATABASE_URL` | The private pooled PostgreSQL URL from Neon |
| `FLASK_ENV` | `production` |
| `PAYSTACK_SECRET_KEY` | Paystack secret key |
| `PAYSTACK_PUBLIC_KEY` | Matching Paystack public key |
| `LIFETIME_PRICE_NAIRA` | `3000` |
| `ADMIN_EMAILS` | Legacy setting; no longer grants Admin Portal access |
| `SMTP_HOST` | SMTP server, for example `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | SMTP account username |
| `SMTP_PASSWORD` | SMTP app password, not the normal mailbox password |
| `SMTP_FROM_EMAIL` | Verified sender address |
| `SMTP_USE_TLS` | `true` |

Never commit or paste real secret values into GitHub, documentation, or chat.

Generate a Flask secret locally with:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 4. Apply database migrations

From a trusted local clone with the virtual environment active, securely read
the production database URL and apply the existing migrations:

```bash
read -s -p "Neon DATABASE_URL: " DATABASE_URL
echo
export DATABASE_URL
flask --app app:create_app db upgrade
unset DATABASE_URL
```

Do this once before accepting signups, and repeat `db upgrade` whenever a future
deployment includes a new migration. Do not run migrations automatically on
every serverless request.

## 5. Deploy and connect Paystack

Deploy the project in Vercel. Once the production domain exists, configure this
Paystack webhook URL in the Paystack dashboard:

```text
https://YOUR-VERCEL-DOMAIN/payments/webhook
```

Paystack must send a signed `charge.success` webhook before StockBridge grants
lifetime access.

## 6. Verify the deployment

Check these routes:

```text
https://YOUR-VERCEL-DOMAIN/health
https://YOUR-VERCEL-DOMAIN/auth/signup
https://YOUR-VERCEL-DOMAIN/auth/login
```

Then test the complete flow with Paystack test keys: signup, email verification,
login, payment gate, test payment, webhook activation, product creation, sale,
expense, restocking recommendation, and owner dashboard access.

## Important production notes

- Localhost, PythonAnywhere, and Vercel use separate databases unless explicitly
  configured to share one PostgreSQL database.
- Preview deployments should not share the production database unless that is a
  deliberate decision.
- Vercel runtime logs are temporary operational logs; configure an external log
  drain or monitoring service before relying on them for long-term auditing.
- Keep automated PostgreSQL backups enabled in the database provider.

## Admin Portal setup

After the production schema reaches migration 0010, run
`python scripts/create_admin.py` from a trusted local clone connected to the
production database. Enter a dedicated administrator email and a strong
password interactively. To grant an existing account access, use
`python scripts/grant_admin.py` from the same trusted environment. Visit
`/admin/login`. Admin recovery emails use the existing SMTP settings. Do not
commit credentials. The production database is separate from your local SQLite database.
