"""#3 (Zoho <-> bot sync, both directions), #4 (language-based ticket
routing + bulk reassignment), #5 (72-hour reopen window), #6/#7 (ticket
filters + CSV export). Runs against a real Postgres instance, same
pattern as the other dashboard/ticket tests here.

Route-level checks (webhook, agent-messages polling) import the real
app.py — needed since those routes live on its Flask `app` directly, not
the dashboard blueprint (unlike tests/test_admin_auth.py, which builds a
standalone Flask app specifically to avoid app.py's bootstrap/secret-key
side effects; those don't matter for what's being tested here).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
from dashboard import db as dashboard_db
from services import ticket_service


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
def client():
    return app_module.app.test_client()


def _make_user(email, role, languages=None):
    return dashboard_db.create_admin_user(email, generate_password_hash("pass12345"), role, languages)


def _make_ticket(user_id="u1", language=None):
    dashboard_db.ensure_conversation(f"sess-{user_id}", user_id)
    dashboard_db.record_ticket(
        f"AST-{user_id}", f"sess-{user_id}", user_id, "technical", "app_crash", "desc",
        None, "mid", language=language,
    )
    return dashboard_db.list_tickets(limit=1)[0]


# --- #4: language-aware round robin --------------------------------------

def test_round_robin_prefers_matching_language():
    hindi_agent = _make_user("hindi@x.com", "support_agent", languages=["hi"])
    _make_user("general@x.com", "support_agent")  # no language preference

    t1 = _make_ticket("u1", language="hi")
    assert t1["assigned_admin_id"] == hindi_agent["id"]


def test_round_robin_falls_back_to_anyone_when_no_language_match():
    general_agent = _make_user("general@x.com", "support_agent")

    t1 = _make_ticket("u1", language="ta")  # nobody speaks Tamil
    assert t1["assigned_admin_id"] == general_agent["id"]


def test_round_robin_with_no_language_ignores_language_field():
    admin = _make_user("admin@x.com", "admin")
    t1 = _make_ticket("u1", language=None)
    assert t1["assigned_admin_id"] == admin["id"]


# --- #4: bulk reassignment -------------------------------------------------

def test_bulk_update_ticket_assignee():
    a1 = _make_user("a1@x.com", "support_agent")
    a2 = _make_user("a2@x.com", "support_agent")
    t1 = _make_ticket("u1")
    t2 = _make_ticket("u2")

    changed = dashboard_db.bulk_update_ticket_assignee([t1["id"], t2["id"]], a2["id"])
    assert changed == 2
    assert dashboard_db.get_ticket(t1["id"])["assigned_admin_id"] == a2["id"]
    assert dashboard_db.get_ticket(t2["id"])["assigned_admin_id"] == a2["id"]


def test_bulk_reassign_route_is_admin_only(client):
    _make_user("admin@x.com", "admin")
    _make_user("agent@x.com", "support_agent")
    t1 = _make_ticket("u1")

    client.post("/admin/login", data={"email": "agent@x.com", "password": "pass12345"})
    resp = client.post("/admin/tickets/bulk-reassign", data={"ticket_ids": [str(t1["id"])]})
    assert resp.status_code == 403


# --- #5: 72-hour reopen window ---------------------------------------------

def test_reopen_within_window_succeeds():
    dashboard_db.ensure_conversation("sess-u1", "u1")
    dashboard_db.record_ticket("AST-U1", "sess-u1", "u1", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]
    dashboard_db.update_ticket_status(ticket["id"], "Resolved", "fixed")

    result = ticket_service.reopen_ticket("u1")
    assert result["reopened"] is True
    assert dashboard_db.get_ticket(ticket["id"])["status"] == "Open"


def test_reopen_past_window_is_refused():
    dashboard_db.ensure_conversation("sess-u1", "u1")
    dashboard_db.record_ticket("AST-U1", "sess-u1", "u1", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]
    dashboard_db.update_ticket_status(ticket["id"], "Resolved", "fixed")

    # Backdate resolved_at past the 73-hour mark directly — reopen_ticket
    # must respect real elapsed time, not just "was it ever resolved".
    old_time = datetime.now(timezone.utc) - timedelta(hours=73)
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tickets SET resolved_at = %s WHERE id = %s", (old_time, ticket["id"]))

    result = ticket_service.reopen_ticket("u1")
    assert result["reopened"] is False
    assert result["reason"] == "past_reopen_window"
    assert dashboard_db.get_ticket(ticket["id"])["status"] == "Resolved"


def test_reopen_with_no_resolved_ticket():
    result = ticket_service.reopen_ticket("nobody")
    assert result == {"reopened": False, "reason": "no_resolved_ticket"}


# --- #2: conversation transcript reaches Zoho -----------------------------

def test_create_ticket_includes_conversation_transcript(monkeypatch):
    from integrations import zoho_client
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    dashboard_db.ensure_conversation("sess-transcript", "u_transcript")
    dashboard_db.record_turn("sess-transcript", "u_transcript", "my payment failed", "Sorry to hear that",
                              "en", "agent", [], card_shown=False)

    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "zoho-1"}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return Resp()

    with patch("integrations.zoho_client.requests.post", side_effect=fake_post):
        ticket_service.create_ticket("u_transcript", "payment", "recharge_failed", "desc", session_id="sess-transcript")

    assert "my payment failed" in captured["json"]["description"]
    assert "Sorry to hear that" in captured["json"]["description"]


# --- #3: Zoho webhook (inbound sync) ---------------------------------------

def test_webhook_rejects_without_correct_secret(client, monkeypatch):
    monkeypatch.setattr(app_module, "ZOHO_WEBHOOK_SECRET", "correct-secret")
    resp = client.post("/webhooks/zoho", json={"zoho_ticket_id": "z1"})
    assert resp.status_code == 401

    resp2 = client.post(
        "/webhooks/zoho", json={"zoho_ticket_id": "z1"},
        headers={"X-Webhook-Secret": "wrong"},
    )
    assert resp2.status_code == 401


def test_webhook_syncs_status_category_and_agent_reply(client, monkeypatch):
    monkeypatch.setattr(app_module, "ZOHO_WEBHOOK_SECRET", "correct-secret")

    dashboard_db.ensure_conversation("sess-wh", "u_wh")
    dashboard_db.record_ticket("AST-WH", "sess-wh", "u_wh", "technical", "x", "d", None, "mid",
                                zoho_ticket_id="zoho-123")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    resp = client.post(
        "/webhooks/zoho",
        json={
            "zoho_ticket_id": "zoho-123",
            "status": "In Progress",
            "category": "Payment Queries",
            "sub_category": "Refund",
            "comment": "We're looking into your refund now.",
            "agent_name": "Priya",
        },
        headers={"X-Webhook-Secret": "correct-secret"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["matched"] is True

    updated = dashboard_db.get_ticket(ticket["id"])
    assert updated["status"] == "In Progress"
    assert updated["category"] == "Payment Queries"
    assert updated["sub_category"] == "Refund"

    agent_messages = dashboard_db.get_new_agent_messages("sess-wh", None)
    assert len(agent_messages) == 1
    assert "Priya" in agent_messages[0]["text"]
    assert "refund" in agent_messages[0]["text"]


def test_webhook_ignores_unknown_zoho_ticket_id(client, monkeypatch):
    monkeypatch.setattr(app_module, "ZOHO_WEBHOOK_SECRET", "correct-secret")
    resp = client.post(
        "/webhooks/zoho", json={"zoho_ticket_id": "does-not-exist", "status": "Closed"},
        headers={"X-Webhook-Secret": "correct-secret"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["matched"] is False


def test_webhook_ignores_invalid_status_value(client, monkeypatch):
    monkeypatch.setattr(app_module, "ZOHO_WEBHOOK_SECRET", "correct-secret")
    dashboard_db.ensure_conversation("sess-wh2", "u_wh2")
    dashboard_db.record_ticket("AST-WH2", "sess-wh2", "u_wh2", "technical", "x", "d", None, "mid",
                                zoho_ticket_id="zoho-999")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    client.post(
        "/webhooks/zoho", json={"zoho_ticket_id": "zoho-999", "status": "SomeRandomZohoStatus"},
        headers={"X-Webhook-Secret": "correct-secret"},
    )
    assert dashboard_db.get_ticket(ticket["id"])["status"] == "Open"  # unchanged


# --- #3: visitor-chat polling for agent replies ----------------------------

def test_agent_messages_polling_endpoint(client):
    dashboard_db.ensure_conversation("sess-poll", "u_poll")
    dashboard_db.record_agent_message("sess-poll", "hello from support", "Priya")

    resp = client.get("/conversations/sess-poll/agent-messages")
    data = resp.get_json()
    assert len(data["messages"]) == 1
    assert "hello from support" in data["messages"][0]["text"]


def test_agent_messages_polling_respects_since():
    dashboard_db.ensure_conversation("sess-poll2", "u_poll2")
    dashboard_db.record_agent_message("sess-poll2", "first message")
    checkpoint = datetime.now(timezone.utc).isoformat()
    dashboard_db.record_agent_message("sess-poll2", "second message")

    messages = dashboard_db.get_new_agent_messages("sess-poll2", checkpoint)
    assert len(messages) == 1
    assert "second message" in messages[0]["text"]


# --- Support-agent reply direct from this dashboard (no Zoho needed) ------

def test_send_agent_reply_reaches_visitor_chat():
    dashboard_db.ensure_conversation("sess-reply", "u_reply")
    dashboard_db.record_ticket("AST-REPLY", "sess-reply", "u_reply", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    ok = ticket_service.send_agent_reply(ticket["id"], "We've fixed your account issue.", "Priya")
    assert ok is True

    messages = dashboard_db.get_new_agent_messages("sess-reply", None)
    assert len(messages) == 1
    assert "Priya" in messages[0]["text"]
    assert "fixed your account issue" in messages[0]["text"]


def test_send_agent_reply_ignores_blank_text():
    dashboard_db.ensure_conversation("sess-reply2", "u_reply2")
    dashboard_db.record_ticket("AST-REPLY2", "sess-reply2", "u_reply2", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    assert ticket_service.send_agent_reply(ticket["id"], "   ", "Priya") is False
    assert dashboard_db.get_new_agent_messages("sess-reply2", None) == []


def test_ticket_detail_route_sends_reply(client):
    _make_user("agent@x.com", "support_agent")
    dashboard_db.ensure_conversation("sess-reply3", "u_reply3")
    dashboard_db.record_ticket("AST-REPLY3", "sess-reply3", "u_reply3", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    client.post("/admin/login", data={"email": "agent@x.com", "password": "pass12345"})
    resp = client.post(f"/admin/tickets/{ticket['id']}", data={"reply": "Checking this now."})
    assert resp.status_code == 302

    messages = dashboard_db.get_new_agent_messages("sess-reply3", None)
    assert len(messages) == 1
    assert "agent@x.com" in messages[0]["text"]
    assert "Checking this now." in messages[0]["text"]


def test_ticket_detail_reply_route_is_agent_or_admin_only(client):
    dashboard_db.create_admin_user("analyst@x.com", generate_password_hash("pass12345"), "analyst")
    dashboard_db.ensure_conversation("sess-reply4", "u_reply4")
    dashboard_db.record_ticket("AST-REPLY4", "sess-reply4", "u_reply4", "technical", "x", "d", None, "mid")
    ticket = dashboard_db.list_tickets(limit=1)[0]

    client.post("/admin/login", data={"email": "analyst@x.com", "password": "pass12345"})
    resp = client.post(f"/admin/tickets/{ticket['id']}", data={"reply": "Should not be allowed."})
    assert resp.status_code == 403
    assert dashboard_db.get_new_agent_messages("sess-reply4", None) == []


# --- #6/#7: ticket filters + CSV export -------------------------------------

def test_tickets_list_filters_by_language():
    _make_ticket("u1", language="hi")
    _make_ticket("u2", language="ta")

    hindi_only = dashboard_db.list_tickets(language="hi")
    assert len(hindi_only) == 1
    assert hindi_only[0]["user_id"] == "u1"


def test_export_tickets_csv(client):
    _make_user("admin@x.com", "admin")
    _make_ticket("u1", language="hi")

    client.post("/admin/login", data={"email": "admin@x.com", "password": "pass12345"})
    resp = client.get("/admin/tickets/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.get_data(as_text=True)
    assert "Ticket,User,Category" in body
    assert "u1" in body
