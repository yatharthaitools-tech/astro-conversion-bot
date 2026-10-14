"""Viewing a conversation from a ticket's "View chat" link used to always
back-link to the Conversations list, even when the admin came from
Tickets — stranding them away from the ticket they were just working.
ticket_detail.html now passes ticket_id along, and conversation_detail's
back-link honors it when present, falling back to Conversations otherwise.
"""
import pytest
from werkzeug.security import generate_password_hash

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


def _login_admin(client):
    dashboard_db.create_admin_user("admin@x.com", generate_password_hash("pass12345"), "admin")
    client.post("/admin/login", data={"email": "admin@x.com", "password": "pass12345"})


def _create_ticket_for_session(session_id, user_id="u1"):
    dashboard_db.record_ticket(
        "T-1", session_id, user_id, "technical", "login_issue", "can't log in", None, "New/Unpaid",
    )
    return dashboard_db.list_tickets_for_user(user_id)[0]


def test_ticket_detail_links_to_chat_with_ticket_id(client):
    _login_admin(client)
    dashboard_db.record_turn("sess-1", "u1", "help", "sure", "en", "agent", [], card_shown=False)
    ticket = _create_ticket_for_session("sess-1")

    resp = client.get(f"/admin/tickets/{ticket['id']}")
    body = resp.get_data(as_text=True)

    assert f"/admin/conversations/sess-1?ticket_id={ticket['id']}" in body


def test_conversation_detail_back_link_returns_to_the_ticket(client):
    _login_admin(client)
    dashboard_db.record_turn("sess-1", "u1", "help", "sure", "en", "agent", [], card_shown=False)
    ticket = _create_ticket_for_session("sess-1")

    resp = client.get(f"/admin/conversations/sess-1?ticket_id={ticket['id']}")
    body = resp.get_data(as_text=True)

    assert f'href="/admin/tickets/{ticket["id"]}" class="back-link"' in body
    assert "Back to ticket" in body
    assert "Back to conversations" not in body


def test_conversation_detail_back_link_defaults_to_conversations_list(client):
    _login_admin(client)
    dashboard_db.record_turn("sess-2", "u1", "help", "sure", "en", "agent", [], card_shown=False)

    resp = client.get("/admin/conversations/sess-2")
    body = resp.get_data(as_text=True)

    assert 'href="/admin/conversations" class="back-link"' in body
    assert "Back to conversations" in body
