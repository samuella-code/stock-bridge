"""Admin reporting against disposable SQLite; all external actions are forbidden."""
from datetime import datetime, timedelta
import pytest
from sqlalchemy import event
from app import db
from app.models import AccountBilling, Business, Payment, RecurringSubscription, User
from app.admin.subscription_reporting import current_subscription_accounts, collected_subscription_payments
from app.subscriptions.entitlements import PLANS
from tests.test_admin_portal import client, seed, admin_login


@pytest.fixture
def setup(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Admin reporting attempted an external action')
    monkeypatch.setattr('app.admin.routes.verify_transaction', forbidden)
    monkeypatch.setattr('app.admin.routes.safely_send', forbidden)
    ids = seed(client)
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    return ids


def customer(plan='pro', interval='monthly', *, now=None, status='active', businesses=1):
    now = now or datetime.utcnow()
    user = User(full_name='Metric customer', email=f'metric-{User.query.count()}@example.invalid',
                email_verified_at=now, password_hash='unused-synthetic')
    db.session.add(user)
    db.session.flush()
    shops = [Business(user_id=user.id, name=f'Metric shop {user.id}-{i}',
                      subscription_status='active') for i in range(businesses)]
    db.session.add_all(shops)
    db.session.flush()
    db.session.add(AccountBilling(user_id=user.id, primary_business_id=shops[0].id))
    sub = RecurringSubscription(user_id=user.id, business_id=shops[0].id,
        plan_code=plan, billing_interval=interval, amount_kobo=PLANS[plan, interval]['amount'],
        provider_plan_code='synthetic', status=status,
        current_period_start=now-timedelta(days=1), current_period_end=now+timedelta(days=1))
    db.session.add(sub)
    db.session.flush()
    return user, shops, sub


def payment(user, sub, reference, paid_at):
    row = Payment(user_id=user.id, business_id=sub.business_id, subscription_id=sub.id,
        product='subscription', plan_code=sub.plan_code, billing_interval=sub.billing_interval,
        reference=reference, customer_email=user.email, amount_kobo=sub.amount_kobo,
        status='success', provider='paystack', currency='NGN', paid_at=paid_at)
    db.session.add(row)
    return row


@pytest.mark.parametrize('plan,interval', list(PLANS))
def test_six_account_buckets_and_html(client, setup, plan, interval):
    user, shops, sub = customer(plan, interval, businesses=3)
    # Duplicate overlapping period rows and multiple payments must not multiply accounts.
    duplicate = RecurringSubscription(user_id=user.id, business_id=shops[0].id,
        plan_code=plan, billing_interval=interval, amount_kobo=sub.amount_kobo,
        provider_plan_code='synthetic', status='active',
        current_period_start=sub.current_period_start, current_period_end=sub.current_period_end)
    db.session.add(duplicate)
    payment(user, sub, 'one', datetime.utcnow())
    payment(user, sub, 'two', datetime.utcnow())
    db.session.commit()
    admin_login(client)
    data = client.get('/api/admin/dashboard').json
    assert data['current_subscription_accounts']['total'] == 1
    assert data['current_subscription_accounts']['by_plan'][plan][interval] == 1
    page = client.get('/admin/').data
    assert f'{plan.title()} {interval} accounts'.encode() in page
    assert b'Collected subscription revenue' in page


@pytest.mark.parametrize('state,expected', [
    ('expired', 0), ('cancel_at_period_end', 1), ('cancelled', 1),
    ('past_due', 1), ('suspended', 0), ('unverified', 0), ('admin', 0),
    ('dual', 0), ('business-suspended', 1), ('ends-now', 0), ('starts-now', 1),
])
def test_current_access_boundaries_and_account_exclusions(client, setup, state, expected):
    now = datetime(2026, 10, 25, 12)
    user, shops, sub = customer(now=now)
    if state == 'expired': sub.current_period_end = now-timedelta(seconds=1)
    elif state == 'ends-now': sub.current_period_end = now
    elif state == 'starts-now': sub.current_period_start = now
    elif state == 'suspended': user.suspended_at = now
    elif state == 'unverified': user.email_verified_at = None
    elif state == 'admin': user.role = 'admin'
    elif state == 'dual': user.admin_enabled = True
    elif state == 'business-suspended': shops[0].suspended_at = now
    else:
        sub.status = state
        sub.cancel_at_period_end = True
    db.session.commit()
    from app.admin.routes import customer_query
    result = current_subscription_accounts(customer_query().all(), now=now)
    assert result['total'] == expected


def test_latest_effective_plan_wins_without_overlap(client, setup):
    user, shops, old = customer('basic')
    latest = RecurringSubscription(user_id=user.id, business_id=shops[0].id,
        plan_code='pro', billing_interval='yearly', amount_kobo=10000000,
        provider_plan_code='synthetic', status='cancel_at_period_end',
        current_period_start=old.current_period_start+timedelta(hours=1),
        current_period_end=old.current_period_end)
    db.session.add(latest)
    db.session.commit()
    result = current_subscription_accounts([user])
    assert result['total'] == 1 and result['by_plan']['pro']['yearly'] == 1
    assert result['by_plan']['basic']['monthly'] == 0


@pytest.mark.parametrize('plan,interval', list(PLANS))
def test_collections_paid_at_boundaries_and_legacy_separation(client, setup, plan, interval):
    user, shops, sub = customer(plan, interval)
    start = datetime(2026, 10, 1)
    end = datetime(2026, 10, 2)
    # Historical verified amounts, not today's prices, define collected revenue.
    sub.amount_kobo = 123400
    for name, stamp in [('start', start), ('inside', start+timedelta(hours=12)),
                        ('before', start-timedelta(microseconds=1)), ('end', end)]:
        row = payment(user, sub, name, stamp)
        row.created_at = datetime(2020, 1, 1)
    legacy = Payment(business_id=shops[0].id, customer_email=user.email,
        reference='legacy', product='lifetime', amount_kobo=300000,
        status='success', paid_at=start)
    db.session.add(legacy)
    db.session.commit()
    assert collected_subscription_payments(start, end) == {'count': 2, 'revenue_kobo': 246800}
    admin_login(client)
    data = client.get('/api/admin/reports?period=custom&start=2026-10-01&end=2026-10-01').json
    assert data['period_metrics']['subscription_payments']['count'] == 2
    assert data['period_metrics']['purchases'] == 1
    assert data['period_metrics']['revenue_kobo'] == 300000
    assert data['access_revenue_kobo'] == 300000
    assert client.get('/api/admin/reports?period=custom&start=bad&end=bad').status_code == 400


@pytest.mark.parametrize('corruption', [
    'status', 'paid_at', 'currency', 'provider', 'amount', 'plan', 'interval',
    'subscription', 'owner', 'business', 'admin', 'dual',
])
def test_unverified_or_mismatched_rows_excluded(client, setup, corruption):
    user, shops, sub = customer()
    row = payment(user, sub, 'bad', datetime.utcnow())
    other, foreign, _ = customer()
    if corruption == 'status': row.status = 'pending'
    elif corruption == 'paid_at': row.paid_at = None
    elif corruption == 'currency': row.currency = 'USD'
    elif corruption == 'provider': row.provider = 'other'
    elif corruption == 'amount': row.amount_kobo += 1
    elif corruption == 'plan': row.plan_code = 'basic'
    elif corruption == 'interval': row.billing_interval = 'yearly'
    elif corruption == 'subscription': row.subscription_id = None
    elif corruption == 'owner': row.user_id = other.id
    elif corruption == 'business': row.business_id = foreign[0].id
    elif corruption == 'admin': user.role = 'admin'
    elif corruption == 'dual': user.admin_enabled = True
    db.session.commit()
    assert collected_subscription_payments()['count'] == 0


@pytest.mark.parametrize('kind,alert', [('trial', False), ('legacy', False), ('pro', False),
    ('expired', True), ('suspended-user', False), ('suspended-business', False)])
def test_alerts_use_entitlements(client, setup, kind, alert):
    user, shops, sub = customer()
    shops[0].name = 'ALERT-SUBJECT'
    account = db.session.get(AccountBilling, user.id)
    if kind != 'pro': sub.current_period_end = datetime.utcnow()-timedelta(days=1)
    if kind == 'trial':
        account.trial_started_at = datetime.utcnow()-timedelta(days=1)
        account.trial_ends_at = datetime.utcnow()+timedelta(days=1)
    elif kind == 'legacy':
        legacy = Payment(business_id=shops[0].id, customer_email=user.email,
            reference='legacy', amount_kobo=300000, status='success', paid_at=datetime.utcnow())
        db.session.add(legacy)
        db.session.flush()
        account.legacy_payment_id, account.legacy_granted_at = legacy.id, datetime.utcnow()
    elif kind == 'suspended-user': user.suspended_at = datetime.utcnow()
    elif kind == 'suspended-business': shops[0].suspended_at = datetime.utcnow()
    db.session.commit()
    admin_login(client)
    assert (b'ALERT-SUBJECT' in client.get('/admin/notifications').data) is alert


def test_api_compatibility_and_read_only_gets(client, setup):
    user, shops, sub = customer()
    payment(user, sub, 'SUB-API', datetime.utcnow())
    db.session.add(Payment(reference='LEGACY-API', customer_email=user.email, amount_kobo=300000))
    db.session.commit()
    admin_login(client)
    statements = []
    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    event.listen(db.engine, 'before_cursor_execute', record)
    try:
        legacy = client.get('/api/admin/payments').json
        assert set(legacy) == {'payments'}
        assert legacy['payments'][0]['reference'] == 'LEGACY-API'
        assert set(legacy['payments'][0]) == {'id', 'business_id', 'reference', 'amount_kobo', 'status'}
        data = client.get('/api/admin/payments?product=subscription').json
        assert data['payments'][0]['reference'] == 'SUB-API'
        assert data['payments'][0]['plan_code'] == 'pro'
        assert b'SUB-API' in client.get('/admin/payments').data
        assert client.get('/api/admin/payments?product=subscription&status=failed').json['payments'] == []
        assert client.get('/api/admin/payments?product=bad').status_code == 400
        for path in ['/admin/', '/admin/reports', '/admin/notifications', '/api/admin/reports']:
            assert client.get(path).status_code == 200
    finally:
        event.remove(db.engine, 'before_cursor_execute', record)
    assert statements and set(statements) == {'SELECT'}


def test_legacy_upgrade_and_suspension_do_not_rewrite_collections(client, setup):
    user, shops, sub = customer('pro', 'yearly')
    legacy = Payment(business_id=shops[0].id, customer_email=user.email,
        reference='legacy-upgrade', amount_kobo=300000, status='success', paid_at=datetime.utcnow())
    db.session.add(legacy)
    db.session.flush()
    account = db.session.get(AccountBilling, user.id)
    account.legacy_payment_id, account.legacy_granted_at = legacy.id, datetime.utcnow()
    payment(user, sub, 'upgrade', datetime.utcnow())
    db.session.commit()
    assert current_subscription_accounts([user])['total'] == 1
    user.suspended_at = datetime.utcnow()
    db.session.commit()
    assert current_subscription_accounts([user])['total'] == 0
    assert collected_subscription_payments()['revenue_kobo'] == 10000000


def test_api_excludes_foreign_and_admin_owned_subscription_rows(client, setup):
    user, shops, sub = customer()
    other, foreign, _ = customer()
    mismatched = payment(user, sub, 'FOREIGN-ROW', datetime.utcnow())
    mismatched.business_id = foreign[0].id
    protected = payment(other, sub, 'ADMIN-ROW', datetime.utcnow())
    other.admin_enabled = True
    db.session.commit()
    admin_login(client)
    assert client.get('/api/admin/payments?product=subscription').json['payments'] == []



def test_api_reports_computes_current_account_totals_once(client, setup, monkeypatch):
    from app.admin import subscription_reporting
    customer('pro', 'yearly')
    db.session.commit()
    original = subscription_reporting.current_subscription_accounts
    calls = []
    def measured(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(subscription_reporting, 'current_subscription_accounts', measured)
    admin_login(client)
    data = client.get('/api/admin/reports').json
    assert len(calls) == 1
    assert data['current_subscription_accounts']['total'] == 1
    assert data['period_metrics']['current_subscription_accounts'] == data['current_subscription_accounts']


def test_business_status_label_preserves_existing_calculation(client, setup):
    from app.admin.routes import overview
    user, shops, sub = customer('pro', businesses=3)
    shops[0].subscription_status = 'inactive'
    shops[1].subscription_status = 'trialing'
    shops[2].subscription_status = 'cancelled'
    db.session.commit()
    admin_login(client)
    assert overview()['unpaid_users'] == 3
    page = client.get('/admin/').data
    assert b'Businesses without active status' in page
    assert b'Non-active legacy business status' not in page
