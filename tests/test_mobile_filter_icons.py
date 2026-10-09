"""Targeted presentation checks, not a substitute for iPhone viewport QA."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from test_multi_business import app, seed, login

ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_arrows_are_fixed_svg_and_keep_accessible_navigation():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True)
    for label, href, glyph in [('Sales', '/sales/', '↗'), ('Restocking', '/restocking/', '↻')]:
        html = env.from_string("{% from 'components/ui.html' import nav_link %}{{ nav_link(label, href, true, glyph) }}").render(label=label, href=href, glyph=glyph)
        assert '<svg width="17" height="17" viewBox="0 0 24 24"' in html
        assert 'stroke="currentColor"' in html
        assert 'focusable="false" aria-hidden="true"' in html
        assert f'href="{href}"' in html and 'aria-current="page"' in html
        assert f'<span>{label}</span>' in html
        assert glyph not in html


def test_mobile_date_filter_constrained_single_column():
    css = (ROOT / 'app/static/css/customer.css').read_text()
    mobile = css.split('@media(max-width:600px)', 1)[1]
    assert '.customer-app .period-picker{display:grid;grid-template-columns:minmax(0,1fr);padding:12px}' in mobile
    assert '.customer-app .period-picker label{width:100%;max-width:100%;min-width:0}' in mobile
    assert '.customer-app .period-picker input,.customer-app .period-picker select,.customer-app .period-picker button{width:100%;max-width:100%;min-width:0;box-sizing:border-box}' in mobile


def test_dashboard_preserves_custom_date_parameters(app):
    user, _ = seed('basic')
    client = app.test_client(); login(client, user)
    response = client.get('/dashboard?period=custom&from=2026-10-01&to=2026-10-09')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'name="from" value="2026-10-01"' in html
    assert 'name="to" value="2026-10-09"' in html
    assert '<button class="secondary-btn">Apply</button>' in html
    assert 'aria-label="Main navigation"' in html
