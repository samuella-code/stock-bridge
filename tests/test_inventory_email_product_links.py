"""Focused regression tests for secure product-specific inventory email links."""
from app import db
from app.models import EmailOutbox, Product
from tests.test_notifications_business_profile import app, setup, product, sale
from tests.test_multi_business import seed, login


def test_inventory_email_uses_product_specific_restock_link(app, monkeypatch):
    app.config.update(
        INVENTORY_EMAILS_ENABLED=True,
        INVENTORY_EMAIL_BASE_URL='https://stock-bridge-staging.vercel.app',
        INVENTORY_EMAIL_MAX_ATTEMPTS=3,
    )
    from app.notifications.staging_email_policy import Policy
    monkeypatch.setattr('app.notifications.staging_email_policy.policy', lambda now=None: Policy('production'))
    monkeypatch.setattr('app.notifications.email_outbox.policy', lambda now=None: Policy('production'))
    client, _, businesses = setup(app)
    item = product(client, 'Coffee')
    sale(client, item, 3)
    sent = []
    monkeypatch.setattr(
        'app.email_service._send_email',
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    from app.notifications.email_outbox import dispatch_one
    assert dispatch_one() == 'sent'

    expected = (
        f'https://stock-bridge-staging.vercel.app/notifications/email/product/'
        f'{businesses[0].id}/{item.id}?action=restock'
    )
    assert expected in sent[0][0][2]
    assert expected.replace('?action=restock', '') in sent[0][1]['html']


def test_email_product_link_restores_correct_owned_business(app):
    client, _, businesses = setup(app, 2)
    first_item = product(client, 'First shop item')
    client.post(f'/businesses/{businesses[1].id}/switch')
    product(client, 'Second shop item')

    response = client.get(
        f'/notifications/email/product/{businesses[0].id}/{first_item.id}?action=restock'
    )

    assert response.status_code == 302
    assert response.location.endswith(f'/restocking/?product_id={first_item.id}')
    with client.session_transaction() as state:
        assert state.get('active_business_id') == businesses[0].id


def test_email_product_link_rejects_another_owners_product(app):
    client, _, businesses = setup(app)
    item = product(client, 'Private item')
    other, _ = seed('basic', email='another-owner@example.invalid')
    login(client, other)

    response = client.get(
        f'/notifications/email/product/{businesses[0].id}/{item.id}?action=restock'
    )

    assert response.status_code == 404


def test_email_product_link_rejects_inactive_product(app):
    client, _, businesses = setup(app)
    item = product(client, 'Archived item')
    item.active = False
    db.session.commit()

    response = client.get(
        f'/notifications/email/product/{businesses[0].id}/{item.id}?action=restock'
    )

    assert response.status_code == 404


def test_email_product_link_rejects_product_from_different_business(app):
    client, _, businesses = setup(app, 2)
    client.post(f'/businesses/{businesses[1].id}/switch')
    item = product(client, 'Second shop item')
    response = client.get(
        f'/notifications/email/product/{businesses[0].id}/{item.id}?action=restock'
    )
    assert response.status_code == 404
