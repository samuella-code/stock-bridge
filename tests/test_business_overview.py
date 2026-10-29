"""Pro overview regressions on guarded, disposable in-memory SQLite only."""
from datetime import datetime, timedelta
from decimal import Decimal
import re
from unittest.mock import patch
import pytest
from flask import template_rendered
from sqlalchemy import event
from app import db
from app.models import AccountBilling, BillingEvent, SaleItem
from app.main.overview import build_overview, METRICS
from app.main.reporting import (
    financial_totals, grouped_financial_totals, combined_financial_totals, REPORTING_BATCH_SIZE,
)
from app.subscriptions.entitlements import EntitlementSnapshot
from tests.test_business_insights import app, seed, login, product, sale, expense, subscription, NOW, START, END


class Clock(datetime):
    @classmethod
    def utcnow(cls):
        return NOW


def page(application, user=None, *, client=None, **args):
    client = client or application.test_client()
    if user: login(client,user)
    with patch('app.main.routes.datetime',Clock):
        return client.get('/business-overview',query_string=args or {
            'period':'custom','from':'2026-10-08','to':'2026-10-14'})


def captured_page(application,user=None,**kwargs):
    contexts=[]
    def capture(sender,template,context,**extra):
        if template.name=='business_overview.html':contexts.append(context)
    with template_rendered.connected_to(capture,application):response=page(application,user,**kwargs)
    return response,contexts[0] if contexts else {}


def test_exact_combined_and_per_business_totals_with_saved_costs_and_zero_activity(app):
    user,shops=seed('pro',businesses=3)
    a,b,empty=shops; p=product(a);q=product(b)
    first=sale(a,p,START,qty=2,price='10.10',cost='4.05')
    # A second line in one sale must not multiply expenses or sale counts.
    db.session.add(SaleItem(sale_id=first.id,product_id=p.id,quantity=1,unit_price=Decimal('5'),unit_cost=Decimal('2')))
    sale(b,q,END-timedelta(microseconds=1),qty=3,price='20',cost='7')
    expense(a,START,'3');expense(a,START,'2');expense(b,START,'4')
    sale(a,p,START-timedelta(microseconds=1),price='100',cost='40')
    sale(a,p,END,price='900');sale(a,p,START,price='900',void=True)
    expense(a,START-timedelta(microseconds=1),'8');expense(a,END,'900');expense(b,START,'900',void=True)
    p.buying_price=Decimal('123456');db.session.commit()
    response,context=captured_page(app,user)
    assert response.status_code==200
    data=context['overview'];rows={row['business'].id:row for row in data['rows']}
    expected={a.id:dict(revenue=Decimal('25.20'),cogs=Decimal('10.10'),gross_profit=Decimal('15.10'),expenses=Decimal('5'),net_profit=Decimal('10.10')),
              b.id:dict(revenue=Decimal('60'),cogs=Decimal('21'),gross_profit=Decimal('39'),expenses=Decimal('4'),net_profit=Decimal('35')),
              empty.id:dict(revenue=Decimal('0'),cogs=Decimal('0'),gross_profit=Decimal('0'),expenses=Decimal('0'),net_profit=Decimal('0'))}
    for bid,values in expected.items():
        assert {key:rows[bid]['current'][key] for key,_ in METRICS}==values
        assert rows[bid]['current']==financial_totals(bid,START,END)
    assert data['current']['number_of_sales']==2 and data['current']['expense_count']==3
    assert {k:data['current'][k] for k,_ in METRICS}==dict(revenue=Decimal('85.20'),cogs=Decimal('31.10'),gross_profit=Decimal('54.10'),expenses=Decimal('9'),net_profit=Decimal('45.10'))
    assert data['previous']['revenue']==Decimal('100') and data['previous']['expenses']==Decimal('8')
    for key in data['current']:
        assert data['current'][key]==sum(row['current'][key] for row in data['rows'])
        assert data['previous'][key]==sum(row['previous'][key] for row in data['rows'])
    assert data['previous_start']==datetime(2026,10,1)
    html=response.get_data(as_text=True)
    assert f'data-overview-business="{empty.id}"' in html
    assert 'No recorded sales or operating expenses in the selected period.' in html
    combined_cards=html.split('<div class="metric-grid">',1)[1].split('</div>',1)[0]
    assert re.findall(r'<strong>₦([^<]+)</strong>',combined_cards)==[f'{data["current"][key]:,.2f}' for key,_ in METRICS]
    for bid,values in expected.items():
        card=re.search(r'data-overview-business="'+str(bid)+r'">(.*?)</article>',html,re.S)[1]
        assert re.findall(r'<strong>₦([^<]+)</strong>',card)==[f'{values[key]:,.2f}' for key,_ in METRICS]


@pytest.mark.parametrize('kind,allowed',[('basic',False),('plus',False),('trial',False),
    ('lifetime',False),('lifetime_plus',False),('pro',True),('lifetime_pro',True)])
@pytest.mark.parametrize('interval',['monthly','yearly'])
def test_pro_only_gate_navigation_and_no_denied_data_reads(app,kind,allowed,interval):
    user,shops=seed(kind);shops[0].name='Private business name'
    sub=subscription(user)
    if sub:sub.billing_interval=interval
    db.session.commit()
    with patch('app.main.overview.grouped_financial_totals',wraps=grouped_financial_totals) as grouped:
        response=page(app,user)
    assert response.status_code==(200 if allowed else 403)
    assert grouped.call_count==(2 if allowed else 0)
    assert response.headers['Cache-Control']=='private, no-store'
    if not allowed:
        assert b'Private business name' not in response.data and b'data-overview-results' not in response.data
    client=app.test_client();login(client,user)
    reports=client.get('/reports')
    assert reports.status_code==200
    assert (b'href="/business-overview"' in reports.data) is allowed
    if kind in ('plus','pro','lifetime_plus','lifetime_pro'):assert client.get('/insights').status_code==200


@pytest.mark.parametrize('state,allowed',[('expired',False),('ends-now',False),('future',False),
    ('starts-now',True),('past_due',True),('cancelled',True),('cancel_at_period_end',True),
    ('failed',False),('suspended',False),('unverified',False),('admin',False),('dual',False)])
def test_authoritative_period_and_account_restrictions(app,state,allowed):
    user,shops=seed('pro');shops[0].name='Unavailable business name';sub=subscription(user)
    if state=='expired':sub.current_period_end=NOW-timedelta(seconds=1)
    elif state=='ends-now':sub.current_period_end=NOW
    elif state=='future':sub.current_period_start=NOW+timedelta(seconds=1)
    elif state=='starts-now':sub.current_period_start=NOW
    elif state=='suspended':user.suspended_at=NOW
    elif state=='unverified':user.email_verified_at=None
    elif state=='admin':user.role='admin'
    elif state=='dual':user.admin_enabled=True
    else:sub.status=state
    db.session.commit()
    with patch('app.main.overview.grouped_financial_totals',wraps=grouped_financial_totals) as grouped:
        response=page(app,user)
    assert (response.status_code==200) is allowed
    assert grouped.call_count==(2 if allowed else 0)
    if not allowed:assert b'Unavailable business name' not in response.data
    assert response.headers['Cache-Control']=='private, no-store'


def test_permitted_set_and_totals_are_independent_of_selected_header(app):
    user,shops=seed('pro',businesses=6);other,foreign=seed('pro',email='foreign-overview@invalid.test')
    account=db.session.get(AccountBilling,user.id);account.primary_business_id=shops[1].id
    for i,shop in enumerate(shops):
        shop.name=f'Owned overview shop {i}';sale(shop,product(shop),START,price=str(10+i),cost='1')
    foreign[0].name='Foreign overview secret';sale(foreign[0],product(foreign[0]),START,price='999')
    shops[0].suspended_at=NOW;db.session.commit()
    client=app.test_client();login(client,user)
    expected_ids={s.id for s in shops[1:5]}
    for active in [None,shops[1].id,shops[3].id,shops[5].id,shops[0].id,foreign[0].id]:
        with client.session_transaction() as state:
            if active is None:state.pop('active_business_id',None)
            else:state['active_business_id']=active
        with patch('app.main.insights.EntitlementSnapshot',wraps=EntitlementSnapshot) as snap, \
             patch('app.subscriptions.entitlements.selected_business',side_effect=AssertionError('Second context')), \
             patch('app.subscriptions.entitlements.effective_access',side_effect=AssertionError('Second instant')):
            response,context=captured_page(app,client=client)
        assert response.status_code==200 and snap.call_count==1
        assert {row['business'].id for row in context['overview']['rows']}==expected_ids
        assert context['overview']['current']['revenue']==Decimal('50')
        expected_header=shops[3] if active==shops[3].id else shops[1]
        assert context['subscription_business'].id==expected_header.id
        html=response.get_data(as_text=True)
        assert f'Active business: {expected_header.name}. Switch business' in html
        assert 'independent of the selected-business header' in html
        for secret in [shops[0].name,shops[5].name,foreign[0].name]:assert secret not in html
        with client.session_transaction() as state:assert state.get('active_business_id')==active


@pytest.mark.parametrize('next_state',['plus','basic','expired'])
def test_next_request_downgrade_or_expiry_removes_all_overview_results(app,next_state):
    user,shops=seed('pro',businesses=3);client=app.test_client();login(client,user)
    assert page(app,client=client).status_code==200
    sub=subscription(user)
    if next_state=='expired':sub.current_period_end=NOW
    else:sub.plan_code=next_state
    db.session.commit()
    with patch('app.main.overview.grouped_financial_totals',side_effect=AssertionError('Denied financial read')):
        response=page(app,client=client)
    assert response.status_code==403 and b'data-overview-results' not in response.data
    for shop in shops:assert shop.name.encode() not in response.data


@pytest.mark.parametrize('values,field',[({'from':'2026-10-22','to':'2026-10-20'},'to'),
    ({'from':'2026-10-01'},'to'),({'to':'2026-10-02'},'from'),
    ({'from':'0001-01-01','to':'0001-01-01'},'from'),
    ({'from':'2026-02-30','to':'2026-03-01'},'from'),
    ({'from':'<script>','to':'2026-10-01'},'from')])
def test_invalid_dates_preserved_accessible_without_financial_reads(app,values,field):
    user,_=seed('pro')
    with patch('app.main.overview.grouped_financial_totals',side_effect=AssertionError('Invalid financial read')):
        response=page(app,user,period='month',**values)
    assert response.status_code==400 and b'data-overview-results' not in response.data
    html=response.get_data(as_text=True)
    assert f'aria-describedby="overview-{field}-error"' in html
    assert '<option value="custom" selected>' in html and '<script>' not in html


@pytest.mark.parametrize('last,incomplete',[('2026-10-27',False),('2026-10-28',True),('2026-11-01',True)])
def test_explicit_dates_precedence_equal_utc_periods_and_incomplete(app,last,incomplete):
    user,_=seed('pro')
    response,context=captured_page(app,user,period='month',**{'from':'2026-10-21','to':last})
    assert response.status_code==200
    data=context['overview'];assert data['start']==datetime(2026,10,21)
    assert data['start']-data['previous_start']==data['end']-data['start']
    assert data['incomplete'] is incomplete
    assert all(row['state']==('incomplete' if incomplete else 'no-baseline') for row in data['trends'].values())


@pytest.mark.parametrize('cost_period',['current','previous'])
def test_zero_cost_warning_combined_and_per_business_comparisons(app,cost_period):
    user,shops=seed('pro',businesses=2)
    for i,shop in enumerate(shops):
        p=product(shop)
        sale(shop,p,START,price='30',cost='0' if i==0 and cost_period=='current' else '10')
        sale(shop,p,START-timedelta(days=1),price='20',cost='0' if i==0 and cost_period=='previous' else '5')
    db.session.commit()
    response,context=captured_page(app,user);data=context['overview']
    assert data['zero_cost'] and b'Review zero saved costs.' in response.data
    assert data['trends']['gross_profit']['state']=='cost-review'
    assert data['trends']['net_profit']['state']=='cost-review'
    assert data['rows'][0]['trends']['gross_profit']['state']=='cost-review'
    assert data['rows'][1]['trends']['gross_profit']['state']=='comparable'
    assert data['current']['revenue']==Decimal('60')
    assert data[cost_period]['zero_cost_lines']==1


def test_negative_baseline_and_zero_activity_have_no_percentage_claims(app):
    user,shops=seed('pro');p=product(shops[0])
    sale(shops[0],p,START-timedelta(days=1),price='5',cost='10')
    sale(shops[0],p,START,price='10',cost='15');expense(shops[0],START,'2')
    db.session.commit()
    _,context=captured_page(app,user);data=context['overview']
    assert data['previous']['gross_profit']==Decimal('-5')
    assert data['trends']['gross_profit']['state']=='negative-baseline'
    assert data['trends']['gross_profit']['percent'] is None
    assert data['trends']['expenses']['state']=='no-baseline'


@pytest.mark.parametrize('count,foreign_count,volume',[(1,0,10),(5,0,10),(5,100,100)])
def test_cold_select_only_bounded_grouped_queries(app,count,foreign_count,volume):
    user,shops=seed('pro',businesses=count)
    if foreign_count:seed('pro',businesses=foreign_count,email='query-foreign@invalid.test')
    for shop in shops:
        p=product(shop)
        for _ in range(volume):sale(shop,p,START)
    account=db.session.get(AccountBilling,user.id);account.trial_eligible=True
    db.session.commit();db.session.expire_all()
    statements=[]
    def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:
        with patch('app.main.reporting.financial_totals',side_effect=AssertionError('Scalar report per business')):
            response,context=captured_page(app,user)
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert response.status_code==200
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    activity=[sql for sql in statements if 'FROM sale ' in sql or 'FROM expense ' in sql]
    assert len(activity)==4 and all('GROUP BY' in sql for sql in activity)
    assert len(statements)<=11  # one owner read, four entitlement, ownership, notification, four financial
    assert len(context['overview']['rows'])==count
    assert db.session.get(AccountBilling,user.id).trial_started_at is None
    assert BillingEvent.query.filter_by(kind='trial_started').count()==0
    print(f'OVERVIEW QUERIES permitted={count} foreign={foreign_count} lines_per_business={volume}: total={len(statements)} financial={len(activity)} SELECT-only')


def test_no_permitted_businesses_anonymous_and_disabled(app):
    response=page(app)
    assert response.status_code==302 and response.headers['Cache-Control']=='private, no-store'
    user,shops=seed('pro');shops[0].suspended_at=NOW;db.session.commit()
    with patch('app.main.overview.grouped_financial_totals',wraps=grouped_financial_totals) as grouped:
        response,context=captured_page(app,user)
    assert response.status_code==200 and not context['overview']['rows']
    assert all(value==0 for value in context['overview']['current'].values())
    assert shops[0].name.encode() not in response.data
    assert b'No financial records were queried' in response.data
    app.config['SUBSCRIPTIONS_ENABLED']=False
    assert page(app,user).status_code==403


def test_grouped_helper_deduplicates_ids_and_batches_parameters(app):
    user,shops=seed('pro',businesses=REPORTING_BATCH_SIZE+1)
    ids=[s.id for s in shops];statements=[]
    def record(conn,cursor,statement,parameters,context,many):
        statements.append((statement,parameters))
    event.listen(db.engine,'before_cursor_execute',record)
    try:totals=grouped_financial_totals(ids+[ids[0]],START,END)
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert set(totals)==set(ids) and len(statements)==4
    assert all(len(params)<=REPORTING_BATCH_SIZE+9 for _,params in statements)
    assert all(all(value==0 for value in row.values()) for row in totals.values())
    assert combined_financial_totals(list(totals.values()))==financial_totals(ids[0],START,END)
