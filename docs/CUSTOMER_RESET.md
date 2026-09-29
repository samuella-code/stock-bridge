# Customer data reset before launch

The one-time `scripts/reset_customer_data.py` command removes ordinary customer
accounts and their business records. It preserves dedicated administrators
(`role=admin`) and dual-role administrators (`admin_enabled=true`) with their
business data. Customer-related audit entries and password reset tokens are
removed; independent admin logs and login attempts remain.

Payment cleanup follows ownership first, then case-insensitive email matching:

1. Payments linked to an ordinary customer's business are removed, whether
   successful, pending, failed, or initialized.
2. Payments linked to an admin-owned business are preserved, even if their
   `customer_email` happens to match an ordinary customer.
3. Unlinked payments (`business_id IS NULL`) matching an ordinary customer's
   email are removed. Unlinked payments matching an admin's email are preserved.
4. All other unlinked payments are treated as obsolete pre-launch records and
   removed, including successful and pending records. **This also removes an
   unclaimed real payment if it has no business and no admin email match.**
   Review the dry-run counts and payment records before choosing to execute.

The dry run reports all five groups and the total unlinked count. If a customer
and admin have the same email ignoring case, or a customer-owned business has a
payment with an admin's email, the command stops for manual review. It also
stops on a payment pointing at a missing business. Admin-owned business
payments cannot be deleted by an email match alone.

This script never runs during startup or deployment. It does not change tables,
migrations, access rules, Paystack settings, or the ₦3,000 lifetime price.

1. Make a verified backup of the **production PostgreSQL database** with your
   database provider. `scripts/backup.py` only backs up a local SQLite file.
2. Stop new customer signups and payments for the maintenance window.
3. From the repository root in a trusted terminal, activate the virtual
   environment and set `DATABASE_URL` to the intended production database URL
   without pasting it into chat or committing it. If `NEON_DATABASE_URL` or
   `NEON_POSTGRES_URL` is set, those take precedence; make sure they refer to
   the same database.
4. Check the target database and record counts with the default dry run:

   ```bash
   python -m scripts.reset_customer_data
   ```

5. Only after reviewing the target, counts, and backup, execute:

   ```bash
   python -m scripts.reset_customer_data --execute
   ```

   Type `RESET CUSTOMER DATA` at the prompt. The command recomputes the counts
   immediately before deletion and commits all deletes in one transaction. An
   error rolls the transaction back.

6. Confirm the customer count is zero, admin accounts still sign in, and new
   customer signup works. Resume customer traffic.

The script refuses a missing database URL, incomplete schema, unexpected user
roles, ambiguous admin/customer payment ownership or email identity,
cross-business transaction links, or an unknown table with a foreign key to
deleted records. Investigate those cases before trying again. Protected admin
payments can still appear in platform payment and revenue counts after reset.
