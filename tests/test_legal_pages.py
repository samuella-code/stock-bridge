"""Public draft policies do not enable billing or change account requirements."""
import pytest
from test_multi_business import app, seed, login
from app.subscriptions.entitlements import PLANS

PAGES = {'/privacy': 'Privacy Policy', '/terms': 'Terms of Service',
         '/refund-policy': 'Refund and Cancellation Policy',
         '/subscription-terms': 'Subscription and Billing Terms', '/contact': 'Contact and Support'}

@pytest.mark.parametrize('path,title', PAGES.items())
@pytest.mark.parametrize('enabled', [False, True])
def test_public_draft_policy_routes(app, path, title, enabled):
    app.config['SUBSCRIPTIONS_ENABLED'] = enabled
    response = app.test_client().get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert f'<h1>{title}</h1>' in html and f'{title} · StockBridge</title>' in html
    assert 'Draft — not effective legal terms.' in html and 'No effective date' in html
    assert 'Decision pending' in html or 'approval pending' in html
    for destination in PAGES:
        assert f'href="{destination}"' in html
    assert 'legal.css' in html
    # Existing extra security headers apply to admin routes only; match public auth.
    baseline = app.test_client().get('/auth/login')
    for header in ('X-Content-Type-Options', 'X-Frame-Options', 'Content-Security-Policy'):
        assert response.headers.get(header) == baseline.headers.get(header)

@pytest.mark.parametrize('path', ['/', '/auth/signup', '/auth/login', '/plans/'])
@pytest.mark.parametrize('enabled', [False, True])
def test_policy_links_on_public_surfaces(app, path, enabled):
    app.config['SUBSCRIPTIONS_ENABLED'] = enabled
    response = app.test_client().get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    expected = ['/subscription-terms', '/refund-policy'] if path == '/plans/' else PAGES
    for destination in expected:
        assert f'href="{destination}"' in html
    if path.startswith('/auth/'):
        assert 'name="csrf_token"' in html
        assert 'name="consent"' not in html
        assert 'Continue with Apple' not in html

@pytest.mark.parametrize('kind', ['trial', 'basic', 'plus', 'lifetime'])
def test_authenticated_billing_policy_links_preserve_plan_data(app, kind):
    user, _ = seed(kind)
    client = app.test_client(); login(client, user)
    html = client.get('/plans/').get_data(as_text=True)
    assert 'href="/subscription-terms"' in html and 'href="/refund-policy"' in html
    for spec in PLANS.values():
        assert f'{spec["amount"]/100:,.0f}' in html
    for path in PAGES:
        assert client.get(path).status_code == 200


def test_policy_content_and_rollout_do_not_invent_promises(app):
    client = app.test_client()
    app.config['SUBSCRIPTIONS_ENABLED'] = False
    html = client.get('/subscription-terms').get_data(as_text=True)
    assert 'Creating an account does not currently promise a trial' in html
    assert 'not converted to Basic' in html and 'No custom proration' in html
    app.config['SUBSCRIPTIONS_ENABLED'] = True
    html = client.get('/subscription-terms').get_data(as_text=True)
    assert 'Subscription rollout is enabled' in html
    for spec in PLANS.values():
        assert f'{spec["amount"]/100:,.0f}' in html
    assert 'do not restart a trial' in html
    refunds = client.get('/refund-policy').get_data(as_text=True)
    assert 'promises neither a refund nor a no-refund policy' in refunds
    contact = client.get('/contact').get_data(as_text=True)
    assert 'mailto:' not in contact and 'not a working support submission form' in contact
    privacy = client.get('/privacy').get_data(as_text=True)
    assert 'does not provide automatic account deletion' in privacy
    assert 'No compliance certification' in privacy


def test_legal_routes_unique_and_private_routes_still_protected(app):
    rules = list(app.url_map.iter_rules())
    for path in PAGES:
        matching = [rule for rule in rules if rule.rule == path]
        assert len(matching) == 1 and matching[0].endpoint.startswith('legal.')
        assert 'POST' not in matching[0].methods
    client = app.test_client()
    for path in ['/dashboard', '/products/', '/notifications/', '/profile/', '/admin/']:
        assert client.get(path).status_code in (302, 403, 404)
