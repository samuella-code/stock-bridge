"""Reports-only date validation with disposable SQLite and no external services."""
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from flask import template_rendered
from markupsafe import escape

from app import db
from app.models import Expense, Product, Restock, Sale, SaleItem
from tests.test_multi_business import app, seed, login


def authenticated_client(app):
    user, shops = seed('plus')
    client = app.test_client(); login(client, user)
    return client, shops[0]


INVALID = [
    ({'from':'2026-10-23', 'to':'2026-10-01'}, 'to', 'To date must be on or after the From date.'),
    ({'to':'2026-10-01'}, 'from', 'Enter a From date.'),
    ({'from':'2026-10-01'}, 'to', 'Enter a To date.'),
    ({}, 'from', 'Enter a From date.'),
    ({'from':'', 'to':'2026-10-01'}, 'from', 'Enter a From date.'),
    ({'from':'2026-10-01', 'to':''}, 'to', 'Enter a To date.'),
    ({'from':'01/10/2026', 'to':'2026-10-02'}, 'from', 'YYYY-MM-DD format'),
    ({'from':'2026-10-01', 'to':'not-a-date'}, 'to', 'YYYY-MM-DD format'),
    ({'from':'2026-1-01', 'to':'2026-10-02'}, 'from', 'YYYY-MM-DD format'),
    ({'from':'2026-02-30', 'to':'2026-03-01'}, 'from', 'real calendar date for From'),
    ({'from':'2026-10-01', 'to':'2026-13-01'}, 'to', 'real calendar date for To'),
    ({'from':'0000-01-01', 'to':'0000-01-02'}, 'from', 'real calendar date for From'),
    ({'from':'2026-01-01', 'to':'2027-01-03'}, 'to', 'no more than 366 days'),
    ({'from':'9999-12-31', 'to':'9999-12-31'}, 'to', 'earlier than 9999-12-31'),
    ({'from':'<script>alert(1)</script>', 'to':'2026-10-02'}, 'from', 'YYYY-MM-DD format'),
]


@pytest.mark.parametrize('period,values,field,message', [
    (period, values, field, message)
    for period in ('today', 'week', 'month', 'custom')
    for values, field, message in INVALID
    if period == 'custom' or any(values.values())
])
def test_invalid_custom_dates_preserve_input_and_hide_all_results(app, values, field, message, period):
    client, _ = authenticated_client(app)
    with patch('app.main.routes.figures', side_effect=AssertionError('Invalid report queried figures')), \
         patch('app.main.routes.top_products', side_effect=AssertionError('Invalid report queried products')):
        response = client.get('/reports', query_string={'period':period, **values})
    assert response.status_code == 400 and 'Location' not in response.headers
    html = response.get_data(as_text=True)
    assert '<option value="custom" selected>' in html
    assert 'role="alert" aria-labelledby="report-date-error-title"' in html
    assert 'No report has been generated.' in html and message in html
    assert f'href="#report-{field}"' in html
    assert f'aria-invalid="true" aria-describedby="report-{field}-error"' in html
    for key in ('from', 'to'):
        assert f'name="{key}" value="{escape(values.get(key, ""))}"' in html
    if values.get(field):
        assert f'Entered value: {escape(values[field])}' in html
    assert '<script>alert(1)</script>' not in html
    for forbidden in ('data-report-results', 'class="metric-grid"', 'class="inventory-summary"',
                      'Best-selling by units', 'Best-selling by revenue',
                      'Products with no sales in the last 30 days', 'No sales in this period.'):
        assert forbidden not in html


@pytest.mark.parametrize('start,end', [
    ('2026-10-01', '2026-10-01'),
    ('2024-02-29', '2024-03-01'),
    ('2026-01-01', '2027-01-02'),  # Existing maximum difference: 366 days.
    ('9999-12-30', '9999-12-30'),
])
@pytest.mark.parametrize('period', ['today', 'week', 'month', 'custom'])
def test_valid_empty_report_is_200_with_zero_totals(app, start, end, period):
    client, _ = authenticated_client(app)
    contexts = []
    def capture(sender, template, context, **extra):
        if template.name == 'reports.html': contexts.append(context)
    with template_rendered.connected_to(capture, app):
        response = client.get('/reports', query_string={'period':period,'from':start,'to':end})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-report-results' in html and 'No sales in this period.' in html
    assert 'No report has been generated.' not in html and 'aria-invalid="true"' not in html
    assert all(value == 0 for value in contexts[0]['metrics'].values())
    assert contexts[0]['period'] == 'custom'
    assert '<option value="custom" selected>' in html
    assert contexts[0]['start'] == datetime.fromisoformat(start)
    assert contexts[0]['end'] == datetime.fromisoformat(end)


@pytest.mark.parametrize('period', ['today', 'week', 'month'])
def test_existing_preset_reports_remain_valid(app, period):
    client, _ = authenticated_client(app)
    response = client.get('/reports', query_string={'period':period, 'from':'', 'to':''})
    assert response.status_code == 200 and b'data-report-results' in response.data


@pytest.mark.parametrize('period', ['today', 'week', 'month', 'custom'])
def test_correcting_invalid_dates_restores_inclusive_financial_results(app, period):
    client, business = authenticated_client(app)
    product = Product(business_id=business.id, name='Synthetic report product', stock_quantity=9,
                      buying_price=300, selling_price=500)
    db.session.add(product); db.session.flush()
    for when, qty, price, cost in [
        (datetime(2026,9,30,23,59,59,999999), 100, 999, 1),
        (datetime(2026,10,1), 2, 500, 300),
        (datetime(2026,10,2,23,59,59,999999), 3, 200, 100),
        (datetime(2026,10,3), 100, 999, 1),
    ]:
        sale = Sale(business_id=business.id, sold_at=when)
        db.session.add(sale); db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id, product_id=product.id, quantity=qty,
                                unit_price=price, unit_cost=cost))
    db.session.add(Expense(business_id=business.id, description='Synthetic expense',
                          amount=150, spent_at=datetime(2026,10,2,23,59,59)))
    db.session.add(Restock(business_id=business.id, product_id=product.id,
                          quantity=4, unit_cost=250, received_at=datetime(2026,10,2,23,59,59)))
    db.session.commit()
    assert client.get('/reports?period=custom&from=2026-10-02&to=2026-10-01').status_code == 400
    contexts = []
    def capture(sender, template, context, **extra):
        if template.name == 'reports.html': contexts.append(context)
    with template_rendered.connected_to(capture, app):
        response = client.get('/reports', query_string={'period':period, 'from':'2026-10-01', 'to':'2026-10-02'})
    assert response.status_code == 200
    metrics = contexts[0]['metrics']
    assert {key:metrics[key] for key in ('number_of_sales','units_sold','revenue','cogs',
        'gross_profit','expenses','net_profit','inventory_purchased','inventory_value')} == {
        'number_of_sales':2,'units_sold':5,'revenue':Decimal('1600'),'cogs':Decimal('900'),
        'gross_profit':Decimal('700'),'expenses':Decimal('150'),'net_profit':Decimal('550'),
        'inventory_purchased':Decimal('1000'),'inventory_value':Decimal('2700')}
    assert b'1,600.00' in response.data and b'550.00' in response.data
    assert Sale.query.count() == 4 and Expense.query.count() == 1 and Restock.query.count() == 1


@pytest.mark.parametrize('values', [{'from':'2026-10-23','to':'2026-10-01'}, {}])
def test_dashboard_invalid_date_redirect_is_unchanged(app, values):
    client, _ = authenticated_client(app)
    response = client.get('/dashboard', query_string={'period':'custom', **values})
    assert response.status_code == 302 and response.headers['Location'].endswith('/dashboard')
    with client.session_transaction() as session:
        assert ('error','Choose a valid reporting period.') in session['_flashes']


def test_report_errors_do_not_replace_authentication(app):
    response = app.test_client().get('/reports?period=custom&from=bad')
    assert response.status_code == 302 and '/auth/login' in response.headers['Location']


@pytest.mark.parametrize('period', ['today', 'week', 'month', 'custom'])
def test_exact_staging_reversed_dates_are_rejected_before_calculation(app, period):
    client, _ = authenticated_client(app)
    with patch('app.main.routes.figures', side_effect=AssertionError('Must not calculate')), \
         patch('app.main.routes.top_products', side_effect=AssertionError('Must not query')):
        response = client.get('/reports', query_string={
            'period':period, 'from':'2026-10-22', 'to':'2026-10-20'})
    assert response.status_code == 400
    assert b'To date must be on or after the From date.' in response.data
    assert b'data-report-results' not in response.data
    assert b'value="2026-10-22"' in response.data
    assert b'value="2026-10-20"' in response.data


def test_reports_load_scoped_date_controls(app):
    client, _ = authenticated_client(app)
    response = client.get('/reports')
    assert b'data-report-period' in response.data
    assert b'js/report-dates.js' in response.data
    dashboard = client.get('/dashboard')
    assert b'data-report-period' not in dashboard.data
    assert b'js/report-dates.js' not in dashboard.data
