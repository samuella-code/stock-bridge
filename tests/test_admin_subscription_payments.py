"""Admin subscription views use only the existing isolated in-memory SQLite fixture."""
from datetime import datetime
import time
import pytest
from app import db
from app.models import AuditLog, Business, Payment, RecurringSubscription, User
from tests.test_admin_portal import client, seed, admin_login


@pytest.fixture
def records(client, monkeypatch):
    # GETs and rejected subscription actions must never call a payment provider or email.
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only admin payment UI attempted an external action')
    monkeypatch.setattr('app.admin.routes.verify_transaction', forbidden)
    monkeypatch.setattr('app.admin.routes.safely_send', forbidden)
    admin_id, customer_id, business_id = seed(client)
    with client.application.app_context():
        sub = RecurringSubscription(user_id=customer_id, business_id=business_id,
            plan_code='plus', billing_interval='monthly', amount_kobo=500000,
            provider_plan_code='synthetic-plan', status='active')
        db.session.add(sub)
        db.session.flush()
        payment = Payment(user_id=customer_id, business_id=business_id, subscription_id=sub.id,
            customer_email='owner@example.com', reference='SUB-current', product='subscription',
            plan_code='plus', billing_interval='monthly', amount_kobo=500000, currency='NGN',
            provider='paystack', status='success', paid_at=datetime(2026, 10, 20, 8, 30))
        legacy = Payment(user_id=customer_id, business_id=business_id,
            customer_email='owner@example.com', reference='LEGACY-hidden', amount_kobo=300000)
        other = Payment(user_id=customer_id, business_id=business_id, product='other',
            customer_email='owner@example.com', reference='OTHER-hidden', amount_kobo=100)
        db.session.add_all([payment, legacy, other])
        db.session.commit()
        ids = dict(admin=admin_id, customer=customer_id, business=business_id,
                   payment=payment.id, subscription=sub.id, legacy=legacy.id, other=other.id)
    return ids


def test_listing_is_subscription_only(client, records):
    admin_login(client)
    page = client.get('/admin/payments')
    assert page.status_code == 200
    assert b'Subscription payments' in page.data and b'SUB-current' in page.data
    assert b'Plus' in page.data and b'Monthly' in page.data and b'NGN 5,000.00' in page.data
    assert b'LEGACY-hidden' not in page.data and b'OTHER-hidden' not in page.data
    assert b'Lifetime' not in page.data and b'Verify with Paystack' not in page.data


def test_detail_has_subscription_metadata_and_safe_links(client, records):
    admin_login(client)
    page = client.get(f"/admin/payments/{records['payment']}")
    assert page.status_code == 200
    for text in ('Owner', 'owner@example.com', 'Shop', 'Plus', 'Monthly', 'NGN 5,000.00',
                 'Currency: NGN', 'paystack', 'SUB-current', 'Success', '20 Oct 2026 08:30 UTC',
                 'Recurring subscription status: Active'):
        assert text.encode() in page.data
    assert f"/admin/users/{records['customer']}".encode() in page.data
    assert f"/admin/businesses/{records['business']}".encode() in page.data
    assert b'/verify' not in page.data and b'confirm' not in page.data


@pytest.mark.parametrize('status,expected', [('all', 5), ('success', 1), ('failed', 1), ('pending', 2), ('unexpected', 5)])
def test_status_filters_preserve_initialized_as_pending(client, records, status, expected):
    with client.application.app_context():
        for value in ('failed', 'pending', 'initialized', 'abandoned'):
            db.session.add(Payment(user_id=records['customer'], business_id=records['business'],
                product='subscription', customer_email='owner@example.com', reference='FILTER-'+value,
                amount_kobo=300000, status=value))
        db.session.commit()
    admin_login(client)
    page = client.get('/admin/payments?status='+status).data
    assert page.count(b'data-label="Reference"') == expected
    if status == 'pending':
        assert b'FILTER-initialized' in page and b'FILTER-pending' in page and b'SUB-current' not in page


def test_pagination_preserves_filter(client, records):
    with client.application.app_context():
        for i in range(26):
            db.session.add(Payment(user_id=records['customer'], product='subscription',
                customer_email='owner@example.com', reference=f'PAGE-{i}', amount_kobo=300000,
                status='pending', created_at=datetime(2026, 1, 1)))
        db.session.commit()
    admin_login(client)
    first = client.get('/admin/payments?status=pending').data
    second = client.get('/admin/payments?status=pending&page=2').data
    assert first.count(b'data-label="Reference"') == 25
    assert second.count(b'data-label="Reference"') == 1
    assert b'page=2' in first and b'status=pending' in first
    assert b'Page 2 of 2' in second


@pytest.mark.parametrize('kind', ['anonymous', 'customer', 'customer-forged-admin-session'])
@pytest.mark.parametrize('destination', ['list', 'detail'])
def test_admin_authentication_required(client, records, kind, destination):
    if kind != 'anonymous':
        with client.session_transaction() as session:
            session['_user_id'] = str(records['customer'])
            session['_fresh'] = True
            if kind == 'customer-forged-admin-session':
                session.update(admin_session=True, admin_auth_version=1, admin_last_activity=time.time())
    url = '/admin/payments' if destination == 'list' else f"/admin/payments/{records['payment']}"
    response = client.get(url)
    assert response.status_code == (302 if kind == 'anonymous' else 403)
    assert b'SUB-current' not in response.data


@pytest.mark.parametrize('kind', ['admin', 'dual-role', 'other-customer'])
def test_mismatched_or_protected_business_never_exposed(client, records, kind):
    with client.application.app_context():
        user = User(full_name='Hidden owner', email='hidden@example.com',
                    role='admin' if kind == 'admin' else 'user', admin_enabled=kind == 'dual-role')
        user.set_password('synthetic-password123')
        db.session.add(user)
        db.session.flush()
        business = Business(user_id=user.id, name='PROTECTED-BUSINESS')
        db.session.add(business)
        db.session.flush()
        payment = db.session.get(Payment, records['payment'])
        payment.business_id = business.id
        if kind != 'other-customer':
            payment.user_id = user.id
        db.session.commit()
        business_id = business.id
    admin_login(client)
    assert b'SUB-current' not in client.get('/admin/payments').data
    assert client.get(f"/admin/payments/{records['payment']}").status_code == 404
    if kind != 'other-customer':
        assert client.get(f'/admin/businesses/{business_id}').status_code == 404


def test_missing_customer_not_inferred_from_email(client, records):
    with client.application.app_context():
        db.session.get(Payment, records['payment']).user_id = None
        db.session.commit()
    admin_login(client)
    assert b'SUB-current' not in client.get('/admin/payments').data
    assert client.get(f"/admin/payments/{records['payment']}").status_code == 404


def test_account_level_payment_without_business_is_readable(client, records):
    with client.application.app_context():
        payment = db.session.get(Payment, records['payment'])
        payment.business_id = None
        payment.subscription_id = None
        payment.paid_at = None
        payment.plan_code = None
        payment.billing_interval = None
        db.session.commit()
    admin_login(client)
    page = client.get(f"/admin/payments/{records['payment']}")
    assert page.status_code == 200
    assert b'Unlinked' in page.data and b'Not recorded' in page.data
    assert b'Not linked or unavailable' in page.data


def test_subscription_reference_cannot_disclose_other_customer(client, records):
    with client.application.app_context():
        other = User(full_name='Another customer', email='another@example.com')
        other.set_password('synthetic-password123')
        db.session.add(other)
        db.session.flush()
        business = Business(user_id=other.id, name='Another shop')
        db.session.add(business)
        db.session.flush()
        sub = db.session.get(RecurringSubscription, records['subscription'])
        sub.user_id, sub.business_id, sub.status = other.id, business.id, 'private-status'
        db.session.commit()
    admin_login(client)
    page = client.get(f"/admin/payments/{records['payment']}").data
    assert b'Not linked or unavailable' in page
    assert b'Private-Status' not in page and b'another@example.com' not in page


def test_business_references_link_subscription_not_legacy(client, records):
    admin_login(client)
    page = client.get(f"/admin/businesses/{records['business']}").data
    assert f"/admin/payments/{records['payment']}".encode() in page
    assert f"/admin/payments/{records['legacy']}".encode() not in page
    assert b'LEGACY-hidden' in page  # Historical record remains visible as plain text.


@pytest.mark.parametrize('kind', ['legacy', 'other'])
def test_non_subscription_details_excluded(client, records, kind):
    admin_login(client)
    assert client.get(f"/admin/payments/{records[kind]}").status_code == 404


def test_views_and_rejected_actions_do_not_mutate_billing_or_audit(client, records):
    admin_login(client)
    with client.application.app_context():
        before = (AuditLog.query.count(), Payment.query.count(),
                  db.session.get(RecurringSubscription, records['subscription']).status,
                  db.session.get(Business, records['business']).subscription_status)
    assert client.get('/admin/payments').status_code == 200
    assert client.get(f"/admin/payments/{records['payment']}").status_code == 200
    # Existing global middleware rejects unmatched POSTs before method handling.
    assert client.post(f"/admin/payments/{records['payment']}").status_code == 403
    assert client.post(f"/admin/payments/{records['payment']}/verify", data={'confirm': 'on'}).status_code == 404
    with client.application.app_context():
        after = (AuditLog.query.count(), Payment.query.count(),
                 db.session.get(RecurringSubscription, records['subscription']).status,
                 db.session.get(Business, records['business']).subscription_status)
        assert before == after


def test_csrf_still_guards_legacy_post_endpoint(client, records):
    admin_login(client)
    client.application.config['WTF_CSRF_ENABLED'] = True
    assert client.post(f"/admin/payments/{records['payment']}/verify", data={'confirm': 'on'}).status_code == 400


@pytest.mark.parametrize('enabled', [False, True])
def test_payment_views_remain_read_only_with_billing_rollout(client, records, enabled):
    from sqlalchemy import event
    client.application.config.update(SUBSCRIPTIONS_ENABLED=enabled, BILLING_PROVIDER_ENABLED=enabled)
    admin_login(client)
    statements = []
    def record_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())
    with client.application.app_context():
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', record_sql)
        try:
            assert client.get('/admin/payments').status_code == 200
            assert client.get(f"/admin/payments/{records['payment']}").status_code == 200
        finally:
            event.remove(engine, 'before_cursor_execute', record_sql)
    assert statements and set(statements) == {'SELECT'}
