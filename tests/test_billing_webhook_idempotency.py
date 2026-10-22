"""Targeted billing safety tests; disposable SQLite and mocked Paystack only."""
from datetime import timedelta

import pytest

from app import db
from app.models import Payment, RecurringSubscription
from app.subscriptions import billing
from tests.test_billing import app, client, fake, seed, purchase, renewal_event, webhook


def test_successful_payment_rejects_changed_invoice_period(client, fake):
    user, _ = seed(eligible=False)
    sub = purchase(user)
    end = sub.current_period_end
    before = Payment.query.count()
    response = webhook(client, renewal_event(sub, sub.checkout_reference, end, end+timedelta(days=28)))
    assert response.status_code == 503
    db.session.refresh(sub)
    assert sub.current_period_end == end and Payment.query.count() == before


def test_successful_payment_still_validates_provider_identity(app, fake):
    user, _ = seed(eligible=False)
    sub = purchase(user)
    payment = Payment.query.filter_by(reference=sub.checkout_reference).one()
    data = fake.verified(payment.reference)
    data['amount'] += 1
    with pytest.raises(ValueError):
        billing.confirm_payment(sub, payment, data)
    db.session.rollback()


def test_attachment_acquires_account_lock_before_identity_mutation(app, fake, monkeypatch):
    user, _ = seed(eligible=False)
    billing.begin_checkout(user, 'basic', 'monthly')
    sub = RecurringSubscription.query.one()
    original = billing.lock_account
    locks = []
    def guarded_lock(user_id):
        locks.append(user_id)
        from sqlalchemy import inspect
        assert not inspect(sub).attrs.provider_subscription_code.history.has_changes()
        assert not inspect(sub).attrs.provider_customer_code.history.has_changes()
        return original(user_id)
    monkeypatch.setattr(billing, 'lock_account', guarded_lock)
    billing.attach_subscription('SUB_new')
    assert locks == [user.id]
    db.session.rollback()


def test_same_invoice_period_replay_is_verified_without_duplicate_events(client, fake):
    from app.models import BillingEvent
    user, _ = seed(eligible=False); sub = purchase(user)
    start = sub.current_period_end; end = start+timedelta(days=28)
    data = fake.verified(sub.checkout_reference)
    from tests.test_billing import iso
    data.update(reference='verified-renewal', paid_at=iso(start), metadata={})
    fake.transactions['verified-renewal'] = data
    event = renewal_event(sub, 'verified-renewal', start, end)
    assert webhook(client, event).status_code == 200
    count = BillingEvent.query.count()
    assert webhook(client, event).status_code == 200
    assert Payment.query.count() == 2 and BillingEvent.query.count() == count
    assert sub.current_period_end == end
    # Same invoice key/end, but a changed start must not bypass receipt validation.
    event['data']['period_start'] = iso(start+timedelta(hours=1))
    assert webhook(client, event).status_code == 503
    assert BillingEvent.query.count() == count and sub.current_period_end == end


def test_old_success_without_period_receipt_requires_reconciliation(client, fake):
    from app.models import BillingEvent
    user, _ = seed(eligible=False); sub = purchase(user)
    BillingEvent.query.filter(BillingEvent.event_key.like('payment-period:%')).delete()
    db.session.commit()
    end = sub.current_period_end
    assert webhook(client, renewal_event(sub, sub.checkout_reference,
        sub.current_period_start, end)).status_code == 503
    assert Payment.query.one().status == 'success' and sub.current_period_end == end
    # Existing callback replay stays usable without inventing historic period evidence.
    billing.verify_checkout(user, sub.checkout_reference)
    assert sub.current_period_end == end


def test_integrity_failure_rolls_back_and_returns_retryable_response(client, fake, monkeypatch, caplog):
    from sqlalchemy.exc import IntegrityError
    from app.models import BillingEvent
    user, _ = seed(eligible=False); sub = purchase(user)
    before = sub.current_period_end; events = BillingEvent.query.count()
    start = before; end = start+timedelta(days=28)
    from tests.test_billing import iso
    data = fake.verified(sub.checkout_reference)
    data.update(reference='rollback-renewal', paid_at=iso(start), metadata={})
    fake.transactions['rollback-renewal'] = data
    real_commit = db.session.commit
    def fail():
        db.session.flush()
        raise IntegrityError('private-sql', {'private':'private-value'}, Exception('private-exception'))
    monkeypatch.setattr(db.session, 'commit', fail)
    event = renewal_event(sub, 'rollback-renewal', start, end)
    assert webhook(client, event).status_code == 503
    assert Payment.query.count() == 1 and BillingEvent.query.count() == events
    db.session.refresh(sub)
    assert sub.current_period_end == before
    assert 'private-sql' not in caplog.text and 'private-value' not in caplog.text
    # Rollback leaves the same session usable, and a valid retry succeeds once.
    monkeypatch.setattr(db.session, 'commit', real_commit)
    assert webhook(client, event).status_code == 200
    assert Payment.query.count() == 2 and sub.current_period_end == end


@pytest.mark.parametrize('scenario', ['subscription_create', 'renewal', 'callback_webhook'])
def test_two_workers_same_webhook_or_callback_sqlite(tmp_path, monkeypatch, scenario):
    """Real thread interleaving on disposable SQLite; not PostgreSQL verification."""
    import hashlib
    import hmac
    import json
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from threading import Barrier
    from app import create_app
    from app.models import BillingEvent, User
    from app.subscriptions import provider
    from tests.test_billing import FakeProvider, iso
    monkeypatch.delenv('VERCEL_ENV', raising=False)
    monkeypatch.setattr('app.email_service._send_email', lambda *a, **k: False)
    application = create_app({'TESTING':True, 'SECRET_KEY':'race', 'SERVER_NAME':'localhost',
        'SQLALCHEMY_DATABASE_URI':f'sqlite:///{tmp_path / "billing-workers.db"}',
        'SQLALCHEMY_ENGINE_OPTIONS':{'connect_args':{'timeout':5}},
        'SUBSCRIPTIONS_ENABLED':True, 'BILLING_PROVIDER_ENABLED':True,
        'PAYSTACK_SECRET_KEY':'sk_test_fixture', 'PAYSTACK_PUBLIC_KEY':'pk_test_fixture',
        'PAYSTACK_BASIC_MONTHLY_PLAN':'PLN_basic_monthly'})
    transport = FakeProvider(); monkeypatch.setattr(provider, 'call', transport.call)
    with application.app_context():
        db.create_all(); user, _ = seed(eligible=False); uid = user.id
        billing.begin_checkout(user, 'basic', 'monthly')
        sub = RecurringSubscription.query.one(); ref = sub.checkout_reference; sid = sub.id
        event = {'event':'subscription.create', 'data':{'domain':'test','subscription_code':'SUB_new'}}
        if scenario == 'renewal':
            billing.verify_checkout(user, ref)
            start = sub.current_period_end; expected_end = start+timedelta(days=28)
            tx = transport.verified(ref)
            tx.update(reference='concurrent-renewal',paid_at=iso(start),metadata={})
            transport.transactions['concurrent-renewal'] = tx
            event = renewal_event(sub, 'concurrent-renewal', start, expected_end)
        baseline_payments = Payment.query.count()
    barrier = Barrier(2, timeout=5)
    original_fetch = provider.fetch_subscription
    def coordinated_fetch(code):
        result = original_fetch(code)
        barrier.wait()
        return result
    monkeypatch.setattr(provider, 'fetch_subscription', coordinated_fetch)
    body = json.dumps(event).encode()
    signature = hmac.new(b'sk_test_fixture', body, hashlib.sha512).hexdigest()
    def worker(index):
        with application.app_context():
            client = application.test_client()
            if scenario == 'callback_webhook' and index == 0:
                with client.session_transaction() as session:
                    session['_user_id'] = str(uid); session['_fresh'] = True
                response = client.get('/plans/callback?reference='+ref)
            else:
                response = client.post('/payments/webhook', data=body,
                    headers={'x-paystack-signature':signature})
            return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, i) for i in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(results) == ([200, 302] if scenario == 'callback_webhook' else [200, 200])
    with application.app_context():
        sub = db.session.get(RecurringSubscription, sid)
        assert Payment.query.count() == baseline_payments + (1 if scenario == 'renewal' else 0)
        assert BillingEvent.query.filter_by(kind='subscription_paid').count() == (2 if scenario == 'renewal' else 1)
        assert BillingEvent.query.filter_by(kind='provider_event').filter(
            BillingEvent.event_key.like('payment-period:%')).count() == (2 if scenario == 'renewal' else 1)
        assert sub.current_period_end is not None
        if scenario == 'renewal':
            assert sub.current_period_end == expected_end
        db.session.remove()
