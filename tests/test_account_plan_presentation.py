"""Account identity and effective-plan presentation on disposable SQLite only."""
from datetime import datetime, timedelta
from html import unescape
import re
from unittest.mock import patch

import pytest
from flask import render_template
from flask_login import login_user

from app import db
from app.models import BillingEvent, RecurringSubscription
from tests.test_multi_business import app, seed, login


def section(html, marker):
    return re.search(marker + r'(.*?)</div>', html, re.S).group(1)


def text(html):
    return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', html)).split())


@pytest.mark.parametrize('kind,interval,label', [
    ('basic', 'monthly', 'StockBridge Basic · Monthly'),
    ('basic', 'yearly', 'StockBridge Basic · Yearly'),
    ('plus', 'monthly', 'StockBridge Plus · Monthly'),
    ('plus', 'yearly', 'StockBridge Plus · Yearly'),
    ('pro', 'monthly', 'StockBridge Pro · Monthly'),
    ('pro', 'yearly', 'StockBridge Pro · Yearly'),
    ('trial', None, 'Free Trial'),
    ('lifetime', None, 'Existing account access'),
    ('lifetime_plus', 'monthly', 'Existing account access + Plus · Monthly'),
    ('lifetime_pro', 'monthly', 'Existing account access + Pro · Monthly'),
    ('expired', None, 'Subscription expired · read-only'),
])
def test_dropdown_plan_matches_sidebar_without_mutating_billing(app, kind, interval, label):
    user, _ = seed('basic' if kind == 'expired' else kind)
    sub = RecurringSubscription.query.first()
    if sub:
        if interval:
            sub.billing_interval = interval
        if kind == 'expired':
            sub.current_period_start = datetime.utcnow() - timedelta(days=31)
            sub.current_period_end = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    client = app.test_client(); login(client, user)
    before = (RecurringSubscription.query.count(), BillingEvent.query.count())
    with patch.object(db.session(), 'commit', side_effect=AssertionError('Unexpected write')):
        response = client.get('/plans/')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    dropdown = section(html, r'id="profileDropdown">')
    assert '<dt>Plan</dt>' in dropdown and label in text(dropdown)
    assert label in text(re.search(r'<span class="workspace-label">(.*?)</span>', html, re.S).group(1))
    assert (RecurringSubscription.query.count(), BillingEvent.query.count()) == before


def test_plan_like_account_name_is_preserved_and_distinguished(app):
    user, _ = seed('plus')
    user.full_name = 'basic yearly'
    sub = RecurringSubscription.query.one(); sub.billing_interval = 'yearly'
    sub.cancel_at_period_end = True
    db.session.add(RecurringSubscription(user_id=user.id, business_id=sub.business_id,
        plan_code='basic', billing_interval='yearly', amount_kobo=3000000,
        provider_plan_code='PLN_older_fixture', status='active',
        current_period_start=datetime.utcnow()-timedelta(days=2),
        current_period_end=datetime.utcnow()+timedelta(days=20)))
    db.session.commit()
    client = app.test_client(); login(client, user)
    html = client.get('/dashboard').get_data(as_text=True)
    identity = section(html, r'<div class="profile-copy">')
    dropdown = section(html, r'id="profileDropdown">')
    assert text(identity) == 'Account basic yearly Owner'
    assert 'StockBridge Plus · Yearly · renewal stopped' in text(dropdown)
    assert 'StockBridge Basic' not in text(dropdown)
    assert 'aria-label="Account menu" aria-controls="profileDropdown" aria-expanded="false"' in html
    for href, label in [('/profile/', 'Profile & settings'), ('/businesses/', 'My Businesses'), ('/plans/', 'Billing & plans')]:
        assert f'href="{href}">{label}</a>' in unescape(dropdown)
    db.session.refresh(user)
    assert user.full_name == 'basic yearly'


@pytest.mark.parametrize('label', [None, '', 'undefined'])
def test_missing_billing_context_has_neutral_fallback(app, label):
    user, _ = seed('plus')
    with app.test_request_context('/dashboard'):
        login_user(user)
        value = app.jinja_env.undefined(name='billing_label') if label == 'undefined' else label
        html = render_template('base.html', billing_label=value)
    dropdown = section(html, r'id="profileDropdown">')
    assert '<dt>Plan</dt>' in dropdown
    assert 'Plan information unavailable' in dropdown
    assert 'None' not in dropdown and 'Undefined' not in dropdown


def test_disabled_subscriptions_do_not_display_stale_plan_context(app):
    user, _ = seed('plus')
    app.config['SUBSCRIPTIONS_ENABLED'] = False
    with app.test_request_context('/dashboard'):
        login_user(user)
        html = render_template('base.html', billing_label='StockBridge Pro · Yearly')
    dropdown = section(html, r'id="profileDropdown">')
    assert 'Subscription plans are not enabled' in dropdown
    assert 'StockBridge Pro' not in dropdown
    assert 'href="/plans/">Billing & plans</a>' in unescape(dropdown)


def test_account_without_business_has_safe_missing_context(app):
    user, _ = seed('restricted', businesses=0)
    client = app.test_client(); login(client, user)
    response = client.get('/businesses/')
    assert response.status_code == 200
    assert 'Plan information unavailable' in section(response.get_data(as_text=True), r'id="profileDropdown">')
