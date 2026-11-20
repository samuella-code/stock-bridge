"""Database-free rendering and date/series regression tests."""
import ast
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.presentation_helpers import field, nodes, text
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


def helper(name):
    tree = ast.parse((ROOT / 'app/main/routes.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = dict(datetime=datetime, time=time, timedelta=timedelta)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<dashboard helper>', 'exec'), namespace)
    return namespace[name]


def render(period='today', sales=0, revenue=0, args=None):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{% block content %}{% endblock %}',
                    'components/setup.html': '',
                    'components/business_avatar.html': '{% macro business_avatar(b) %}{% endmacro %}'}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=select_autoescape())
    chart = helper('sales_chart')([(datetime(2026, 10, 4).date(), revenue)], datetime(2026, 10, 1))
    return env.get_template('dashboard.html').render(
        business=SimpleNamespace(name='Example Store'), current_user=SimpleNamespace(full_name='Example Owner'), period=period,
        request=SimpleNamespace(args=args or {}), chart=chart,
        metrics=SimpleNamespace(revenue=1234, gross_profit=900, expenses=200, net_profit=700,
                                number_of_sales=sales, units_sold=4, low_stock_count=2, out_stock_count=1, inventory_value=5000),
        url_for=lambda endpoint, **kwargs: '/' + endpoint,
        top=[], low_stock_products=[], recent_transactions=[])


@pytest.mark.parametrize('period', ['today', 'week', 'month', 'custom'])
def test_explicit_apply_and_selected_period(period):
    html = render(period=period)
    assert 'onchange=' not in html and 'this.form.submit' not in html
    assert field(html, 'period')['value']=='custom'
    assert any(a.get('type')=='submit' for a in nodes(html,'button'))
    assert len(nodes(html,'form',method='GET'))==1
    assert 'aria-label="Reporting period"' in html
    if period in ('today','month'):
        assert any(a.get('aria-current')=='true' for a in nodes(html,'a'))



def test_custom_dates_persist_and_validate():
    args = {'period': 'custom', 'from': '2026-10-01', 'to': '2026-10-04'}
    html = render('custom', args=args)
    assert 'name="from" value="2026-10-01"' in html
    assert 'name="to" value="2026-10-04"' in html
    assert helper('period_dates')(args) == ('custom', datetime(2026, 10, 1), datetime(2026, 10, 5))
    with pytest.raises(ValueError):
        helper('period_dates')({**args, 'to': '2026-09-30'})
    with pytest.raises(KeyError):
        helper('period_dates')({'period': 'custom'})


def test_accessible_daily_values_and_period_label():
    html = render(revenue=2300)
    assert 'Sales over the last 7 days' in html
    days=nodes(html,'button')
    days=[a for a in days if 'data-dashboard-chart-day' in a]
    assert len(days)==7
    assert any(a['aria-label']=='04/10/2026: ₦2,300' for a in days)
    assert sum(a['aria-label'].endswith(': ₦0') for a in days)==6
    assert 'class="daily-revenue"' not in html and 'UTC' not in html



@pytest.mark.parametrize('sales,revenue,empty', [(0, 2300, False), (3, 0, True), (0, 0, True)])
def test_empty_state_uses_chart_not_selected_period(sales, revenue, empty):
    html = render(sales=sales, revenue=revenue)
    assert ('No sales yet. Record a sale and it will show here.' in html) is empty


def test_summary_values_remain_independent_of_chart():
    html = render(sales=9, revenue=2300)
    for value in ['₦1,234', '₦700', '₦5,000', '<dd>2</dd>']:
        assert value in html


@pytest.mark.parametrize('as_string', [False, True])
def test_chart_dates_preserve_totals_and_heights(as_string):
    day = datetime(2026, 10, 4).date()
    chart = helper('sales_chart')([(day.isoformat() if as_string else day, 2300)], datetime(2026, 10, 1))
    assert len(chart) == 7
    assert chart[3] == {'date': day, 'label': 'Sun', 'total': 2300.0, 'height': 100}
    assert sum(row['total'] for row in chart) == 2300
    assert all(row['height'] == 3 for i, row in enumerate(chart) if i != 3)


def test_compact_phone_chart_labels_preserve_desktop_and_accessible_dates():
    html = render(revenue=2300)
    days=[a for a in nodes(html,'button') if 'data-dashboard-chart-day' in a]
    assert len(days)==7
    for day in range(1,8):
        assert any(a['aria-label'].startswith(f'{day:02d}/10/2026:') for a in days)
    assert '04/10/2026: ₦2,300' in html
    assert 'data-dashboard-chart-value' in html



def test_chart_label_css_scopes_phone_switch_and_prevents_wrapping():
    css = (ROOT / 'app/static/css/customer.css').read_text()
    assert '.customer-app .real-chart small{white-space:nowrap;overflow-wrap:normal}' in css
    assert '.customer-app .real-chart .chart-label-mobile{display:none}' in css
    assert """@media(max-width:600px){
  .customer-app .real-chart .chart-label-desktop{display:none}
  .customer-app .real-chart .chart-label-mobile{display:block}
}""" in css
