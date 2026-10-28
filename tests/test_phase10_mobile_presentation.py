"""Database-free presentation contracts. No claim of browser/iPhone layout proof."""
from pathlib import Path
from types import SimpleNamespace as NS
from html.parser import HTMLParser

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def environment():
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html':'{% block content %}{% endblock %}'}),
        FileSystemLoader(ROOT/'app/templates')]), autoescape=True)
    env.globals.update(url_for=lambda endpoint, **kw: '#synthetic', csrf_token=lambda:'synthetic')
    return env


def context():
    page=NS(items=[],page=1,pages=1,has_prev=False,has_next=False)
    return dict(request=NS(form={},args={},endpoint='main.index'),
        business=NS(name='Synthetic',has_write_access=True), total=0,profit=0,
        sales=page,expenses=page,restocks=page,product_page=page,query='',recommendations=[],
        categories=['Transport'],payment_methods=['Cash'],has_products=False,submitted_items=[],
        submission_token='synthetic',config=NS(SUBSCRIPTIONS_ENABLED=True,BILLING_PROVIDER_ENABLED=False),
        plan_prices={'basic':NS(monthly=3000,yearly=30000),
                     'plus':NS(monthly=5000,yearly=50000),'pro':NS(monthly=10000,yearly=100000)})


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__();self.fields=[];self.feed(html)
    def handle_starttag(self,tag,attrs):
        if tag=='input': self.fields.append(dict(attrs))


@pytest.mark.parametrize('view,name', [('sales/index.html','sold_at'),('expenses/index.html','spent_at')])
@pytest.mark.parametrize('value',['','2026-10-28','invalid-retained-value'])
def test_native_optional_date_form_contract(view,name,value):
    ctx=context();ctx['request'].form={name:value}
    html=environment().get_template(view).render(**ctx)
    field=next(f for f in Inputs(html).fields if f.get('name')==name)
    assert field['type']=='date' and field['value']==value
    assert 'required' not in field
    if name=='sold_at': assert field['aria-describedby']=='sale-date-help'
    assert 'name="submission_token"' in html and 'name="csrf_token"' in html


def test_transaction_style_is_scoped_and_preserves_reports_rule():
    css=(ROOT/'app/static/css/customer.css').read_text()
    for selector in ('#sale-form input[type="date"]','#expense-form input[type="date"]',
                     '[data-report-period] input[type="date"]'):
        assert selector in css
    assert '-webkit-appearance:none;appearance:none;box-sizing:border-box;' in css
    assert 'overflow:hidden' not in css[css.index('/* Transaction dates'):]


@pytest.mark.parametrize('authenticated,endpoint,marker', [
    (False,'main.index','home'),(False,'auth.login',None),
    (True,'sales.index',None),(True,'expenses.index',None),(True,'main.reports','reports')])
def test_assistant_scope_and_closed_mobile_flow(authenticated,endpoint,marker):
    ctx=context();ctx.update(current_user=NS(is_authenticated=authenticated),request=NS(endpoint=endpoint))
    html=environment().get_template('components/assistant.html').render(**ctx)
    assert (f'data-assistant-page="{marker}"' in html) if marker else ('data-assistant-page=' not in html)
    css=(ROOT/'app/static/css/assistant.css').read_text()
    assert '.customer-app .sb-assistant:not([data-assistant-open])' in css
    assert '.sb-assistant[data-assistant-page="home"]:not([data-assistant-open])' in css
    assert 'position:static;margin:12px 16px;display:flex;justify-content:flex-end;' in css
    assert '.sb-assistant{position:fixed;' in css
    assert '.sb-assistant-history{min-height:60px;overflow-y:auto;' in css


def test_disclosure_hooks_and_design_system_keep_native_semantics():
    paths=('products/basket.html','restocking/index.html','subscriptions/billing.html')
    for path in paths:
        text=(ROOT/'app/templates'/path).read_text()
        assert ('basket-browse' if path=='products/basket.html' else 'ui-disclosure') in text
        assert '<summary' in text and '</details>' in text
    css=(ROOT/'app/static/css/style.css').read_text()
    for fragment in ('summary::-webkit-details-marker{display:none}', 'summary::marker{content:""}',
                     ':is(.ui-disclosure,.basket-browse)[open]>summary::before{transform:rotate(90deg)}'):
        assert fragment in css
    assert "border-left:6px solid #111827" in css
    customer=(ROOT/'app/static/css/customer.css').read_text()
    assert '.customer-app :is(#sale-form,#restock-form) details.basket-browse>summary{padding-left:30px' in customer
    # Existing help arrows are retained; action-menu/business-switch icons are not replaced.
    assert '.context-help[open] > summary::before' in css
    assert '<summary aria-label="Actions for sale' in (ROOT/'app/templates/sales/index.html').read_text()


def test_home_comparison_retains_table_relationships_and_values():
    html=environment().get_template('home.html').render(**context())
    for value in ('<thead>','<tbody>','scope="row"','scope="col"','Basic','Plus','Pro',
                  'Up to 2','Up to 5','₦3,000','₦5,000','₦10,000'):
        assert value in html
    assert 'role="region" aria-label="Basic, Plus and Pro comparison" tabindex="0"' in html
    css=(ROOT/'app/static/css/entry.css').read_text()
    assert '.entry-home .plan-comparison table{min-width:38rem;overflow-wrap:normal;word-break:normal}' in css
    assert 'hyphens:none' in css and 'overflow-x:auto' in css
