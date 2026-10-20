"""Admin UI contracts on isolated in-memory fixtures; no hosted services."""
from html.parser import HTMLParser
from pathlib import Path
import pytest
from app import db
from app.models import AuditLog, Payment
from tests.test_admin_portal import client, seed, admin_login
from tests.test_admin_subscription_payments import records


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
    def get(self, tag):
        return [attrs for name, attrs in self.tags if name == tag]


@pytest.mark.parametrize('path,section', [
    ('/admin/', '/admin/'), ('/admin/users', '/admin/users'),
    ('/admin/users/{customer}', '/admin/users'),
    ('/admin/businesses', '/admin/businesses'),
    ('/admin/businesses/{business}', '/admin/businesses'),
    ('/admin/payments', '/admin/payments'),
    ('/admin/payments/{payment}', '/admin/payments'),
    ('/admin/activity', '/admin/activity'), ('/admin/reports', '/admin/reports'),
    ('/admin/notifications', '/admin/notifications')])
def test_desktop_and_mobile_section_and_main_landmark(client, records, path, section):
    admin_login(client)
    response = client.get(path.format(**records))
    assert response.status_code == 200
    dom = Elements(response.get_data(as_text=True))
    active = [a for a in dom.get('a') if a.get('aria-current') == 'page']
    assert len(active) == 2 and all(a['href'] == section and a['class'] == 'selected' for a in active)
    assert any(a.get('href') == '#admin-main' for a in dom.get('a'))
    assert dom.get('main') == [{'id': 'admin-main', 'tabindex': '-1'}]


@pytest.mark.parametrize('path', ['/admin/users', '/admin/businesses', '/admin/payments', '/admin/activity'])
def test_mobile_table_has_caption_column_headers_and_every_cell_label(client, records, path):
    with client.application.app_context():
        db.session.add(AuditLog(action='USER_REGISTERED', description='Synthetic event'))
        db.session.commit()
    admin_login(client)
    response = client.get(path)
    dom = Elements(response.get_data(as_text=True))
    assert dom.get('caption') and dom.get('table')[0]['role'] == 'table'
    assert all(h.get('scope') == 'col' for h in dom.get('th'))
    assert dom.get('td') and all(cell.get('data-label') for cell in dom.get('td'))


@pytest.mark.parametrize('path', ['/admin/users', '/admin/businesses', '/admin/payments', '/admin/activity', '/admin/reports'])
def test_filter_controls_have_visible_associated_labels(client, records, path):
    admin_login(client)
    dom = Elements(client.get(path).get_data(as_text=True))
    labels = {label['for'] for label in dom.get('label') if label.get('for')}
    # Report controls retain their existing implicit wrapping labels.
    if path != '/admin/reports':
        controls = dom.get('select') + [i for i in dom.get('input') if i.get('type') == 'search']
        assert controls and all(c.get('id') in labels for c in controls)
    else:
        assert len(dom.get('label')) == 3


@pytest.mark.parametrize('target', ['customer', 'business'])
def test_suspension_form_contracts_preserved(client, records, target):
    admin_login(client)
    path = '/admin/users/' if target == 'customer' else '/admin/businesses/'
    dom = Elements(client.get(path + str(records[target])).get_data(as_text=True))
    fields = {i.get('name'): i for i in dom.get('input')}
    for name in ('csrf_token', 'desired_state', 'expected_state', 'expected_suspended_at', 'reason', 'confirm'):
        assert name in fields
    assert 'required' in fields['reason'] and fields['reason']['maxlength'] == '300'
    assert 'required' in fields['confirm'] and fields['confirm']['type'] == 'checkbox'
    assert fields['desired_state']['value'] == 'suspend'
    assert fields['expected_state']['value'] == 'active'


def test_historical_payment_links_do_not_point_to_subscription_details(client, records):
    admin_login(client)
    with client.application.app_context():
        db.session.get(Payment, records['legacy']).status = 'failed'
        db.session.commit()
    for path in (f"/admin/users/{records['customer']}", '/admin/notifications'):
        response = client.get(path)
        assert response.status_code == 200
        assert f"/admin/payments/{records['legacy']}".encode() not in response.data
        assert b'LEGACY-hidden' in response.data
    assert f"/admin/payments/{records['payment']}".encode() in client.get(f"/admin/users/{records['customer']}").data


def test_dashboard_metric_values_match_existing_api_and_legacy_scope(client, records):
    admin_login(client)
    metrics = client.get('/api/admin/dashboard').json
    page = client.get('/admin/').data
    assert b'Legacy access payments' in page and b'View subscription payments' in page
    # The seeded successful subscription is not misrepresented as legacy revenue.
    assert metrics['access_revenue_kobo'] == 0 and metrics['successful_payments'] == 0
    assert b'NGN 5,000.00' not in page


def test_all_admin_templates_compile(client):
    for path in Path('app/templates/admin').glob('*.html'):
        client.application.jinja_env.get_template('admin/' + path.name)


def test_error_flash_announced_as_alert(client, records):
    admin_login(client)
    with client.session_transaction() as session:
        session['_flashes'] = [('error', 'Synthetic failure'), ('success', 'Synthetic success')]
    page = client.get('/admin/payments').data
    assert b'role="alert">Synthetic failure' in page
    assert b'role="status">Synthetic success' in page


def test_authentication_screen_does_not_show_admin_workspace_navigation(client):
    page = client.get('/admin/login').get_data(as_text=True)
    assert 'aria-label="Admin navigation"' not in page
    assert 'aria-label="Admin mobile navigation"' not in page
    assert 'autocomplete="current-password"' in page


@pytest.mark.parametrize('template,path,fields', [
    ('login.html', '/admin/login', {'email': 'username', 'password': 'current-password'}),
    ('forgot_password.html', '/admin/forgot-password', {'email': 'email'}),
    ('reset_password.html', '/admin/reset-password/synthetic', {'password': 'new-password', 'confirm_password': 'new-password'})])
def test_admin_authentication_form_contracts_and_named_sections(client, template, path, fields):
    from flask import render_template
    # Render the real template with a synthetic context; no reset record or token is created.
    with client.application.test_request_context(path):
        html = render_template('admin/' + template, invalid=False, error=None)
    dom = Elements(html)
    sections = [s for s in dom.get('section') if s.get('class') == 'auth-shell']
    assert sections and sections[0]['aria-labelledby'] in {h.get('id') for h in dom.get('h1')}
    inputs = {i.get('name'): i for i in dom.get('input')}
    assert 'csrf_token' in inputs
    for name, autocomplete in fields.items():
        assert inputs[name]['autocomplete'] == autocomplete and 'required' in inputs[name]
        if 'password' in name:
            assert inputs[name]['type'] == 'password'
    if template == 'reset_password.html':
        assert inputs['password']['minlength'] == inputs['confirm_password']['minlength'] == '12'
    assert all(form.get('method') == 'post' for form in dom.get('form'))
    assert 'aria-label="Admin navigation"' not in html
