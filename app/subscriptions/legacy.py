"""Read-only legacy evidence preflight, also used by the forward migration."""
from sqlalchemy import text

EVIDENCE = """
SELECT b.user_id, b.id AS business_id, MIN(p.id) AS payment_id, MIN(p.paid_at) AS paid_at
FROM business b JOIN "user" u ON u.id=b.user_id JOIN payment p ON p.business_id=b.id
WHERE p.provider='paystack' AND p.product='lifetime' AND p.status='success'
AND p.paid_at IS NOT NULL AND p.amount_kobo=300000 AND p.currency='NGN'
AND (lower(p.customer_email)=lower(u.email) OR
 (p.customer_email = 'legacy-' || p.id || '@stockbridge.local'
 AND b.subscription_plan='lifetime' AND b.subscription_status='active'))
GROUP BY b.user_id,b.id
"""


def preflight(connection):
    rows = connection.execute(text(EVIDENCE)).mappings().all()
    grouped = {}
    for row in rows:
        grouped.setdefault(row['user_id'], []).append(dict(row))
    ambiguous = sum(len(group)>1 for group in grouped.values())
    qualified_ids = {r['business_id'] for r in rows}
    active_ids = {r[0] for r in connection.execute(text("SELECT id FROM business WHERE subscription_plan='lifetime' AND subscription_status='active'"))}
    unproven = len(active_ids-qualified_ids)
    return rows, {'qualified_lifetime_accounts':len(grouped), 'multiple_lifetime_businesses':ambiguous,
                  'active_lifetime_without_payment_evidence':unproven}
