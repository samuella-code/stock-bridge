# Customer data reset before launch

The one-time `scripts/reset_customer_data.py` command removes ordinary customer
accounts and their business records. It preserves dedicated administrators
(`role=admin`) and dual-role administrators (`admin_enabled=true`) with their
business data. Customer-related audit entries and password reset tokens are
removed; independent admin logs and login attempts remain. Payments are removed
when their business or customer email belongs to a deleted customer. Unlinked
payments with no matching customer account remain for manual review.

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
roles, cross-business transaction links, or an unknown table with a foreign key
to deleted records. Investigate those cases before trying again.
