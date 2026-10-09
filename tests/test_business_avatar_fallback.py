from pathlib import Path
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader
from test_multi_business import app, seed, login

ROOT = Path(__file__).resolve().parents[1]


def render_avatar(key):
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True)
    env.globals['url_for'] = lambda *args, **kwargs: '/businesses/1/logo?v=example.webp'
    business = SimpleNamespace(id=1, name='Tosin <Store>', logo_key=key)
    html = env.from_string("{% from 'components/business_avatar.html' import business_avatar %}{{ business_avatar(business) }}").render(business=business)
    assert business.logo_key == key
    return html


def test_existing_logo_has_hidden_escaped_initial_fallback():
    html = render_avatar('businesses/1/example.webp')
    assert 'data-business-logo' in html
    assert 'data-business-initial' in html
    assert 'default business avatar" hidden>T</span>' in html
    assert 'Tosin &lt;Store&gt;' in html


def test_no_logo_shows_initial_without_image():
    html = render_avatar(None)
    assert '<img' not in html
    assert 'default business avatar">T</span>' in html
    assert ' hidden' not in html


def test_customer_stylesheet_version_updated(app):
    user, _ = seed('basic')
    client = app.test_client(); login(client, user)
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'css/customer.css?v=responsive-22460dd-2' in html
    assert 'css/customer.css?v=ui-6.1' not in html
    assert 'css/activity.css?v=activity-10' in html
