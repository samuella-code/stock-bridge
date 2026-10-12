import pytest
from pathlib import Path
from markupsafe import escape
from jinja2 import Environment, FileSystemLoader
from tests.test_business_workflows import client, owner
from app import db
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def test_help_is_public_read_only_and_links_to_existing_workflows(client):
    response = client.get('/help')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for anchor in ('getting-started', 'products', 'inventory', 'sales', 'restocking',
                   'adjustments', 'expenses', 'reports', 'interrupted', 'businesses'):
        assert f'id="{anchor}"' in html and f'href="#{anchor}"' in html
    assert 'Official contact details remain pending' in html
    assert '<form' not in html
    assert client.post('/help').status_code in (400, 405)
    with patch.object(db.session, 'execute', side_effect=AssertionError('DB lookup')):
        assert client.get('/help').status_code == 200


def test_help_authenticated_and_always_discoverable(client):
    owner(client)
    assert client.get('/help').status_code == 200
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'Help Centre' in html and 'href="/help"' in html
    assert 'only when they happen' in html
    assert 'Review your inventory' in html


def test_shared_navigation_icons_are_decorative_and_keep_active_state():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True)
    for label in ('Dashboard', 'Products', 'Inventory', 'Sales', 'Restocking', 'Expenses',
                  'Notifications', 'Reports', 'My Businesses', 'Billing & Plan', 'Profile & settings', 'Help Centre'):
        html = env.from_string("{% from 'components/ui.html' import nav_link %}{{ nav_link(label, '/safe', true, 'unused') }}").render(label=label)
        assert '<svg' in html and 'stroke="currentColor"' in html
        assert 'aria-hidden="true"' in html and 'focusable="false"' in html
        assert 'aria-current="page"' in html and 'href="/safe"' in html
        assert f'<span>{escape(label)}</span>' in html


def test_plus_connection_is_separate_from_business_cards(client):
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    html = client.get('/').get_data(as_text=True)
    assert 'pair-connection' in html and '↔' not in html
    assert 'Example Mini Mart' in html and 'Example Fashion Shop' in html
    assert 'Its stock. Its sales. Its reports.' in html
    css = (ROOT / 'app/static/css/entry.css').read_text()
    assert '.business-pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))' in css
    assert 'grid-template-columns:minmax(0,1fr) 24px minmax(0,1fr)' in css
    assert 'overflow-wrap:anywhere' in css


@pytest.mark.parametrize('visitor', ['anonymous', 'unverified', 'verified'])
def test_help_never_starts_trial_or_commits(client, visitor):
    from app.models import Business, User, AccountBilling, BillingEvent
    if visitor != 'anonymous':
        bid = owner(client)
        user = db.session.get(Business, bid).owner
        if visitor == 'unverified':
            user.email_verified_at = None
        db.session.add(AccountBilling(user_id=user.id, primary_business_id=bid, trial_eligible=True))
        db.session.commit()
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    before = (AccountBilling.query.count(), BillingEvent.query.count())
    with patch('app.subscriptions.entitlements.start_trial', side_effect=AssertionError('Trial side effect')), \
         patch.object(db.session(), 'commit', side_effect=AssertionError('Unexpected commit')):
        assert client.get('/help').status_code == 200
    assert (AccountBilling.query.count(), BillingEvent.query.count()) == before
    assert all(a.trial_started_at is None for a in AccountBilling.query.all())


def test_normal_route_still_starts_eligible_trial(client):
    from app.models import Business, AccountBilling, BillingEvent
    bid = owner(client)
    user = db.session.get(Business, bid).owner
    db.session.add(AccountBilling(user_id=user.id, primary_business_id=bid, trial_eligible=True))
    db.session.commit()
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    assert client.get('/help').status_code == 200
    assert AccountBilling.query.one().trial_started_at is None
    assert client.get('/dashboard').status_code == 200
    account = AccountBilling.query.one()
    assert account.trial_started_at is not None and account.trial_ends_at is not None
    assert BillingEvent.query.filter_by(kind='trial_started').count() == 1
    started = account.trial_started_at
    assert client.get('/dashboard').status_code == 200
    assert AccountBilling.query.one().trial_started_at == started
    assert BillingEvent.query.filter_by(kind='trial_started').count() == 1


def test_help_navigation_reuses_responsive_focus_styles(client):
    html = client.get('/help').get_data(as_text=True)
    assert 'class="policy-navigation"' in html and 'class="policy-links"' in html
    css = (ROOT / 'app/static/css/legal.css').read_text()
    assert 'flex-wrap:wrap' in css and '.legal-public a:focus-visible' in css


def test_page_header_caller_actions_and_help_panel_are_independent():
    from jinja2 import StrictUndefined
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'),
                      autoescape=True, undefined=StrictUndefined)
    env.globals['url_for'] = lambda endpoint: '/help'
    html = env.from_string("{% from 'components/ui.html' import page_header %}{% call page_header('Products', 'Manage stock') %}<a href='/products/new'>Add product</a>{% endcall %}").render()
    assert '<h2>Products</h2>' in html and 'Manage stock' in html
    assert 'class="page-actions"' in html and "href='/products/new'" in html
    assert '/help' not in html and 'guide' not in html
    plain = env.from_string("{% from 'components/ui.html' import page_header %}{{ page_header('Products') }}").render()
    assert 'page-actions' not in plain
    source = (ROOT / 'app/templates/components/ui.html').read_text()
    header = source.split('{% macro page_header(', 1)[1].split('{%- endmacro %}', 1)[0]
    assert 'topic' not in header
    help_html = env.from_string("{% from 'components/ui.html' import help_panel %}{% call help_panel('Sales') %}<p>Sales help</p>{% endcall %}").render()
    assert 'href="/help#sales"' in help_html and 'Read the Sales guide' in help_html
