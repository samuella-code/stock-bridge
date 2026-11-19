"""Activity regressions use disposable SQLite only; no transports or hosted data."""
import csv
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app import db
from app.admin import activity_data as activity
from app.models import AuditLog, Business, StaffAuditEvent, StaffInvitation, User
from tests.test_admin_customers import records
from tests.test_admin_portal import client, seed, admin_login


def add(action, actor=None, shop=None, **values):
    row = AuditLog(action=action, actor_id=actor, business_id=shop,
                   description=values.pop('description', 'Recorded.'), **values)
    db.session.add(row)
    db.session.flush()
    return row


def test_lagos_day_groups_and_hidden_details(client):
    admin, owner, shop = seed(client)
    now = datetime(2026, 11, 19, 0, 15)
    sale = add('SALE_CREATED', owner, shop, target_type='sale', target_id=30,
               created_at=datetime(2026, 11, 18, 23, 30))
    restock = add('RESTOCK_CREATED', owner, shop, description='Stock receipt private-receipt-code recorded.',
                  created_at=datetime(2026, 11, 18, 22, 30))
    role = add('STAFF_ROLE_CHANGED', owner, target_type='staff_role',
               created_at=datetime(2026, 11, 16, 12))
    values = activity.serialize([sale, restock, role], now)
    assert [v['day_label'] for v in values] == ['TODAY', 'YESTERDAY', '16/11/2026']
    assert values[0]['time'] == '00:30 WAT'
    assert values[0]['sentence'] == 'Owner recorded Sale #30 in Shop'
    assert 'private-receipt-code' not in values[1]['sentence']
    assert values[2]['sentence'] == 'Owner updated a staff role'
    assert len(activity.grouped(values)) == 3
    db.session.commit()
    admin_login(client)
    page = client.get('/admin/activity').get_data(as_text=True)
    assert '<details>' in page and '<details open' not in page
    assert 'private-receipt-code' in page  # only the collapsed recorded details
    assert 'Staff_role #None' not in page and 'Staff Role #None' not in page
    assert page.count('<h1>') == 1


def test_staff_sentences_masks_search_and_business_snapshot(client, records):
    owner, shop = records['owner'], records['shop']
    invite = db.session.get(StaffInvitation, records['pending'])
    row = add('STAFF_INVITED', owner, target_type='staff', target_id=invite.id,
              description='pending@example.invalid https://example.invalid/private token=hidden')
    db.session.add(StaffAuditEvent(audit_log_id=row.id, owner_id=owner,
        details={'invitation_id': invite.id, 'role': 'manager', 'businesses': [shop]}))
    empty = add('STAFF_CHANGED', owner)
    db.session.add(StaffAuditEvent(audit_log_id=empty.id, owner_id=owner,
        details={'invitation_id': invite.id, 'businesses': []}))
    delivery = add('STAFF_DELIVERY_RESULT', owner)
    db.session.add(StaffAuditEvent(audit_log_id=delivery.id, owner_id=owner,
        details={'invitation_id': invite.id}))
    foreign = add('STAFF_INVITED', owner)
    db.session.add(StaffAuditEvent(audit_log_id=foreign.id, owner_id=records['original'],
        details={'invitation_id': invite.id, 'businesses': [shop], 'role': 'manager'}))
    db.session.commit()
    values = activity.serialize([row, foreign], datetime.utcnow())
    assert 'p***@example.invalid as Manager' in values[0]['sentence']
    assert values[0]['business']
    assert 'pending@example.invalid' not in str(values)
    assert 'https://' not in str(values) and 'token=hidden' not in str(values)
    assert 'p***@' not in values[1]['sentence'] and values[1]['business'] == ''
    ids = {r.id for r in activity.filtered(business=db.session.get(Business, shop))}
    assert {row.id, delivery.id} <= ids
    assert empty.id not in ids and foreign.id not in ids
    searched = {r.id for r in activity.filtered(q='pending@example.invalid')}
    assert row.id in searched and foreign.id not in searched
    admin_login(client)
    for suffix in ('', '?format=csv'):
        response = client.get('/admin/activity' + suffix)
        assert response.status_code == 200
        assert b'pending@example.invalid' not in response.data
        assert b'employee@example.invalid' not in response.data
        assert b'Operator' in response.data


@pytest.mark.parametrize('category,action', [('sales', 'SALE_CREATED'), ('stock', 'RESTOCK_CREATED'),
    ('stock', 'PRODUCT_UPDATED'), ('expenses', 'EXPENSE_CREATED'), ('staff', 'ADMIN_STAFF_CANCEL'),
    ('security', 'ADMIN_MFA_FAILED'), ('account', 'USER_SUSPENDED'), ('payments', 'ADMIN_PAYMENT_CHECK_RESULT')])
def test_categories_and_person_filters(client, category, action):
    admin, owner, shop = seed(client)
    expected = add(action, owner, shop)
    other = add('UNKNOWN_EVENT', description='literal%_match')
    db.session.commit()
    assert [r.id for r in activity.filtered(action=category, person=owner)] == [expected.id]
    assert [r.id for r in activity.filtered(person='system')] == [other.id]
    assert [r.id for r in activity.filtered(q='%_')] == [other.id]
    assert list(activity.filtered(action=category, person='system')) == []


def test_paging_export_filters_and_select_only(client):
    admin, owner, shop = seed(client)
    stamp = datetime.utcnow() - timedelta(days=1)
    ids = [add('SALE_CREATED', owner, shop, target_type='sale', target_id=n,
               created_at=stamp, description='receipt-code-not-for-export').id for n in range(1, 107)]
    db.session.get(User, owner).full_name = '=formula owner@example.invalid'
    db.session.commit()
    admin_login(client)
    before = AuditLog.query.count()
    statements = []
    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement)
    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        query = f'action=sales&business_id={shop}&person_id={owner}'
        response = client.get('/admin/activity?' + query)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Page 1 of 4' in html and 'Load older activity' in html
        assert 'business_id=' + str(shop) in html and 'person_id=' + str(owner) in html
        assert [r.id for r in activity.filtered(action='sales').limit(30)] == list(reversed(ids))[:30]
        exported = client.get('/admin/activity?' + query + '&format=csv')
        rows = list(csv.reader(io.StringIO(exported.get_data(as_text=True).lstrip('\ufeff'))))
        assert len(rows) == 107
        assert rows[1][2].startswith("'=formula o***@example.invalid")
        assert [r[-1] for r in rows[1:]] == ['Sale #' + str(n) for n in range(106, 0, -1)]
        assert 'receipt-code-not-for-export' not in exported.get_data(as_text=True)
        assert exported.headers['Cache-Control'] == 'no-store, private'
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert AuditLog.query.count() == before


@pytest.mark.parametrize('query,code', [('business_id=-1',400),('person_id=abc',400),
    ('person_id=999999999999999999999',400),('business_id=999999',404),('person_id=999999',404)])
def test_invalid_filter_selection(client, query, code):
    seed(client)
    admin_login(client)
    assert client.get('/admin/activity?' + query).status_code == code


def test_activity_requires_admin_and_postgres_json_compiles_offline(client):
    admin, owner, shop = seed(client)
    assert client.get('/admin/activity?format=csv').status_code == 302
    clause = activity.staff_business_clause(db.session.get(Business, shop), dialect='postgresql')
    sql = str(clause.compile(dialect=postgresql.dialect()))
    assert '@>' in sql and 'JSONB' in sql
    assert activity.safe_text('<script> x@example.invalid password=secret https://private.invalid') == \
        '<script> x***@example.invalid [redacted] [link hidden]'
