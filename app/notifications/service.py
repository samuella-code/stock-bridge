"""No commits or network calls: notification rows share the business transaction."""
from flask import url_for
from app import db
from app.models import Business, Notification, Product
from app.notifications.email_outbox import enabled, queue_alert


def notify(business_id, kind, title, body, event_key, resource_id=None):
    field = {'product_created': 'product_added', 'sale_recorded': 'sales', 'restock_recorded': 'restocking'}.get(kind)
    if field and not enabled(business_id, field):
        return None
    business = db.session.get(Business, business_id)
    row = Notification(user_id=business.user_id, business_id=business_id, kind=kind,
        title=title, body=body, event_key=event_key, resource_id=resource_id)
    db.session.add(row)
    return row


def product_created(product):
    notify(product.business_id, 'product_created', 'Product added',
        f'{product.name} was added with an opening stock of {product.opening_quantity} {product.unit}.',
        f'product:{product.id}:created', product.id)


def stock_transition(product, before, after, event_key):
    # Zero wins over low-stock: one alert for a single downward transition.
    if before > 0 and after == 0:
        notify(product.business_id, 'out_of_stock', 'Out of stock',
            f'{product.name} is now out of stock.', f'{event_key}:out', product.id)
        queue_alert(product, after, 'out_of_stock', f'{event_key}:out')
    elif before > product.minimum_stock_level and 0 < after <= product.minimum_stock_level:
        notify(product.business_id, 'low_stock', 'Low stock',
            f'{product.name} has {after} {product.unit} remaining. Low-stock threshold: {product.minimum_stock_level}.',
            f'{event_key}:low', product.id)
        queue_alert(product, after, 'low_stock', f'{event_key}:low')


def destination(notification):
    if notification.kind in {'product_created', 'low_stock', 'out_of_stock'}:
        product = Product.query.filter_by(id=notification.resource_id, business_id=notification.business_id).first()
        if product:
            return url_for('products.detail', product_id=product.id)
        return url_for('products.index')
    return url_for('sales.index' if notification.kind == 'sale_recorded' else 'restocking.index')
