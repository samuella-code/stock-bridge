"""Synthetic in-memory SQLite only; no providers, mail or hosted databases."""
from datetime import datetime, timedelta
from decimal import Decimal
import re
from unittest.mock import patch

import pytest
from flask import g, template_rendered
from sqlalchemy import event
from app import db
from app.models import AccountBilling, BillingEvent, Expense, Product, Sale, SaleItem
from app.main.insights import build_insights, comparison, insights_business, slow_stock
from app.main.reporting import financial_totals
from app.main.routes import figures, period_dates
from app.subscriptions.entitlements import EntitlementSnapshot, access_label, effective_access
from tests.test_multi_business import app, seed as base_seed, login

NOW = datetime(2026, 10, 28, 12)
START, END = datetime(2026, 10, 8), datetime(2026, 10, 15)


def seed(*args, **kwargs):
    user, shops = base_seed(*args, **kwargs)
    sub = subscription(user)
    if sub:
        sub.current_period_start = NOW-timedelta(days=1)
        sub.current_period_end = NOW+timedelta(days=30)
    account = db.session.get(AccountBilling, user.id)
    if account.trial_started_at:
        account.trial_started_at = NOW-timedelta(days=1)
        account.trial_ends_at = NOW+timedelta(days=7)
    db.session.commit()
    return user, shops


def product(shop, name='Recorded product', **kw):
    row = Product(business_id=shop.id, name=name, created_at=NOW-timedelta(days=50),
                  stock_quantity=kw.pop('stock_quantity', 5), buying_price=999, **kw)
    db.session.add(row); db.session.flush()
    return row


def sale(shop, item, when, *, qty=1, price='10.10', cost='4.05', void=False):
    row = Sale(business_id=shop.id, sold_at=when, voided_at=NOW if void else None)
    db.session.add(row); db.session.flush()
    db.session.add(SaleItem(sale_id=row.id, product_id=item.id, quantity=qty,
                           unit_price=Decimal(price), unit_cost=Decimal(cost)))
    db.session.flush()
    return row


def expense(shop, when, amount='3.00', *, void=False):
    db.session.add(Expense(business_id=shop.id, description='Synthetic expense',
                          spent_at=when, amount=Decimal(amount), voided_at=NOW if void else None))
    db.session.flush()


def subscription(user):
    from app.models import RecurringSubscription
    return RecurringSubscription.query.filter_by(user_id=user.id).one_or_none()


def request_page(application, user, **args):
    client = application.test_client(); login(client, user)
    with patch('app.main.routes.datetime') as clock:
        clock.utcnow.return_value = NOW
        clock.min = datetime.min
        clock.max = datetime.max
        clock.strptime = datetime.strptime
        clock.combine = datetime.combine
        response = client.get('/insights', query_string=args or {
            'period':'custom', 'from':'2026-10-08', 'to':'2026-10-14'})
    return response


def test_shared_totals_snapshots_voids_boundaries_and_no_expense_fanout(app):
    user, shops = seed('plus'); other, foreign = seed('plus', email='other@invalid.test')
    shop = shops[0]; item = product(shop)
    sale(shop, item, START, qty=2)
    sale(shop, item, END-timedelta(microseconds=1), qty=3)
    sale(shop, item, START-timedelta(microseconds=1), price='900')
    sale(shop, item, END, price='900')
    sale(shop, item, START, price='900', void=True)
    sale(foreign[0], product(foreign[0]), START, price='900')
    expense(shop, START); expense(shop, END-timedelta(microseconds=1), '2.00')
    expense(shop, END, '900'); expense(shop, START, '900', void=True)
    expense(foreign[0], START, '900')
    db.session.commit()
    expected = dict(number_of_sales=2, units_sold=5, revenue=Decimal('50.50'),
                    cogs=Decimal('20.25'), gross_profit=Decimal('30.25'),
                    expenses=Decimal('5.00'), net_profit=Decimal('25.25'))
    totals = financial_totals(shop.id, START, END)
    assert {key:totals[key] for key in expected} == expected
    assert totals['zero_cost_lines'] == 0 and totals['expense_count'] == 2
    assert {key:figures(shop, START, END)[key] for key in expected} == expected
    item.buying_price = Decimal('123456.00'); db.session.commit()
    assert financial_totals(shop.id, START, END) == totals


@pytest.mark.parametrize('current,previous,delta,percent,state', [
    ('150','100','50','50','comparable'), ('50','100','-50','-50','comparable'),
    ('0','100','-100','-100','comparable'), ('0','0','0',None,'no-baseline'),
    ('100','0','100',None,'no-baseline'), ('10','-10','20',None,'negative-baseline'),
    ('-30','-10','-20',None,'negative-baseline'), ('100','100','0','0','comparable')])
def test_independent_comparison_expectations(current, previous, delta, percent, state):
    result = comparison(Decimal(current), Decimal(previous))
    assert result == dict(delta=Decimal(delta), percent=Decimal(percent) if percent is not None else None, state=state)


@pytest.mark.parametrize('last,expected', [('2026-10-27',False), ('2026-10-28',True), ('2026-11-10',True)])
def test_equal_utc_periods_and_incomplete_labels(app, last, expected):
    user, shops = seed('plus')
    _, start, end = period_dates({'period':'custom','from':'2026-10-21','to':last}, now=NOW)
    result = build_insights(shops[0].id, start, end, now=NOW)
    assert result['incomplete'] is expected
    assert start-result['previous_start'] == end-start
    assert result['previous_last'] == start-timedelta(days=1)
    assert all(row['state'] == ('incomplete' if expected else 'no-baseline') for row in result['trends'].values())
    assert 'No sales or operating expenses were recorded' in result['explanations'][0]


@pytest.mark.parametrize('cost_period', ['current','previous'])
def test_zero_cost_warning_preserves_values_and_withholds_profit_direction(app, cost_period):
    user, shops = seed('plus'); item = product(shops[0])
    sale(shops[0], item, START if cost_period=='current' else START-timedelta(days=1), cost='0')
    db.session.commit()
    data = build_insights(shops[0].id, START, END, now=NOW)
    totals = data[cost_period]
    assert totals['revenue'] == Decimal('10.10') and totals['cogs'] == 0
    assert totals['gross_profit'] == Decimal('10.10') and totals['zero_cost_lines'] == 1
    assert data['trends']['gross_profit']['state'] == 'cost-review'
    assert any('saved unit cost of zero' in text for text in data['explanations'])


def test_actionable_explanations_and_incomplete_suppression(app):
    user, shops = seed('plus'); shop=shops[0]; item=product(shop)
    sale(shop,item,START-timedelta(days=1),price='100',cost='40')
    sale(shop,item,START,price='50',cost='30')
    expense(shop,START-timedelta(days=1),'5');expense(shop,START,'10')
    db.session.commit()
    complete=build_insights(shop.id,START,END,now=NOW)
    assert complete['previous']['revenue']==Decimal('100')
    assert complete['current']['gross_profit']==Decimal('20')
    assert complete['trends']['revenue']['percent']==Decimal('-50')
    assert complete['trends']['expenses']['percent']==Decimal('100')
    text=' '.join(complete['explanations'])
    assert 'Recorded revenue is lower' in text
    assert 'operating expenses increased' in text
    assert 'Estimated gross profit decreased' in text
    assert 'do not establish a cause' in text
    incomplete=build_insights(shop.id,START,END,now=START+timedelta(hours=12))
    assert all(t['state']=='incomplete' for t in incomplete['trends'].values())
    assert not any(word in ' '.join(incomplete['explanations']) for word in ('is lower','increased','decreased'))


def test_slow_stock_complete_days_new_inactive_void_and_tenant_boundaries(app):
    user, shops = seed('plus'); other, foreign = seed('plus', email='other@invalid.test')
    shop = shops[0]; cutoff=datetime(2026,9,28); midnight=datetime(2026,10,28)
    rows={name:product(shop,name) for name in ['none','cutoff-sale','before','last','today','void','new','exact-age','inactive','empty']}
    rows['new'].created_at=cutoff+timedelta(microseconds=1)
    rows['exact-age'].created_at=cutoff
    rows['inactive'].active=False; rows['empty'].stock_quantity=0
    for name,when in [('cutoff-sale',cutoff),('before',cutoff-timedelta(microseconds=1)),
                      ('last',midnight-timedelta(microseconds=1)),('today',midnight),('void',cutoff)]:
        sale(shop,rows[name],when,void=name=='void')
    product(foreign[0], 'Foreign secret'); db.session.commit()
    result=slow_stock(shop.id,now=NOW)
    assert result['start']==cutoff and result['end']==midnight
    assert {p.name for p in result['products']} == {'none','before','today','void','exact-age'}
    # Financial dates do not alter the trailing stock review.
    for start,end in [(START,END),(datetime(2026,1,1),datetime(2026,2,1))]:
        assert {p.id for p in build_insights(shop.id,start,end,now=NOW)['stock']['products']} == {p.id for p in result['products']}


@pytest.mark.parametrize('kind,allowed', [('basic',False),('trial',False),('lifetime',False),
    ('plus',True),('pro',True),('lifetime_plus',True),('lifetime_pro',True)])
@pytest.mark.parametrize('interval', ['monthly','yearly'])
def test_plan_gate_and_navigation_preserve_reports(app, kind, allowed, interval):
    user, shops=seed(kind)
    sub=subscription(user)
    if sub: sub.billing_interval=interval
    db.session.commit()
    with patch('app.main.routes.financial_totals', wraps=financial_totals), \
         patch('app.main.insights.build_insights', wraps=build_insights) as build:
        response=request_page(app,user)
        assert response.status_code==(200 if allowed else 403)
        assert build.call_count == int(allowed)
    assert response.headers['Cache-Control']=='private, no-store'
    client=app.test_client(); login(client,user)
    html=client.get('/reports').get_data(as_text=True)
    assert ('href="/insights"' in html) is allowed
    assert 'Stock counts and inventory value reflect today' in html


@pytest.mark.parametrize('state,allowed', [('expired',False),('ends-now',False),('starts-now',True),
    ('future',False),('past_due',True),('cancelled',True),('cancel_at_period_end',True),
    ('failed',False),('suspended',False),('unverified',False),('admin',False),('dual',False)])
def test_access_boundaries_reject_before_activity_reads(app, state, allowed):
    user,shops=seed('plus'); sub=subscription(user)
    sub.current_period_start=NOW-timedelta(days=1); sub.current_period_end=NOW+timedelta(days=1)
    if state=='expired': sub.current_period_end=NOW-timedelta(seconds=1)
    elif state=='ends-now': sub.current_period_end=NOW
    elif state=='starts-now': sub.current_period_start=NOW
    elif state=='future': sub.current_period_start=NOW+timedelta(seconds=1)
    elif state=='suspended': user.suspended_at=NOW
    elif state=='unverified': user.email_verified_at=None
    elif state=='admin': user.role='admin'
    elif state=='dual': user.admin_enabled=True
    else: sub.status=state
    db.session.commit()
    with app.test_request_context('/insights'):
        assert (insights_business(user,now=NOW) is not None) is allowed
    with patch('app.main.insights.build_insights', wraps=build_insights) as build:
        response=request_page(app,user)
        assert (response.status_code==200) is allowed
        assert build.call_count==int(allowed)
        assert response.headers['Cache-Control']=='private, no-store'


def test_foreign_locked_suspended_selections_and_downgrade(app):
    user,shops=seed('plus',businesses=3); other,foreign=seed('plus',email='foreign@invalid.test')
    for shop,name in [(shops[0],'Allowed one'),(shops[1],'Allowed two'),(shops[2],'Locked secret'),(foreign[0],'Foreign secret')]:
        item=product(shop,name);sale(shop,item,START)
    db.session.commit()
    client=app.test_client();login(client,user)
    for chosen,expected in [(shops[1],shops[1]),(shops[2],shops[0]),(foreign[0],shops[0])]:
        with client.session_transaction() as state: state['active_business_id']=chosen.id
        captured=[]
        def capture(sender,template,context,**extra):
            if template.name=='insights.html':captured.append(context)
        with template_rendered.connected_to(capture,app):
            response=client.get('/insights?from=2026-10-08&to=2026-10-14')
        assert response.status_code==200 and captured[0]['business'].id==expected.id
        assert captured[0]['insight']['current']['revenue']==Decimal('10.10')
        assert b'Locked secret' not in response.data and b'Foreign secret' not in response.data
    shops[0].suspended_at=NOW;db.session.commit()
    with app.test_request_context('/insights'): assert insights_business(user,now=NOW).id==shops[1].id
    # Downgrade/expiry immediately removes premium results on a new request.
    subscription(user).plan_code='basic';db.session.commit();g.pop('_login_user',None)
    with patch('app.main.insights.build_insights', side_effect=AssertionError('Denied activity read')):
        response=client.get('/insights')
    assert response.status_code==403 and b'data-insight-results' not in response.data


@pytest.mark.parametrize('plan,limit', [('plus',2),('pro',5)])
@pytest.mark.parametrize('selection', ['missing','foreign','locked','suspended','valid-first','valid-primary'])
def test_one_business_context_for_queries_content_header_and_switcher(app,plan,limit,selection):
    user,shops=seed(plan,businesses=limit+1)
    other,foreign=seed('plus',email='context-foreign@invalid.test')
    account=db.session.get(AccountBilling,user.id)
    account.primary_business_id=shops[1].id  # primary is NOT the first permitted shop
    for index,shop in enumerate(shops):
        shop.name=f'Context business {index}'
        item=product(shop,f'Only stock for business {index}')
        sale(shop,item,START,price=str(100+index))
    foreign[0].name='Foreign business secret'
    if selection=='suspended':shops[0].suspended_at=NOW
    db.session.commit()
    chosen={'missing':None,'foreign':foreign[0].id,'locked':shops[-1].id,
            'suspended':shops[0].id,'valid-first':shops[0].id,'valid-primary':shops[1].id}[selection]
    expected=shops[0] if selection=='valid-first' else shops[1]
    expected_ids={shop.id for shop in shops[:limit] if not shop.suspended_at}
    client=app.test_client();login(client,user)
    if chosen is not None:
        with client.session_transaction() as state:state['active_business_id']=chosen
    contexts=[];statements=[]
    def capture(sender,template,context,**extra):
        if template.name=='insights.html':contexts.append(context)
    def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:
        with template_rendered.connected_to(capture,app), \
             patch('app.subscriptions.entitlements.selected_business',side_effect=AssertionError('Second business resolver')), \
             patch('app.subscriptions.entitlements.effective_access',side_effect=AssertionError('Second access evaluation')), \
             patch('app.main.insights.EntitlementSnapshot',wraps=EntitlementSnapshot) as snapshot, \
             patch('app.main.insights.financial_totals',wraps=financial_totals) as totals, \
             patch('app.main.insights.slow_stock',wraps=slow_stock) as stock:
            response=client.get('/insights?from=2026-10-08&to=2026-10-14')
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert response.status_code==200
    assert snapshot.call_count==1
    assert [call.args[0] for call in totals.call_args_list]==[expected.id,expected.id]
    assert stock.call_args.args[0]==expected.id
    context=contexts[0]
    assert context['business'] is context['subscription_business'] is expected
    assert context['business_switch_ids']==expected_ids
    assert {b.id for b in context['business_choices']}==expected_ids
    assert context['insight']['current']['revenue']==Decimal(100+shops.index(expected))
    html=response.get_data(as_text=True)
    assert f'<p class="eyebrow">{expected.name}</p>' in html
    first_strong=re.search(r'<div class="topbar-business">.*?<strong>(.*?)</strong>',html,re.S)
    assert first_strong and re.sub('<[^>]+>','',first_strong[1]).strip().endswith(expected.name)
    if len(expected_ids)>1:
        assert f'aria-label="Active business: {expected.name}. Switch business"' in html
        # Match each form separately so an earlier non-current form cannot win.
        current_forms=[form for form in re.findall(r'<form\b[^>]*>.*?</form>',html,re.S) if 'aria-current="true"' in form]
        assert len(current_forms)==1 and f'action="/businesses/{expected.id}/switch"' in current_forms[0]
    assert shops[-1].name not in html and 'Foreign business secret' not in html
    if selection=='suspended':assert shops[0].name not in html
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    with client.session_transaction() as state:
        assert state.get('active_business_id')==chosen  # render does not repair even stale cookies
    assert response.headers['Cache-Control']=='private, no-store'


@pytest.mark.parametrize('primary_state',['foreign','suspended','locked'])
def test_invalid_primary_falls_back_only_to_first_permitted_business(app,primary_state):
    user,shops=seed('plus',businesses=3)
    other,foreign=seed('plus',email='primary-foreign@invalid.test')
    account=db.session.get(AccountBilling,user.id)
    primary={'foreign':foreign[0], 'suspended':shops[1], 'locked':shops[2]}[primary_state]
    account.primary_business_id=primary.id
    if primary_state=='suspended':primary.suspended_at=NOW
    db.session.commit()
    contexts=[]
    def capture(sender,template,context,**extra):
        if template.name=='insights.html':contexts.append(context)
    with template_rendered.connected_to(capture,app):response=request_page(app,user)
    assert response.status_code==200
    assert contexts[0]['business'] is contexts[0]['subscription_business'] is shops[0]


def test_billing_label_reuses_supplied_access_without_changing_default(app):
    user,_=seed('plus')
    subscription(user).status='past_due';db.session.commit()
    access=effective_access(user,now=NOW)
    original=access_label(user)
    with patch('app.subscriptions.entitlements.effective_access',side_effect=AssertionError('Repeated access evaluation')):
        assert access_label(user,access=access)==original=='StockBridge Plus · Monthly · renewal failed'


@pytest.mark.parametrize('args,field', [({'from':'2026-10-22','to':'2026-10-20'},'to'),
    ({'from':'2026-10-01'},'to'),({'to':'2026-10-02'},'from'),
    ({'from':'0001-01-01','to':'0001-01-01'},'from'),
    ({'from':'<script>','to':'2026-10-02'},'from')])
def test_invalid_dates_accessible_preserved_no_activity_queries(app,args,field):
    user,_=seed('plus')
    with patch('app.main.insights.build_insights', side_effect=AssertionError('Invalid activity read')):
        response=request_page(app,user,period='month',**args)
    assert response.status_code==400
    html=response.get_data(as_text=True)
    assert f'aria-describedby="insight-{field}-error"' in html
    assert '<option value="custom" selected>' in html
    assert 'data-insight-results' not in html and '<script>' not in html


def test_explicit_dates_override_preset_and_correction(app):
    user,_=seed('plus');captured=[]
    def capture(sender,template,context,**extra): captured.append(context)
    with template_rendered.connected_to(capture,app):
        response=request_page(app,user,period='month',**{'from':'2026-10-08','to':'2026-10-14'})
    assert response.status_code==200
    assert captured[-1]['insight']['start']==START and captured[-1]['insight']['end']==END
    assert captured[-1]['insight']['previous_start']==datetime(2026,10,1)
    html=response.get_data(as_text=True)
    assert 'value="2026-10-08"' in html and 'value="2026-10-14"' in html
    assert 'Separate stock review, independent of the selected financial dates.' in html
    assert 'type="date"' in html and 'data-report-period' in html


@pytest.mark.parametrize('volume',[10,100])
def test_select_only_gets_bounded_queries_and_no_trial_start(app,volume):
    user,shops=seed('plus'); item=product(shops[0])
    for _ in range(volume):sale(shops[0],item,START)
    account=db.session.get(AccountBilling,user.id)
    account.trial_eligible=True
    db.session.commit();db.session.expire_all()
    statements=[]
    def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:response=request_page(app,user)
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert response.status_code==200
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    activity=[sql for sql in statements if 'FROM sale ' in sql or 'FROM expense ' in sql or 'FROM product ' in sql]
    assert len(activity)==5  # two independent totals per period + one stock query
    assert len(statements)<60  # shared navigation/context queries are bounded too
    assert db.session.get(AccountBilling,user.id).trial_started_at is None
    assert BillingEvent.query.filter_by(kind='trial_started').count()==0


def test_anonymous_disabled_and_empty_business_are_private(app):
    response=app.test_client().get('/insights')
    assert response.status_code==302 and response.headers['Cache-Control']=='private, no-store'
    user,_=seed('plus',businesses=0)
    assert request_page(app,user).status_code==403
    app.config['SUBSCRIPTIONS_ENABLED']=False
    assert request_page(app,user).status_code==403


def test_all_businesses_suspended_and_trial_eligible_denial_are_select_only(app):
    user,shops=seed('plus',businesses=2)
    for shop in shops: shop.suspended_at=NOW
    account=db.session.get(AccountBilling,user.id);account.trial_eligible=True
    db.session.commit()
    statements=[]
    def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',record)
    try:
        with patch('app.main.insights.build_insights',side_effect=AssertionError('Denied activity read')):
            response=request_page(app,user)
    finally:event.remove(db.engine,'before_cursor_execute',record)
    assert response.status_code==403
    assert all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
    assert not any('FROM sale ' in sql or 'FROM expense ' in sql or 'FROM product ' in sql for sql in statements)
    assert account.trial_started_at is None


def test_no_snapshot_reuse_on_expiry_and_slow_list_is_bounded(app):
    user,shops=seed('pro');shop=shops[0]
    for index in range(12):product(shop,f'Stock {index}')
    db.session.commit()
    assert len(slow_stock(shop.id,now=NOW)['products'])==10
    client=app.test_client();login(client,user)
    assert client.get('/insights').status_code==200
    subscription(user).current_period_end=NOW-timedelta(days=1);db.session.commit()
    with patch('app.main.insights.build_insights',side_effect=AssertionError('Expired activity read')):
        response=client.get('/insights')
    assert response.status_code==403 and b'data-insight-results' not in response.data
