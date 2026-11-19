"""Admin Businesses regressions on disposable SQLite; no hosted services."""
import csv
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app import db
from app.admin import business_data
from app.models import (AuditLog, Business, Expense, Product, Restock, Sale,
                        StaffMembership, StaffMembershipBusiness, StockMovement, User)
from tests.test_admin_portal import client, seed, admin_login
from tests.test_admin_customers import records


@pytest.fixture(autouse=True)
def no_delivery_or_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Unexpected provider or email action')
    monkeypatch.setattr('app.email_service._send_email', forbidden)
    monkeypatch.setattr('app.admin.routes.verify_transaction', forbidden)


def test_business_table_masks_emails_and_shows_local_last_sale_and_assigned_team(client, records):
    now = datetime.utcnow()
    shops = [db.session.get(Business, records[k]) for k in ('shop', 'other_shop')]
    first = Sale(business_id=shops[0].id, sold_at=now-timedelta(minutes=2))
    last = Sale(business_id=shops[0].id, sold_at=now-timedelta(minutes=2))
    void = Sale(business_id=shops[0].id, sold_at=now-timedelta(minutes=1), voided_at=now)
    future = Sale(business_id=shops[0].id, sold_at=now+timedelta(days=1))
    db.session.add_all([first, last, void, future]); db.session.commit()
    rows = business_data.serialize(shops, now)
    assert rows[0]['last_sale_id'] == last.id
    assert rows[0]['last_sale'] == business_data.local_time(last.sold_at)
    assert rows[0]['team'] == [dict(id=records['employee'],email='e•••@e•••.invalid',role='Salesperson/Cashier')]
    assert rows[1]['team'] == []
    admin_login(client)
    page = client.get('/admin/businesses').get_data(as_text=True)
    assert '<h1>Businesses</h1>' in page and 'WAT' in page
    assert f'Sale #{last.id}' in page and f'Sale #{void.id}' not in page
    assert 'employee@example.invalid' not in page and db.session.get(User,records['owner']).email not in page
    assert 'Show owner email' in page and 'Show employee email' in page
    assert 'Used in last 7 days' in page and 'Never used' in page
    assert 'No sales yet' in page and 'Export CSV' in page
    # Reuse the existing CSRF-protected, audited reveal contract.
    shown = client.post(f"/admin/users/{records['employee']}/email",json={})
    assert shown.json['email'] == 'employee@example.invalid'
    assert AuditLog.query.filter_by(action='ADMIN_EMAIL_REVEALED',target_id=records['employee']).count() == 1


def test_scope_literal_search_and_filter_validation(client, records):
    protected = Business(user_id=records['admin'],name='Admin secret shop')
    literal = Business(user_id=records['owner'],name='100%_shop')
    db.session.add_all([protected,literal]); db.session.commit()
    admin_login(client)
    assert 'Admin secret shop' not in client.get('/admin/businesses').get_data(as_text=True)
    filtered = client.get('/admin/businesses?q=%25_').get_data(as_text=True)
    assert '100%_shop' in filtered and '>Shop</a>' not in filtered
    page = client.get('/admin/businesses?q=employee@example.invalid').get_data(as_text=True)
    assert 'No matching businesses.' in page  # search belongs to business owners
    for path in ('/admin/businesses','/admin/businesses/export.csv'):
        assert client.get(path+'?status=invalid').status_code == 400
    assert client.get('/admin/businesses?q=owner@example.com').status_code == 200


@pytest.mark.parametrize('kind', ['product','sale','restock','expense','stock','audit'])
def test_usage_filters_boundary_and_never_used_include_all_record_types(client, kind):
    _, owner, shop = seed(client)
    now = datetime.utcnow()
    boundary = now-timedelta(days=7)
    product = Product(business_id=shop,name='Historical item',created_at=now-timedelta(days=30),updated_at=now-timedelta(days=30))
    if kind in ('restock','stock'):
        db.session.add(product); db.session.flush()
    record = {
        'product': lambda: Product(business_id=shop,name='Item',created_at=boundary,updated_at=boundary),
        'sale': lambda: Sale(business_id=shop,sold_at=boundary,voided_at=now),
        'restock': lambda: Restock(business_id=shop,product_id=product.id,quantity=1,unit_cost=1,received_at=boundary),
        'expense': lambda: Expense(business_id=shop,description='Transport',amount=1,spent_at=boundary),
        'stock': lambda: StockMovement(business_id=shop,product_id=product.id,kind='adjustment',quantity_change=1,occurred_at=boundary),
        'audit': lambda: AuditLog(business_id=shop,action='SALE_CREATED',description='Synthetic',created_at=boundary),
    }[kind]()
    db.session.add(record); db.session.commit()
    assert [b.id for b in business_data.query(status='used',now=now)] == [shop]
    assert not business_data.query(status='used',now=now+timedelta(microseconds=1)).all()
    assert not business_data.query(status='never-used',now=now).all()


def test_admin_inspection_is_not_use_and_old_or_future_work_is_not_recent(client):
    _,owner,shop = seed(client)
    now=datetime.utcnow()
    db.session.add(AuditLog(business_id=shop,action='ADMIN_READ_ONLY_OPENED',description='Synthetic',created_at=now))
    db.session.commit()
    assert [b.id for b in business_data.query(status='never-used',now=now)] == [shop]
    assert not business_data.query(status='used',now=now).all()
    db.session.add(Sale(business_id=shop,sold_at=now+timedelta(days=1))); db.session.commit()
    assert not business_data.query(status='used',now=now).all()
    assert not business_data.query(status='never-used',now=now).all()


def test_foreign_assignment_and_removed_memberships_do_not_leak_team(client, records):
    member = db.session.get(StaffMembership,records['member'])
    db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=records['original_shop']))
    db.session.commit()
    original = db.session.get(Business,records['original_shop'])
    assert business_data.serialize([original],datetime.utcnow())[0]['team'] == []
    member.state='revoked'; member.revoked_at=datetime.utcnow(); db.session.commit()
    assert business_data.serialize([db.session.get(Business,records['shop'])],datetime.utcnow())[0]['team'] == []


def test_access_warning_detects_per_business_disagreement_even_when_counts_match(client, records):
    now=datetime.utcnow()
    db.session.get(Business,records['shop']).subscription_status='inactive'; db.session.commit()
    values=business_data.summary(now)
    assert values == dict(total=3,active=2,marked_active=2,disagreements=2)
    admin_login(client)
    assert b'Business access needs review.' in client.get('/admin/businesses?q=no-match').data
    client.application.config['SUBSCRIPTIONS_ENABLED']=False
    assert business_data.summary(now)['disagreements'] == 0
    assert b'Business access needs review.' not in client.get('/admin/businesses').data


def test_business_slot_and_suspension_follow_authoritative_access(client, records):
    from app.subscriptions.entitlements import effective_access
    now=datetime.utcnow()
    owner=db.session.get(User,records['owner'])
    extra=[Business(user_id=owner.id,name=f'Additional {i}') for i in range(5)]
    db.session.add_all(extra); db.session.commit()
    rows=Business.query.filter_by(user_id=owner.id).order_by(Business.id).all()
    actual=business_data.serialize(rows,now)
    assert [b['status']=='Active' for b in actual] == [effective_access(owner,business=b,now=now).can_write for b in rows]
    owner.suspended_at=now; db.session.commit()
    assert all(b['status']=='Suspended' for b in business_data.serialize(rows,now))


def test_export_all_batches_masks_formula_cells_and_matches_filters(client, records):
    stamp=datetime.utcnow()-timedelta(days=1)
    db.session.add_all(Business(user_id=records['owner'],name=f'=Shop {i}',created_at=stamp) for i in range(105))
    db.session.commit(); admin_login(client)
    response=client.get('/admin/businesses/export.csv?status=never-used&q=%3DShop')
    assert response.status_code == 200 and response.mimetype=='text/csv'
    text=response.get_data(as_text=True)
    rows=list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    assert len(rows)==106 and len({r[0] for r in rows[1:]})==105
    assert all(r[0].startswith("'=Shop") for r in rows[1:])
    assert db.session.get(User,records['owner']).email not in text and 'employee@example.invalid' not in text
    assert all('WAT' in r[4] for r in rows[1:])
    assert len(client.get('/admin/businesses?q=%3DShop&status=never-used&page=2').get_data())>0
    assert 'status=never-used' in client.get('/admin/businesses?q=%3DShop&status=never-used').get_data(as_text=True)


@pytest.mark.parametrize('path',['/admin/businesses','/admin/businesses/export.csv'])
def test_listing_and_export_require_admin_session(client, records, path):
    assert client.get(path).status_code == 302
    assert client.post('/auth/login',data={'email':'owner@example.com','password':'customer-password123'}).status_code == 302
    from flask import g
    g.pop('_login_user',None)
    assert client.get(path).status_code == 403


def test_business_queries_compile_for_postgres_and_load_in_batches(client, records):
    # Compilation catches grouping/window mistakes without connecting to PostgreSQL.
    statements=[]
    def capture(conn, clause, *args):
        if getattr(clause,'is_select',False):
            statements.append(str(clause.compile(dialect=postgresql.dialect())))
    event.listen(db.engine,'before_execute',capture)
    try:
        shops=business_data.query(status='used',now=datetime.utcnow()).all()
        business_data.serialize(shops,datetime.utcnow())
        statements.clear()
        rows=Business.query.filter_by(user_id=records['owner']).all()
        business_data.serialize(rows,datetime.utcnow())
        small=len(statements)
        db.session.add_all(Business(user_id=records['owner'],name=f'Batch {i}') for i in range(20)); db.session.commit()
        statements.clear()
        rows=Business.query.filter_by(user_id=records['owner']).all()
        business_data.serialize(rows,datetime.utcnow())
        assert len(statements)==small
        assert any('row_number() OVER (PARTITION BY sale.business_id' in sql for sql in statements)
    finally:
        event.remove(db.engine,'before_execute',capture)
