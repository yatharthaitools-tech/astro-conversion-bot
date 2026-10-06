"""Google Sign-In for the admin dashboard login (dashboard/auth.py's
authenticate_google(), routes.py's /admin/login/google) — offered
alongside email+password, never a parallel way to create an admin
account: a verified Google account only logs in if its email was
already added via the Users page. Mocks
google.oauth2.id_token.verify_oauth2_token (no real network/Google call),
same no-real-network posture as the other external-integration tests
here.
"""
from unittest.mock import patch

import pytest
from flask import Flask
from werkzeug.security import generate_password_hash

from dashboard import auth, db as dashboard_db
from dashboard.routes import bp as dashboard_bp


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE ticket_status_history, tickets, messages, conversations, admin_users CASCADE"
            )
    yield


@pytest.fixture
def app():
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.register_blueprint(dashboard_bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(email, role="support_agent"):
    return dashboard_db.create_admin_user(email, generate_password_hash("irrelevant1"), role)


def _claims(email="agent@astrolokal.com", email_verified=True):
    return {"email": email, "email_verified": email_verified}


# --- authenticate_google() ------------------------------------------------


def test_google_signin_not_configured_by_default(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    assert auth.google_signin_configured() is False
    user, error = auth.authenticate_google("some-token")
    assert user is None
    assert "not configured" in error


def test_authenticate_google_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    with patch("google.oauth2.id_token.verify_oauth2_token", side_effect=ValueError("bad token")):
        user, error = auth.authenticate_google("not-a-real-jwt")
    assert user is None
    assert "verify" in error.lower()


def test_authenticate_google_rejects_unverified_email(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    _make_user("agent@astrolokal.com")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email_verified=False)):
        user, error = auth.authenticate_google("token")
    assert user is None
    assert "not verified" in error


def test_authenticate_google_rejects_wrong_domain(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="agent@gmail.com")):
        user, error = auth.authenticate_google("token")
    assert user is None
    assert "astrolokal.com" in error


def test_authenticate_google_rejects_unregistered_email(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")
    # No admin_users row created for this email — a verified, on-domain
    # Google account must still not be able to log in by itself.
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="nobody@astrolokal.com")):
        user, error = auth.authenticate_google("token")
    assert user is None
    assert "not registered as an admin" in error


def test_authenticate_google_succeeds_for_registered_on_domain_user(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")
    created = _make_user("agent@astrolokal.com", role="support_agent")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="agent@astrolokal.com")):
        user, error = auth.authenticate_google("token")
    assert error is None
    assert user["id"] == created["id"]


def test_authenticate_google_email_match_is_case_insensitive(monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")
    _make_user("agent@astrolokal.com")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="Agent@AstroLokal.COM")):
        user, error = auth.authenticate_google("token")
    assert error is None
    assert user is not None


# --- /admin/login/google route --------------------------------------------


def test_login_google_rejects_missing_csrf_cookie(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    resp = client.post("/admin/login/google", data={"credential": "x", "g_csrf_token": "abc"})
    assert resp.status_code == 400
    assert b"could not be verified" in resp.data


def test_login_google_rejects_mismatched_csrf(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    client.set_cookie("g_csrf_token", "cookie-value")
    resp = client.post("/admin/login/google", data={"credential": "x", "g_csrf_token": "different-value"})
    assert resp.status_code == 400
    assert b"could not be verified" in resp.data


def test_login_google_logs_in_on_valid_token_and_csrf(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")
    _make_user("agent@astrolokal.com", role="support_agent")

    client.set_cookie("g_csrf_token", "matching-token")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="agent@astrolokal.com")):
        resp = client.post(
            "/admin/login/google",
            data={"credential": "real-jwt", "g_csrf_token": "matching-token"},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert "/admin/conversations" in resp.headers["Location"]

    whoami = client.get("/admin/users")
    assert whoami.status_code in (200, 403)  # logged in either way, not redirected to /login


def test_login_google_shows_error_for_unregistered_email(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123")
    monkeypatch.setattr(auth, "GOOGLE_ALLOWED_DOMAIN", "astrolokal.com")

    client.set_cookie("g_csrf_token", "matching-token")
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=_claims(email="ghost@astrolokal.com")):
        resp = client.post(
            "/admin/login/google",
            data={"credential": "real-jwt", "g_csrf_token": "matching-token"},
            follow_redirects=False,
        )

    assert resp.status_code == 200
    assert b"not registered as an admin" in resp.data


def test_login_page_hides_google_button_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "")
    resp = client.get("/admin/login")
    assert b"g_id_onload" not in resp.data


def test_login_page_shows_google_button_when_configured(client, monkeypatch):
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-123.apps.googleusercontent.com")
    resp = client.get("/admin/login")
    assert b"g_id_onload" in resp.data
    assert b"client-123.apps.googleusercontent.com" in resp.data
