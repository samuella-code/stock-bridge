"""Database-free rendering and date/series regression tests."""
import ast
from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
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
        business=SimpleNamespace(name='Example Store'), period=period,
        request=SimpleNamespace(args=args or {}), chart=chart,
        metrics=SimpleNamespace(revenue=1234, gross_profit=900, expenses=200, net_profit=700,
                                number_of_sales=sales, units_sold=4, low_stock_count=2, out_stock_count=1),
        url_for=lambda endpoint, **kwargs: '/' + endpoint,
        top=[], low_stock_products=[], recent_transactions=[])


@pytest.mark.parametrize('period', ['today', 'week', 'month', 'custom'])
def test_explicit_apply_and_selected_period(period):
    html = render(period=period)
    assert 'onchange=' not in html and 'this.form.submit' not in html
    assert '<button class="secondary-btn">Apply</button>' in html
    assert f'<option value="{period}" selected>' in html
    assert '<form class="period-picker" method="GET">' in html


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
    assert 'Sales in the last 7 days' in html
    assert 'independent of the reporting period selected above (UTC)' in html
    assert '<div class="empty-chart" aria-hidden="true">' in html
    assert '<dl class="daily-revenue">' in html
    assert html.count('<dt><time datetime=') == 7
    assert '<time datetime="2026-10-04">04 Oct 2026</time></dt><dd>₦2,300.00</dd>' in html
    assert html.count('<dd>₦0.00</dd>') == 6


@pytest.mark.parametrize('sales,revenue,empty', [(0, 2300, False), (3, 0, True), (0, 0, True)])
def test_empty_state_uses_chart_not_selected_period(sales, revenue, empty):
    html = render(sales=sales, revenue=revenue)
    assert ('No sales recorded in the last 7 days.' in html) is empty


def test_summary_values_remain_independent_of_chart():
    html = render(sales=9, revenue=2300)
    for value in ['₦1,234', '₦900', '₦200', '₦700', '<strong>9</strong>']:
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
    assert html.count('class="chart-label-desktop"') == 7
    assert html.count('class="chart-label-mobile"') == 7
    for day in range(1, 8):
        assert f'<small class="chart-label-mobile">{day:02d}</small>' in html
    assert '<small class="chart-label-desktop">Sun</small>' in html
    assert '<time datetime="2026-10-04">04 Oct 2026</time></dt><dd>₦2,300.00</dd>' in html
    assert '<div class="empty-chart" aria-hidden="true">' in html


def test_chart_label_css_scopes_phone_switch_and_prevents_wrapping():
    css = (ROOT / 'app/static/css/customer.css').read_text()
    assert '.customer-app .real-chart small{white-space:nowrap;overflow-wrap:normal}' in css
    assert '.customer-app .real-chart .chart-label-mobile{display:none}' in css
    assert """@media(max-width:600px){
  .customer-app .real-chart .chart-label-desktop{display:none}
  .customer-app .real-chart .chart-label-mobile{display:block}
}""" in css
