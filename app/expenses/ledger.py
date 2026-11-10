"""Expense row and actor audit share the caller's transaction."""
from datetime import datetime
from app import db
from app.models import Expense
from app.admin.routes import log


def create_expense(business,*,description,amount,category,note,actor,spent_at=None):
    row=Expense(business_id=business.id,description=description,amount=amount,category=category,
                note=note,spent_at=spent_at or datetime.utcnow())
    db.session.add(row);db.session.flush()
    log('EXPENSE_CREATED',f'Expense {row.id} created.',actor=actor,business_id=business.id)
    return row
