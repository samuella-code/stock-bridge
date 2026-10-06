"""Read-only presentation state from the selected business's existing records."""
from sqlalchemy import exists
from app import db
from app.models import Expense, Product, Restock, Sale, SaleItem


def setup_progress(business):
    # One query; never load history or depend on the dashboard date range.
    products, sales, expenses, restocks = db.session.query(
        exists().where(Product.business_id == business.id),
        exists().where(Sale.business_id == business.id, Sale.voided_at.is_(None),
            exists().where(SaleItem.sale_id == Sale.id, SaleItem.quantity > 0)),
        exists().where(Expense.business_id == business.id, Expense.voided_at.is_(None)),
        exists().where(Restock.business_id == business.id),
    ).one()
    steps = [
        {'key': 'product', 'title': 'Add your first product',
         'description': 'Enter the quantity you already have as opening stock.',
         'endpoint': 'products.create', 'complete': products},
        {'key': 'sale', 'title': 'Record your first sale',
         'description': 'Sold quantities reduce stock automatically.',
         'endpoint': 'sales.index', 'complete': sales},
        {'key': 'expense', 'title': 'Record your first expense',
         'description': 'Include running costs to understand your profit.',
         'endpoint': 'expenses.index', 'complete': expenses},
    ]
    return {'steps': steps, 'completed': sum(step['complete'] for step in steps),
        'show_welcome': not any((products, sales, expenses, restocks)),
        'expanded': not any((products, sales, expenses, restocks))}
