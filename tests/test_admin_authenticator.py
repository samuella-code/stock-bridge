import base64
import re
import time

from cryptography.fernet import Fernet
import pytest

from app import db
from app.admin import mfa
from app.models import AdminAuthenticator, AuditLog, User
from tests.test_admin_portal import admin_login, client, seed

SECRET = base64.b32encode(b"12345678901234567890").decode()
PATH = "/admin/verify-authenticator"


@pytest.fixture
def enrolled_flow(client, monkeypatch):
    admin_id, _, _ = seed(client)
    key = Fernet.generate_key()
    client.application.config.update(ADMIN_MFA_REQUIRED=True, ADMIN_MFA_ENCRYPTION_KEY=key)
    monkeypatch.setattr(mfa, "new_secret", lambda: SECRET)
    return client, admin_id, key


def enroll(client, monkeypatch):
    monkeypatch.setattr(mfa, "time", lambda: 59)
    assert admin_login(client).location.endswith(PATH)
    assert client.get("/api/admin/dashboard").status_code == 401
    with client.session_transaction() as session:
        assert "_user_id" not in session
        assert "admin_session" not in session
        assert SECRET not in str(dict(session))
    response = client.get(PATH)
    assert b"Set up your authenticator" in response.data
    assert b"data:image/svg+xml;base64," in response.data
    assert response.headers["Cache-Control"] == "no-store, private"
    response = client.post(PATH, data={"code": "287082"})
    assert response.status_code == 200
    assert b"Save your recovery codes" in response.data
    return re.findall(rb"<li><code>([a-f0-9]{12}-[a-f0-9]{12})</code></li>", response.data)


def test_enrollment_replay_and_recovery(enrolled_flow, monkeypatch):
    client, admin_id, key = enrolled_flow
    codes = [code.decode() for code in enroll(client, monkeypatch)]
    assert len(set(codes)) == 8
    assert client.get("/api/admin/dashboard").status_code == 200
    assert client.get(PATH).status_code == 302
    with client.application.app_context():
        factor = db.session.get(AdminAuthenticator, admin_id)
        assert factor.encrypted_secret != SECRET.encode()
        assert Fernet(key).decrypt(factor.encrypted_secret) == SECRET.encode()
        assert factor.recovery_digests == [mfa.digest_token(code) for code in codes]
        assert factor.challenge_digest is None

    client.post("/admin/logout")
    admin_login(client)
    assert b"Enter your 6-digit code" in client.get(PATH).data
    assert SECRET.encode() not in client.get(PATH).data
    assert client.post(PATH, data={"code": "287082"}).status_code == 401

    monkeypatch.setattr(mfa, "time", lambda: 1111111109)
    assert client.post(PATH, data={"code": "081804"}).location.endswith("/admin/")
    client.post("/admin/logout")
    admin_login(client)
    assert b"Use a recovery code" in client.get(PATH, query_string={"method": "recovery"}).data
    assert client.post(PATH, data={"method": "recovery", "code": codes[0].upper()}).location.endswith("/admin/")
    client.post("/admin/logout")
    admin_login(client)
    assert client.post(PATH, data={"method": "recovery", "code": codes[0]}).status_code == 401
    with client.application.app_context():
        assert db.session.get(AdminAuthenticator, admin_id).recovery_digests == [
            mfa.digest_token(code) for code in codes[1:]
        ]
        assert AuditLog.query.filter_by(action="ADMIN_MFA_RECOVERY_USED").count() == 1
        assert AuditLog.query.filter_by(action="ADMIN_LOGIN_SUCCESS").count() == 3


def test_throttle_survives_new_challenges_and_ip_changes(enrolled_flow):
    client, _, _ = enrolled_flow
    admin_login(client)
    for _ in range(5):
        assert client.post(PATH, data={"code": "invalid"}).status_code == 401
    admin_login(client)
    response = client.post(PATH, data={"code": "invalid"}, environ_overrides={"REMOTE_ADDR": "192.0.2.7"})
    assert response.status_code == 429
    assert client.get("/api/admin/dashboard").status_code == 401


def test_old_session_challenge_expiry_and_changed_credentials(enrolled_flow):
    client, admin_id, _ = enrolled_flow
    with client.session_transaction() as session:
        session.update(_user_id=str(admin_id), admin_session=True, admin_last_activity=time.time(), admin_auth_version=0)
    assert client.get("/api/admin/dashboard").status_code == 401
    assert client.post(PATH, data={"code": "287082"}).location.endswith("/admin/login")
    admin_login(client)
    with client.application.app_context():
        factor = db.session.get(AdminAuthenticator, admin_id)
        factor.challenge_expires_at = factor.challenge_expires_at.replace(year=2000)
        db.session.commit()
    assert client.get(PATH).location.endswith("/admin/login")
    admin_login(client)
    with client.application.app_context():
        db.session.get(User, admin_id).admin_auth_version += 1
        db.session.commit()
    assert client.post(PATH, data={"code": "287082"}).location.endswith("/admin/login")


def test_missing_key_fails_closed(enrolled_flow):
    client, admin_id, _ = enrolled_flow
    client.application.config["ADMIN_MFA_ENCRYPTION_KEY"] = ""
    assert admin_login(client).status_code == 503
    assert client.get("/api/admin/dashboard").status_code == 401
    with client.application.app_context():
        assert db.session.get(AdminAuthenticator, admin_id) is None


def test_new_challenge_invalidates_the_previous_browser(enrolled_flow, monkeypatch):
    client, _, _ = enrolled_flow
    monkeypatch.setattr(mfa, "time", lambda: 59)
    second_browser = client.application.test_client()
    assert admin_login(client).location.endswith(PATH)
    assert admin_login(second_browser).location.endswith(PATH)
    assert client.post(PATH, data={"code": "287082"}).location.endswith("/admin/login")
    assert second_browser.post(PATH, data={"code": "287082"}).status_code == 200


def test_verification_requires_csrf(enrolled_flow, monkeypatch):
    client, _, _ = enrolled_flow
    monkeypatch.setattr(mfa, "time", lambda: 59)
    admin_login(client)
    client.application.config["WTF_CSRF_ENABLED"] = True
    assert client.post(PATH, data={"code": "287082"}).status_code == 400
    page = client.get(PATH)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert client.post(PATH, data={"code": "287082", "csrf_token": token}).status_code == 200


def test_pilot_only_requires_verification_for_selected_admin(enrolled_flow, monkeypatch):
    client, _, _ = enrolled_flow
    client.application.config.update(ADMIN_MFA_REQUIRED=False, ADMIN_MFA_PILOT_EMAIL="admin@example.com")
    with client.application.app_context():
        other = User(full_name="Other admin", email="other-admin@example.com", role="admin")
        other.set_password("other-admin-password456")
        db.session.add(other)
        db.session.commit()
    other_browser = client.application.test_client()
    response = other_browser.post("/admin/login", data={
        "email": "other-admin@example.com", "password": "other-admin-password456",
    })
    assert response.location.endswith("/admin/")
    assert other_browser.get("/api/admin/dashboard").status_code == 200
    assert other_browser.post("/admin/logout").status_code == 302
    assert len(enroll(client, monkeypatch)) == 8
    assert client.get("/api/admin/dashboard").status_code == 200
