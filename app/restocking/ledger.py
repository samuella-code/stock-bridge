"""Shared atomic restock ledger. Caller owns authority, locks and transaction."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import update
from app import db
from app.models import Product, Restock, StockMovement
from app.admin.routes import log


class StockChanged(ValueError):
    pass


def receive_stock(b, parsed, products, *, supplier, note, actor):
    batch = str(uuid4())
    for pid, qty, cost in sorted(parsed):
        receipt = Restock(business_id=b.id, product_id=pid, quantity=qty,
            unit_cost=cost, supplier=supplier, note=note, batch_id=batch)
        db.session.add(receipt)
        db.session.flush()
        result = db.session.execute(update(Product).where(Product.id == pid, Product.business_id == b.id,
            Product.active.is_(True), Product.stock_quantity <= 2147483647 - qty)
            .values(stock_quantity=Product.stock_quantity + qty,
                    buying_price=cost, updated_at=datetime.utcnow(),
                    supplier_name=supplier or Product.supplier_name))
        if result.rowcount != 1:
            raise StockChanged('Review stock quantities.')
        db.session.add(StockMovement(business_id=b.id, product_id=pid, kind="restock",
            quantity_change=qty, restock_id=receipt.id, occurred_at=receipt.received_at))
    log("RESTOCK_CREATED", f"Stock receipt {batch} recorded for {len(parsed)} products.", actor=actor, business_id=b.id)
    from app.notifications.service import notify
    message = (f'{products[parsed[0][0]].name} was restocked with {parsed[0][1]} {products[parsed[0][0]].unit}.'
        if len(parsed) == 1 else f'Stock replenished for {len(parsed)} products ({sum(qty for _, qty, _ in parsed)} units received).')
    notify(b.id, 'restock_recorded', 'Stock replenished', message, f'restock:{batch}:created')
    from app.notifications.email_outbox import queue_restock_alert
    queue_restock_alert(
        b.id,
        [
            {
                'name': products[pid].name,
                'quantity': qty,
                'unit': products[pid].unit,
            }
            for pid, qty, _ in sorted(parsed)
        ],
        f'restock:{batch}:created',
    )
    return batch
