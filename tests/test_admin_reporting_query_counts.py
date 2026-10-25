"""Reproducible local SQLite request measurements; never a hosted profiler."""
from datetime import datetime
import json
from time import perf_counter
from flask import g
from sqlalchemy import event
import pytest
from app import db
from app.models import BillingEvent, User
from app.businesses.service import selection_key
from tests.test_admin_portal import client, admin_login
from tests.test_admin_subscription_reporting import customer
from app.subscriptions.entitlements import PLANS


@pytest.mark.parametrize('size', [10, 100])
def test_measure_reporting_requests(client, monkeypatch, size):
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only measurement attempted an external action')
    monkeypatch.setattr('app.admin.routes.verify_transaction', forbidden)
    monkeypatch.setattr('app.admin.routes.safely_send', forbidden)
    client.application.config['SUBSCRIPTIONS_ENABLED'] = True
    admin = User(full_name='Operator', email='admin@example.com', role='admin',
                 email_verified_at=datetime.utcnow())
    admin.set_password('safe-admin-password123')
    db.session.add(admin)
    for i in range(size):
        plan, interval = list(PLANS)[i % len(PLANS)]
        user, shops, sub = customer(plan, interval, businesses=3)
        # Only the primary business carries the historical active marker.
        for shop in shops[1:]:
            shop.subscription_status = 'inactive'
        if plan == 'basic':
            db.session.add(BillingEvent(user_id=user.id, event_key=selection_key(user),
                kind='business_selection', message='Synthetic approved primary selection'))
    db.session.commit()
    assert User.query.filter_by(role='user').count() == size
    admin_login(client)
    paths = ['/admin/', '/admin/reports', '/api/admin/reports', '/admin/notifications']
    for path in paths:
        samples = []
        for repeat in range(3):
            # Keep cold ORM identity maps; first sample also includes cold templates.
            g.pop('_login_user', None)
            db.session.remove()
            statements = []
            def record(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement.lstrip().split()[0].upper())
            engine = db.engine
            event.listen(engine, 'before_cursor_execute', record)
            try:
                started = perf_counter()
                response = client.get(path)
                elapsed = (perf_counter()-started)*1000
            finally:
                event.remove(engine, 'before_cursor_execute', record)
            assert response.status_code == 200
            assert statements and set(statements) == {'SELECT'}
            samples.append({'queries': len(statements), 'elapsed_ms': round(elapsed, 3)})
        print('MEASUREMENT ' + json.dumps({'customers': size, 'businesses': size*3,
              'path': path, 'samples': samples}), flush=True)
