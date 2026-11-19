"""Disposable, disconnected coverage for local report windows and verified collections."""
import csv
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app import db
from app.admin import report_data
from app.admin.routes import verified_payments_query
from app.models import Payment, User
from tests.test_admin_portal import client, seed, admin_login
from tests.test_admin_subscription_reporting import customer, payment

NOW = datetime(2026, 11, 19, 0, 30)


@pytest.mark.parametrize('period,days', [('7d',7),('30d',30),('90d',90),('today',1)])
def test_presets_start_at_lagos_midnight(period, days):
    window=report_data.report_range({'period':period}, NOW)
    assert window['start'] == datetime(2026,11,18,23)-timedelta(days=days-1)
    assert window['end'] == NOW
    assert (window['last']-window['first']).days == days-1


@pytest.mark.parametrize('first,last', [('19/11/2026','19/11/2026'),('2026-11-19','2026-11-19')])
def test_custom_dates_preserve_iso_links_with_local_boundaries(first,last):
    window=report_data.report_range(dict(period='custom',start=first,end=last),NOW)
    assert window['start'] == datetime(2026,11,18,23)
    assert window['end'] == datetime(2026,11,19,23)


@pytest.mark.parametrize('args', [dict(period='bad'),dict(period='custom',start='31/02/2026',end='01/03/2026'),
    dict(period='custom',start='20/11/2026',end='19/11/2026'),
    dict(period='custom',start='01/01/2020',end='19/11/2026'),
    dict(period='custom',start='9999-12-31',end='9999-12-31')])
def test_invalid_dates_fail_without_queries(args):
    with pytest.raises(ValueError):report_data.report_range(args,NOW)


def test_weekly_collections_scope_boundaries_and_owner_counts(client,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('External action forbidden')
    monkeypatch.setattr('app.admin.routes.verify_transaction',forbidden)
    monkeypatch.setattr('app.admin.routes.safely_send',forbidden)
    admin,original,shop=seed(client)
    client.application.config['SUBSCRIPTIONS_ENABLED']=True
    owner,shops,sub=customer(now=NOW)
    owner.created_at=datetime(2026,11,15,23) # Monday 16th in WAT
    old=db.session.get(User,original);old.created_at=datetime(2020,1,1);old.suspended_at=NOW
    staff=User(full_name='Employee',email='employee@example.invalid',staff_only=True,password_hash='unused',created_at=NOW)
    db.session.add(staff)
    for name,stamp in [('before',datetime(2026,11,14,22,59)),('start',datetime(2026,11,14,23)),
        ('sunday',datetime(2026,11,15,22,59)),('monday',datetime(2026,11,15,23)),
        ('end',datetime(2026,11,19,23))]:payment(owner,sub,name,stamp)
    bad=payment(owner,sub,'pending',datetime(2026,11,16));bad.status='pending'
    legacy=Payment(business_id=shops[0].id,customer_email=owner.email,product='lifetime',reference='legacy',
        amount_kobo=300000,status='success',paid_at=datetime(2026,11,16))
    db.session.add(legacy);db.session.commit()
    window=report_data.report_range(dict(period='custom',start='15/11/2026',end='19/11/2026'),NOW)
    metrics=report_data.report(window,verified_payments_query(),NOW)
    assert metrics['new_customers']==1 and metrics['active']==1 and metrics['paying']==1
    assert metrics['payment_count']==4 and metrics['by_plan']==dict(basic=0,plus=0,pro=1)
    amount=sub.amount_kobo
    assert metrics['collected']==3*amount+300000
    assert [(w['start'],w['end'],w['amount']) for w in metrics['weeks']] == [
        ('15/11/2026','15/11/2026',2*amount),('16/11/2026','19/11/2026',amount+300000)]
    assert sum(w['amount'] for w in metrics['weeks'])==metrics['collected']


def test_html_csv_agree_are_read_only_and_delivery_stays_disabled(client,monkeypatch):
    seed(client);owner,shops,sub=customer()
    payment(owner,sub,'verified',datetime(2026,11,16,12));db.session.commit()
    admin_login(client)
    statements=[];compiled=[]
    def capture(conn,cursor,statement,params,context,many):statements.append(statement)
    def compile_query(conn,clause,params,multiparams,options):
        if 'UNION ALL' in str(clause):compiled.append(str(clause.compile(dialect=postgresql.dialect())))
    event.listen(db.engine,'before_cursor_execute',capture);event.listen(db.engine,'before_execute',compile_query)
    try:
        query='/admin/reports?period=custom&start=15/11/2026&end=19/11/2026'
        page=client.get(query);html=page.get_data(as_text=True)
        response=client.get(query+'&format=csv')
        rows=list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip('\ufeff'))))
    finally:
        event.remove(db.engine,'before_cursor_execute',capture);event.remove(db.engine,'before_execute',compile_query)
    assert page.status_code==response.status_code==200
    assert html.count('<h1>')==1 and 'Platform reports' in html
    assert 'Scheduled delivery is not enabled yet.' in html
    assert 'name="weekly_email" role="switch"' in html and 'role="switch" disabled' not in html
    assert 'UTC' not in html and 'WAT' in html
    assert 'Collected revenue is not recurring revenue.' in html
    assert report_data.money(sub.amount_kobo) in html
    assert all(len(row)==6 for row in rows)
    assert sum(float(row[4]) for row in rows if row[0]=='Weekly') == sub.amount_kobo/100
    assert next(row[4] for row in rows if row[:2]==['Summary','Collected'])==report_data.naira(sub.amount_kobo)
    assert response.headers['Cache-Control']=='no-store, private'
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert compiled and all('GROUP BY' in sql and 'UNION ALL' in sql for sql in compiled)
    postgres=report_data.daily_collections_query(datetime(2026,11,15),datetime(2026,11,20),
        verified_payments_query(),dialect='postgresql')
    sql=str(postgres.statement.compile(dialect=postgresql.dialect()))
    assert "INTERVAL '1 hour'" in sql and 'GROUP BY date(' in sql
    assert client.post('/admin/reports',data={'weekly_email':'on'}).status_code in (403,405)


def test_empty_state_errors_and_anonymous_export(client):
    seed(client)
    assert client.get('/admin/reports?format=csv').status_code==302
    admin_login(client)
    html=client.get('/admin/reports?period=90d').get_data(as_text=True)
    assert 'No verified payments in this period.' in html
    assert '90 days' in html
    response=client.get('/admin/reports?period=custom&start=bad&end=bad&format=csv')
    assert response.status_code==400
    assert b'Choose valid dates' in response.data
