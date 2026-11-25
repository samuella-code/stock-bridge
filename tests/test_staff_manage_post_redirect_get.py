"""Regression coverage for staff invitation Post/Redirect/Get behaviour."""
from datetime import datetime
from unittest.mock import patch
import pytest
from app import db
from app.models import StaffInvitation
from tests.test_business_insights import app, seed, login, NOW


@pytest.fixture(autouse=True)
def staff_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls):
            return NOW
    monkeypatch.setattr('app.staff.service.datetime', Clock)
    monkeypatch.setattr('app.staff.routes.datetime', Clock)
    monkeypatch.setattr('app.staff.roles.datetime', Clock)
    monkeypatch.setattr('app.staff.delivery.datetime', Clock)


def test_invitation_success_redirect_refresh_never_replays_post(app):
    owner, businesses = seed('pro')
    client = app.test_client()
    login(client, owner)

    with patch('app.staff.routes.delivery.deliver', return_value='sent'):
        response = client.post('/staff/manage', data={
            'action': 'invite',
            'email': 'refresh-safe@example.invalid',
            'role': 'salesperson',
            'duty_label': 'Evening shift',
            'business_ids': [str(businesses[0].id)],
        })

    assert response.status_code == 303
    assert response.location.startswith('/staff/manage#invite-token=')
    token = response.location.split('invite-token=', 1)[1]
    assert len(token) == 43
    assert StaffInvitation.query.filter_by(email='refresh-safe@example.invalid').count() == 1

    # Fragments are browser-only: the GET contains no token and cannot repeat
    # the POST, even when the owner refreshes the resulting page.
    page = client.get('/staff/manage')
    assert page.status_code == 200
    assert b'data-staff-invite-success' in page.data
    assert b'data-invite-endpoint="/staff/invite"' in page.data
    assert StaffInvitation.query.filter_by(email='refresh-safe@example.invalid').count() == 1
    assert client.get('/staff/manage').status_code == 200
    assert StaffInvitation.query.filter_by(email='refresh-safe@example.invalid').count() == 1
