"""Exercise the PG scenario's synthetic fixture/report against memory SQLite only."""
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch
from types import SimpleNamespace
from tests.test_staff_foundation import app,staff_clock
from tests.support.postgres_staff_fixtures import T
from tests.support import postgres_staff_operations as operations


def test_pg_report_fixture_uses_real_models_and_scoped_report_route(app):
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return T
    with ExitStack() as stack:
        for name in ('service','routes','roles','delivery'):
            stack.enter_context(patch('app.staff.'+name+'.datetime',Clock))
        rt=SimpleNamespace(auth={'mode':'operations'},stack=stack,app=lambda:app)
        result=operations.report_check(rt,'ops-cont-1234abcd-ops04-report')
        assert result==dict(variant='report',status='PASS',assigned_business_only=True)


import pytest
from contextlib import contextmanager


@pytest.mark.parametrize('variant',['create','edit','stale','expense','revoke-product','revoke-expense'])
def test_pg_payloads_and_result_assertions_match_real_routes_sequentially_only(app,monkeypatch,variant):
    from app import db
    from app.models import Product,Expense,TransactionSubmission,AuditLog,StockMovement,Sale,StaffMembership
    from tests.support import postgres_staff_operations_races as races
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return T
    @contextmanager
    def connection():yield None
    def sequential(engine,clients,paths,payloads,observer,**kwargs):
        return [client.post(path,data=payload).status_code for client,path,payload in zip(clients,paths,payloads)]
    def result(rt,data,variant,statuses):
        with app.app_context():
            ledger=dict(
                products=db.session.query(Product.id,Product.stock_quantity,Product.opening_quantity,Product.buying_price,Product.selling_price).filter_by(business_id=data['shop']).order_by(Product.id).all(),
                receipts=db.session.query(TransactionSubmission.user_id,TransactionSubmission.operation,TransactionSubmission.result_reference,TransactionSubmission.completed_at).filter_by(business_id=data['shop']).all(),
                audits=db.session.query(AuditLog.actor_id,AuditLog.action).filter(AuditLog.business_id==data['shop'],AuditLog.action.in_(['PRODUCT_CREATED','PRODUCT_UPDATED','EXPENSE_CREATED'])).order_by(AuditLog.id).all(),
                expenses=db.session.query(Expense.id,Expense.amount).filter_by(business_id=data['shop']).all(),
                moves=db.session.query(StockMovement.kind,StockMovement.quantity_change).filter_by(business_id=data['shop']).order_by(StockMovement.id).all(),
                sales=db.session.query(Sale.id).filter_by(business_id=data['shop']).all(),
                membership=db.session.get(StaffMembership,data['member']).state)
            operations.validate_result(data,variant,statuses,ledger)
    monkeypatch.setattr(races,'concurrent_operations_posts',sequential)
    monkeypatch.setattr(operations,'assert_result',result)
    # The shared SQLite fixture holds an outer app context. Clear Flask-Login's
    # cached user between clients to emulate distinct real request contexts.
    from flask import g
    original_client=app.test_client
    def fresh_client(*args,**kwargs):
        client=original_client(*args,**kwargs);original_open=client.open
        def open_request(*a,**kw):
            with app.app_context():return original_open(*a,**kw)
        client.open=open_request
        return client
    monkeypatch.setattr(app,'test_client',fresh_client)
    app.config['WTF_CSRF_ENABLED']=True
    with ExitStack() as stack:
        for name in ('service','routes','roles','delivery'):
            stack.enter_context(patch('app.staff.'+name+'.datetime',Clock))
        rt=SimpleNamespace(auth={'mode':'operations'},stack=stack,app=lambda:app,connection=connection)
        assert operations.race(rt,'ops-cont-1234abcd-ops01-'+variant,variant)['status']=='PASS'
