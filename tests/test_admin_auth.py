"""dashboard/auth.py's email+password login and role-based access control,
plus dashboard/db.py's round-robin ticket assignment. Runs against a real
Postgres instance, same pattern as the other dashboard tests here.

Route-level (role_required/admin_required) checks build a standalone Flask
app around dashboard.routes.bp directly, rather than importing the real
app.py — app.py has import-time side effects (bootstrapping an admin from
whatever ADMIN_EMAIL/ADMIN_PASSWORD happen to be set in the environment,
picking a real DB-persisted secret_key) that don't belong in a test run.
"""
import os

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


def _make_user(email, password, role):
    return dashboard_db.create_admin_user(email, generate_password_hash(password), role)


# --- bootstrap ------------------------------------------------------------

def test_bootstrap_creates_first_admin_from_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "first@astrolokal.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "bootstrappass1")

    auth.bootstrap()

    users = dashboard_db.list_admin_users()
    assert len(users) == 1
    assert users[0]["email"] == "first@astrolokal.com"
    assert users[0]["role"] == "admin"


def test_bootstrap_is_a_noop_once_any_user_exists(monkeypatch):
    _make_user("existing@astrolokal.com", "whatever123", "admin")
    monkeypatch.setenv("ADMIN_EMAIL", "second@astrolokal.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "wouldbeadmin1")

    auth.bootstrap()

    users = dashboard_db.list_admin_users()
    assert len(users) == 1
    assert users[0]["email"] == "existing@astrolokal.com"


def test_bootstrap_does_nothing_without_env_vars(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    auth.bootstrap()

    assert dashboard_db.list_admin_users() == []


# --- authenticate -----------------------------------------------------

def test_authenticate_success():
    _make_user("agent@astrolokal.com", "correcthorse1", "support_agent")
    user = auth.authenticate("agent@astrolokal.com", "correcthorse1")
    assert user is not None
    assert user["role"] == "support_agent"


def test_authenticate_wrong_password():
    _make_user("agent@astrolokal.com", "correcthorse1", "support_agent")
    assert auth.authenticate("agent@astrolokal.com", "wrongpassword") is None


def test_authenticate_unknown_email():
    assert auth.authenticate("nobody@astrolokal.com", "anything123") is None


def test_authenticate_email_is_case_insensitive():
    _make_user("agent@astrolokal.com", "correcthorse1", "support_agent")
    assert auth.authenticate("Agent@AstroLokal.com", "correcthorse1") is not None


def test_create_admin_user_rejects_duplicate_email():
    _make_user("dup@astrolokal.com", "firstpass123", "admin")
    second = dashboard_db.create_admin_user("dup@astrolokal.com", generate_password_hash("secondpass1"), "analyst")
    assert second is None
    assert len(dashboard_db.list_admin_users()) == 1


# --- role_required / admin_required routes --------------------------------

def _login(client, email, password):
    return client.post("/admin/login", data={"email": email, "password": password}, follow_redirects=False)


def test_analytics_blocks_support_agent_but_allows_admin_and_analyst(client):
    _make_user("admin@astrolokal.com", "adminpass123", "admin")
    _make_user("agent@astrolokal.com", "agentpass123", "support_agent")
    _make_user("analyst@astrolokal.com", "analystpass1", "analyst")

    _login(client, "admin@astrolokal.com", "adminpass123")
    assert client.get("/admin/analytics").status_code == 200
    client.post("/admin/logout")

    _login(client, "analyst@astrolokal.com", "analystpass1")
    assert client.get("/admin/analytics").status_code == 200
    client.post("/admin/logout")

    _login(client, "agent@astrolokal.com", "agentpass123")
    assert client.get("/admin/analytics").status_code == 403


def test_users_page_is_admin_only(client):
    _make_user("admin@astrolokal.com", "adminpass123", "admin")
    _make_user("agent@astrolokal.com", "agentpass123", "support_agent")

    _login(client, "agent@astrolokal.com", "agentpass123")
    assert client.get("/admin/users").status_code == 403
    client.post("/admin/logout")

    _login(client, "admin@astrolokal.com", "adminpass123")
    assert client.get("/admin/users").status_code == 200


def test_unauthenticated_request_redirects_to_login(client):
    resp = client.get("/admin/tickets", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_cannot_demote_or_delete_the_last_admin(client):
    admin = _make_user("solo@astrolokal.com", "adminpass123", "admin")
    _login(client, "solo@astrolokal.com", "adminpass123")

    resp = client.post(f"/admin/users/{admin['id']}/role", data={"role": "analyst"})
    assert resp.status_code == 403
    assert dashboard_db.get_admin_user_by_id(admin["id"])["role"] == "admin"

    resp2 = client.post(f"/admin/users/{admin['id']}/delete")
    assert resp2.status_code == 403
    assert dashboard_db.get_admin_user_by_id(admin["id"]) is not None


def test_demoting_a_non_last_admin_succeeds(client):
    admin1 = _make_user("one@astrolokal.com", "adminpass123", "admin")
    admin2 = _make_user("two@astrolokal.com", "adminpass123", "admin")
    _login(client, "one@astrolokal.com", "adminpass123")

    resp = client.post(f"/admin/users/{admin2['id']}/role", data={"role": "support_agent"})
    assert resp.status_code == 302
    assert dashboard_db.get_admin_user_by_id(admin2["id"])["role"] == "support_agent"
    # The demoted-from admin is untouched.
    assert dashboard_db.get_admin_user_by_id(admin1["id"])["role"] == "admin"


def test_analyst_cannot_update_ticket_status(client):
    admin = _make_user("admin@astrolokal.com", "adminpass123", "admin")
    analyst = _make_user("analyst@astrolokal.com", "analystpass1", "analyst")
    dashboard_db.ensure_conversation("sess-1", "user-1")
    dashboard_db.record_ticket("AST-TEST01", "sess-1", "user-1", "technical", "app_crash", "desc", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    _login(client, "analyst@astrolokal.com", "analystpass1")
    resp = client.post(f"/admin/tickets/{ticket['id']}", data={"status": "Resolved", "note": ""})
    assert resp.status_code == 403
    assert dashboard_db.get_ticket(ticket["id"])["status"] == "Open"


# --- round-robin ticket assignment -----------------------------------

def test_ticket_auto_assigns_round_robin_across_admin_and_support_agent_only():
    admin = _make_user("admin@astrolokal.com", "adminpass123", "admin")
    agent = _make_user("agent@astrolokal.com", "agentpass123", "support_agent")
    _make_user("analyst@astrolokal.com", "analystpass1", "analyst")  # never eligible

    for i in range(4):
        dashboard_db.ensure_conversation(f"sess-{i}", f"user-{i}")
        dashboard_db.record_ticket(f"AST-T{i}", f"sess-{i}", f"user-{i}", "technical", "x", "d", None, "mid")

    tickets = dashboard_db.list_tickets(limit=10)
    assignees = {t["assigned_admin_id"] for t in tickets}
    assert assignees == {admin["id"], agent["id"]}, assignees
    # True round robin, not random: assignment alternates in creation order.
    ordered = sorted(tickets, key=lambda t: t["created_at"])
    assert [t["assigned_admin_id"] for t in ordered] == [admin["id"], agent["id"], admin["id"], agent["id"]]


def test_ticket_left_unassigned_when_nobody_eligible():
    _make_user("analyst@astrolokal.com", "analystpass1", "analyst")
    dashboard_db.ensure_conversation("sess-1", "user-1")
    dashboard_db.record_ticket("AST-T1", "sess-1", "user-1", "technical", "x", "d", None, "mid")

    ticket = dashboard_db.list_tickets(limit=1)[0]
    assert ticket["assigned_admin_id"] is None


def test_manual_reassignment_overrides_round_robin():
    admin = _make_user("admin@astrolokal.com", "adminpass123", "admin")
    dashboard_db.ensure_conversation("sess-1", "user-1")
    dashboard_db.record_ticket("AST-T1", "sess-1", "user-1", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]
    assert ticket["assigned_admin_id"] == admin["id"]

    assert dashboard_db.update_ticket_assignee(ticket["id"], None) is True
    assert dashboard_db.get_ticket(ticket["id"])["assigned_admin_id"] is None
