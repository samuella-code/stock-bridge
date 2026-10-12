"""Assistant presentation checks using synthetic template context and existing test fixtures."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from tests.test_business_workflows import client, owner

ROOT = Path(__file__).resolve().parents[1]


def render_component(subscriptions=False, provider=False, authenticated=False):
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True, undefined=StrictUndefined)
    env.globals['url_for'] = lambda endpoint, **kwargs: '/static/' + kwargs['filename'] if endpoint == 'static' else '/fixed/' + endpoint
    return env.get_template('components/assistant.html').render(
        config=SimpleNamespace(SUBSCRIPTIONS_ENABLED=subscriptions, BILLING_PROVIDER_ENABLED=provider),
        current_user=SimpleNamespace(is_authenticated=authenticated))


@pytest.mark.parametrize('subscriptions,provider', [(False,False),(False,True),(True,False),(True,True)])
def test_component_has_only_public_rollout_settings_and_fixed_links(subscriptions, provider):
    html = render_component(subscriptions, provider)
    data = json.loads(re.search(r'data-assistant-config>(.*?)</script>', html, re.S).group(1))
    assert set(data) == {'subscriptions','provider','scope','links'}
    assert data['subscriptions'] is subscriptions and data['provider'] is provider
    assert all(url.startswith('/fixed/') for url in data['links'].values())
    assert 'Automated guided help' in html and 'not generative AI' in html
    assert '<form' not in html and 'type="submit"' not in html


def test_keyboard_labels_and_decorative_icon():
    html = render_component()
    assert 'aria-controls="sb-assistant-panel"' in html
    assert 'aria-labelledby="sb-assistant-title"' in html
    assert 'for="sb-assistant-question"' in html and 'maxlength="300"' in html
    for label in ('Minimize assistant','Close assistant','Conversation','Suggested questions'):
        assert f'aria-label="{label}"' in html
    assert 'aria-live="polite"' in html and 'focusable="false"' in html


@pytest.mark.parametrize('path', ['/', '/auth/signup', '/auth/login', '/help', '/auth/forgot-password'])
def test_public_supported_pages_include_assistant(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert 'data-assistant-config' in response.get_data(as_text=True)


def test_customer_pages_include_assistant(client):
    owner(client)
    for path in ('/dashboard','/products/','/products/?view=inventory','/sales/','/restocking/','/expenses/','/notifications/'):
        response=client.get(path)
        assert response.status_code == 200
        assert 'data-assistant-config' in response.get_data(as_text=True)


def test_admin_callback_and_business_safety_scope():
    base=(ROOT/'app/templates/base.html').read_text()
    assert "request.blueprint != 'admin'" in base
    assert "'auth.social_callback'" in base and "'auth.apple_complete'" in base
    assert 'assistant' not in (ROOT/'app/templates/admin/base.html').read_text()
    assert 'assistant' not in (ROOT/'app/templates/auth/apple_return.html').read_text()
    js=(ROOT/'app/static/js/assistant.js').read_text()
    for forbidden in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'localStorage', 'innerHTML', '.submit(', 'formData', 'document.cookie'):
        assert forbidden not in js
    assert 'sessionStorage' in js and "topics:ids.slice(-LIMIT)" in js


def test_prices_match_authoritative_plan_configuration():
    from app.subscriptions.entitlements import PLANS
    js=(ROOT/'app/static/js/assistant.js').read_text()
    for value in PLANS.values():
        assert f"₦{value['amount']//100:,}" in js
    assert 'No card is required' in js and 'Not every account is eligible' in js


def test_mobile_rules_are_scoped_and_navigation_takes_precedence():
    css=(ROOT/'app/static/css/assistant.css').read_text()
    assert 'calc(100vw - 24px)' in css and 'env(safe-area-inset-bottom)' in css
    assert 'body.navigation-open .sb-assistant{visibility:hidden}' in css
    assert 'min-height:44px' in css and 'prefers-reduced-motion:reduce' in css
    assert 'padding-bottom:calc(80px' in css


def test_admin_login_is_not_given_customer_assistant(client):
    response=client.get('/admin/login')
    assert response.status_code == 200
    assert 'data-assistant' not in response.get_data(as_text=True)
