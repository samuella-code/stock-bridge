"""One sale/stock/notification/audit write path; caller owns locks and commit."""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import update
from app import db
from app.models import Product, Sale, SaleItem, StockMovement


class StockChanged(ValueError):
    pass


def create_sale(business, parsed, products, *, sold_at, method, note, actor):
    sale = Sale(business_id=business.id, sold_at=sold_at, payment_method=method, note=note)
    db.session.add(sale)
    db.session.flush()
    total = Decimal('0')
    for pid, quantity, price in sorted(parsed):
        product = products[pid]
        before = product.stock_quantity
        result = db.session.execute(update(Product).where(
            Product.id == pid, Product.business_id == business.id,
            Product.active.is_(True), Product.stock_quantity >= quantity
        ).values(stock_quantity=Product.stock_quantity-quantity, updated_at=datetime.utcnow())
         .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise StockChanged('Stock changed; review the sale.')
        selling_price = product.selling_price if price is None else price
        db.session.add(SaleItem(sale_id=sale.id, product_id=pid, quantity=quantity,
                               unit_price=selling_price, unit_cost=product.buying_price))
        db.session.add(StockMovement(business_id=business.id, product_id=pid, kind='sale',
                                    quantity_change=-quantity, sale_id=sale.id, occurred_at=sold_at))
        from app.notifications.service import stock_transition
        stock_transition(product, before, before-quantity, f'sale:{sale.id}:product:{pid}')
        total += quantity*selling_price
    from app.notifications.service import notify
    notify(business.id, 'sale_recorded', 'Sale recorded',
           f'₦{total:,.2f} sale recorded successfully.', f'sale:{sale.id}:created', sale.id)
    from app.admin.routes import log
    log('SALE_CREATED', f'Sale {sale.id} created with {len(parsed)} items.', actor=actor,
        business_id=business.id, target_type='sale', target_id=sale.id)
    return sale
