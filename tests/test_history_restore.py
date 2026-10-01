"""QA bug #3 ('chat history is not getting saved') — it was actually
always being saved (dashboard_db.record_turn persists every turn), just
never read back: the page always rendered a blank chatBody and the
welcome message again on reload, even with the same session_id still in
sessionStorage. app.py's /history/<session_id> is what script.js's
restoreHistoryOrShowWelcome() now calls to fix that.
"""
import pytest

import app as app_module
from dashboard import db as dashboard_db


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


def test_history_returns_real_conversation(client):
    dashboard_db.ensure_conversation("sess-restore", "user-restore")
    dashboard_db.record_turn(
        "sess-restore", "user-restore", "what's my recharge status",
        "Let me get you connected with someone who can help.",
        "en", "agent", [], card_shown=False,
    )

    resp = client.get("/history/sess-restore")
    data = resp.get_json()
    assert len(data["messages"]) == 2
    assert data["messages"][0] == {"role": "user", "text": "what's my recharge status"}
    assert data["messages"][1]["role"] == "bot"
    assert data["has_ticket"] is False


def test_history_reports_open_ticket(client):
    dashboard_db.ensure_conversation("sess-restore2", "user-restore2")
    dashboard_db.record_ticket(
        "AST-RESTORE", "sess-restore2", "user-restore2", "technical", "x", "d", None, "mid",
    )

    resp = client.get("/history/sess-restore2")
    assert resp.get_json()["has_ticket"] is True


def test_history_for_unknown_session_is_empty_not_an_error(client):
    resp = client.get("/history/does-not-exist")
    assert resp.status_code == 200
    assert resp.get_json() == {"messages": [], "has_ticket": False}


def test_history_includes_agent_messages(client):
    dashboard_db.ensure_conversation("sess-restore3", "user-restore3")
    dashboard_db.record_agent_message("sess-restore3", "I'm looking into this now.", "Priya")

    resp = client.get("/history/sess-restore3")
    messages = resp.get_json()["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "agent"
    assert "Priya" in messages[0]["text"]
