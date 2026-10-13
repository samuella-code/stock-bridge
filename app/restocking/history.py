"""Read-only receipt pagination using stored batch IDs, never dates or suppliers."""
from decimal import Decimal
from sqlalchemy import String, case, cast, func, and_
from sqlalchemy.orm import joinedload
from app.models import Restock


def receipt_history(business_id, page):
    key = case((and_(Restock.batch_id.isnot(None), func.trim(Restock.batch_id) != ''),
                'batch:' + Restock.batch_id), else_='row:' + cast(Restock.id, String))
    pages = (Restock.query.with_entities(key.label('receipt_key'),
        func.max(Restock.received_at).label('date'), func.max(Restock.id).label('last_id'))
        .filter(Restock.business_id == business_id).group_by(key)
        .order_by(func.max(Restock.received_at).desc(), func.max(Restock.id).desc())
        .paginate(page=page, per_page=20, error_out=False))
    keys = [row.receipt_key for row in pages.items]
    records = (Restock.query.options(joinedload(Restock.product))
        .filter(Restock.business_id == business_id, key.in_(keys))
        .order_by(Restock.id).all()) if keys else []
    grouped = {k:[] for k in keys}
    for record in records:
        grouped['batch:' + record.batch_id if record.batch_id and record.batch_id.strip(' ') else 'row:' + str(record.id)].append(record)
    receipts = []
    for row in pages.items:
        items = grouped[row.receipt_key]
        receipts.append({'reference':row.receipt_key, 'received_at':row.date, 'items':items,
            'item_count':len(items), 'total':sum((r.quantity*r.unit_cost for r in items), Decimal('0'))})
    pages.items = receipts
    return pages
