"""Pricing copy only, rendered with synthetic values and no application/DB imports."""
import ast
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from jinja2 import ChoiceLoader,DictLoader,Environment,FileSystemLoader,StrictUndefined

ROOT=Path(__file__).resolve().parents[1]
PRICES={'basic':NS(monthly=3000,yearly=30000),'plus':NS(monthly=5000,yearly=50000),'pro':NS(monthly=10000,yearly=100000)}


class Node:
    def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[];self.parts=[]
    def text(self):return ' '.join(' '.join(p.text() if isinstance(p,Node) else p for p in self.parts).split())
    def find(self,tag):return [n for c in self.children for n in ([c] if c.tag==tag else [])+c.find(tag)]


class DOM(HTMLParser):
    def __init__(self,html):
        super().__init__();self.root=Node();self.stack=[self.root];self.feed(html)
    def handle_starttag(self,tag,attrs):
        node=Node(tag,attrs);self.stack[-1].children.append(node);self.stack[-1].parts.append(node)
        if tag not in ('input','meta','link','br','hr','img'):self.stack.append(node)
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,0,-1):
            if self.stack[i].tag==tag:self.stack=self.stack[:i];break
    def handle_data(self,data):self.stack[-1].parts.append(data)


def environment():
    env=Environment(loader=ChoiceLoader([DictLoader({'base.html':'{% block content %}{% endblock %}'}),FileSystemLoader(ROOT/'app/templates')]),autoescape=True,undefined=StrictUndefined)
    env.globals.update(url_for=lambda endpoint,**kw:'#synthetic',csrf_token=lambda:'synthetic-inert')
    return env


def context(enabled=True,access=None):
    return dict(config=NS(SUBSCRIPTIONS_ENABLED=enabled),plan_prices=PRICES,access=access,
        provider_ready=False,history=[],events=[],account=NS(trial_ends_at=None),billing_label='Synthetic plan')


def cards(html):return [n for n in DOM(html).root.find('article') if 'plan-card' in n.attrs.get('class','').split()]


@pytest.mark.parametrize('page',['home.html','subscriptions/billing.html'])
@pytest.mark.parametrize('enabled',[False,True])
def test_card_features_match_available_tiers_and_preserve_all_prices_limits(page,enabled):
    html=environment().get_template(page).render(**context(enabled))
    plans=cards(html);assert len(plans)==3
    for card,(plan,prices) in zip(plans,PRICES.items()):
        text=card.text()
        assert ('Business Insights for the selected business' in text)==(plan in ('plus','pro'))
        assert ('Multi-business Overview across permitted businesses' in text)==(plan=='pro')
        assert ('1 business' if plan=='basic' else 'Up to 2 businesses' if plan=='plus' else 'Up to 5 businesses') in text
        price=next(n for n in card.find('p') if 'data-price-monthly' in n.attrs)
        assert price.attrs['data-price-monthly']==f'₦{prices.monthly:,.0f}/month'
        assert price.attrs['data-price-yearly']==f'₦{prices.yearly:,.0f}/year'
        assert f'Save ₦{prices.monthly*12-prices.yearly:,.0f}/year' in text
        assert 'Staff Access' not in text and 'export' not in text.lower()


def test_comparison_has_accessible_feature_rows_with_correct_basic_plus_pro_values():
    html=environment().get_template('home.html').render(**context())
    table=DOM(html).root.find('table')[0]
    rows={r.find('th')[0].text():r for r in table.find('tr') if r.find('th') and r.find('td')}
    for feature,values in [('Business Insights',['Not included','Included','Included']),('Multi-business Overview',['Not included','Not included','Included']),('Businesses',['1','Up to 2','Up to 5'])]:
        row=rows[feature];assert row.find('th')[0].attrs['scope']=='row'
        assert [n.text() for n in row.find('td')]==values
    assert [n.text() for n in table.find('thead')[0].find('th')]==['Included','Basic','Plus','Pro']
    assert all(n.attrs['scope']=='col' for n in table.find('thead')[0].find('th'))
    for feature in ('Products','Inventory','Sales','Restocking','Expenses','Dashboard','Reports'):
        assert [n.text() for n in rows[feature].find('td')]==['Included']*3
    assert 'role="region" aria-label="Basic, Plus and Pro comparison" tabindex="0"' in html


@pytest.mark.parametrize('legacy',[False,True])
def test_billing_uses_shared_descriptions_and_keeps_review_form_values(legacy):
    access=NS(subscription=None,legacy=legacy,business_limit=1,kind='legacy_lifetime' if legacy else 'basic',can_write=True)
    html=environment().get_template('subscriptions/billing.html').render(**context(access=access))
    for card,(plan,prices) in zip(cards(html),PRICES.items()):
        assert ('Business Insights' in card.text())==(plan!='basic')
        plans=[n.attrs['value'] for n in card.find('input') if n.attrs.get('name')=='plan']
        assert plans==([] if legacy and plan=='basic' else [plan])
        options=[n.attrs['value'] for n in card.find('option')]
        assert options==([] if legacy and plan=='basic' else ['monthly','yearly'])
    if legacy:assert 'Your existing account access is preserved forever.' in html


def test_trial_and_disabled_checkout_wording_are_preserved():
    env=environment();home=env.get_template('home.html').render(**context())
    for sentence in ('7-day free trial · No card required','Your trial begins after email verification when eligible.',
                     '7 days free for eligible verified accounts. No card required to start. Existing valid account access remains protected.'):
        assert sentence in home
    billing=env.get_template('subscriptions/billing.html').render(**context())
    assert 'Create an account and verify your email to start a 7-day trial. No card required.' in billing
    assert 'Subscription checkout is not enabled in this environment yet.' in billing
    assert 'Subscription checkout is not currently available.' in env.get_template('home.html').render(**context(False))
    note=env.from_string("{% from 'components/public.html' import trial_note with context %}{{ trial_note() }}").render(**context())
    assert note=='Create an account and verify your email to start your 7-day free trial. No card required.'


def test_no_unavailable_features_promised_and_plan_definitions_unchanged():
    html=environment().get_template('home.html').render(**context())
    for phrase in ('Staff Access','Smart Restock','exports','forecast','AI-powered'):assert phrase not in html
    source=ast.parse((ROOT/'app/subscriptions/entitlements.py').read_text())
    plans=ast.literal_eval(next(n.value for n in source.body if isinstance(n,ast.Assign) and n.targets[0].id=='PLANS'))
    assert len(plans)==6
    for plan,prices in PRICES.items():
        for interval in ('monthly','yearly'):
            assert plans[(plan,interval)]['amount']==getattr(prices,interval)*100
            assert plans[(plan,interval)]['limit']=={'basic':1,'plus':2,'pro':5}[plan]
