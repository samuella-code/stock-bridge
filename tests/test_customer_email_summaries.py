"""Customer summary queueing and rendering tests; SMTP is always mocked."""
from datetime import datetime

from app import db
from app.models import EmailOutbox, Expense, NotificationPreference, Product
from app.notifications.summaries import queue_due_summaries, _wat_window
from tests.test_notifications_business_profile import app, setup, product, sale


def _summary_preferences(business):
    db.session.add(NotificationPreference(
        business_id=business.id,
        low_stock_email=False,
        out_of_stock_email=False,
        daily_summary_email=True,
        weekly_summary_email=True,
    ))
    db.session.commit()


def test_daily_and_weekly_summaries_queue_once_per_business_period(app):
    client, owner, businesses = setup(app)
    business = businesses[0]
    _summary_preferences(business)
    item = product(client, 'Coffee')
    sale(client, item, 3)  # 8 -> 5: needs restocking.
    db.session.add(Expense(
        business_id=business.id, description='Transport', amount=1200
    ))
    db.session.commit()

    assert queue_due_summaries() == 2
    rows = EmailOutbox.query.order_by(EmailOutbox.event_type).all()
    assert [row.event_type for row in rows] == ['daily_summary', 'weekly_summary']
    daily = next(row for row in rows if row.event_type == 'daily_summary')
    weekly = next(row for row in rows if row.event_type == 'weekly_summary')
    assert daily.recipient_user_id == weekly.recipient_user_id == owner.id
    assert daily.payload['low_stock_count'] == 1
    assert daily.payload['low_stock_products'][0]['name'] == 'Coffee'
    assert weekly.payload['sales'] == 1050.0
    assert weekly.payload['cost_of_goods'] == 750.0
    assert weekly.payload['expenses'] == 1200.0
    assert weekly.payload['kept'] == -900.0
    assert weekly.payload['best_seller'] == {'name': 'Coffee', 'quantity': 3}
    assert queue_due_summaries() == 0
    assert EmailOutbox.query.count() == 2


def test_summaries_are_opt_in_and_daily_summary_only_sends_when_stock_needs_attention(app):
    client, _, businesses = setup(app)
    item = product(client, 'Tea')
    sale(client, item, 1)  # No threshold crossing; still above alert level.
    assert queue_due_summaries() == 0

    preference = NotificationPreference(
        business_id=businesses[0].id,
        daily_summary_email=True,
        weekly_summary_email=False,
    )
    db.session.add(preference)
    db.session.commit()
    assert queue_due_summaries() == 0

    sale(client, item, 2)  # Now reaches the threshold.
    assert queue_due_summaries() == 1
    assert EmailOutbox.query.one().event_type == 'daily_summary'


def test_summary_dispatch_uses_shared_template_and_can_run_without_inventory_alerts(app, monkeypatch):
    from app.notifications.staging_email_policy import Policy

    client, _, businesses = setup(app)
    business = businesses[0]
    _summary_preferences(business)
    item = product(client, 'Coffee')
    sale(client, item, 3)
    assert queue_due_summaries() == 2

    app.config.update(
        INVENTORY_EMAILS_ENABLED=False,
        CUSTOMER_SUMMARY_EMAILS_ENABLED=True,
        CUSTOMER_SUMMARY_EMAIL_BASE_URL='https://stock-bridge-staging.vercel.app',
    )
    monkeypatch.setattr('app.notifications.email_outbox.policy', lambda now=None: Policy('production'))
    sent = []
    monkeypatch.setattr(
        'app.email_service._send_email',
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    from app.notifications.email_outbox import dispatch_one
    assert dispatch_one() == 'sent'
    assert dispatch_one() == 'sent'
    assert len(sent) == 2
    assert all('email/base' not in kwargs['html'] for _, kwargs in sent)
    assert any('RESTOCK SUMMARY' in kwargs['html'] for _, kwargs in sent)
    weekly = next((args, kwargs) for args, kwargs in sent if 'weekly' in args[0].lower())
    assert 'Sales: ₦1,050.00' in weekly[0][2]
    assert 'Kept: ₦300.00' in weekly[0][2]
    assert 'Coffee' in weekly[1]['html']


def test_summary_window_uses_wat_midnight_and_previous_monday():
    start, end, day = _wat_window(datetime(2026, 11, 26, 10, 0), 1)
    assert (start, end, day.isoformat()) == (
        datetime(2026, 11, 24, 23), datetime(2026, 11, 25, 23), '2026-11-25'
    )
    start, end, week = _wat_window(datetime(2026, 11, 26, 10, 0), 7)
    assert (start, end, week.isoformat()) == (
        datetime(2026, 11, 15, 23), datetime(2026, 11, 22, 23), '2026-11-16'
    )
