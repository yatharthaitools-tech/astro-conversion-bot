"""A new `language` link param (app.py's / route, carried by static/
script.js as user_language) lets the link that opens the chat declare the
visitor's own language preference, independent of whatever script they
happen to type THIS message in. agent/context.py's resolve_session parses
it into SessionContext.user_language — normalizing the link's full name
('Hindi') to the short code ('hi') dashboard.db.LANGUAGE_CODES actually
uses — and create_support_ticket's handler routes a new ticket by THAT
alone, never the per-message auto-detected language (see
agent/tool_registry.py's own comment on this).
"""
import pytest

from agent import executor as agent_executor
from agent.context import SessionContext, resolve_session
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


# --- agent/context.py: parsing the new link param -------------------------

def test_user_language_parsed_alongside_real_identity():
    ctx = resolve_session(
        {"user_id": "8236", "name": "Durvish", "user_language": "hi"},
        session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language == "hi"


def test_user_language_normalized_to_lowercase():
    ctx = resolve_session(
        {"user_id": "8236", "user_language": "HI"},
        session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language == "hi"


def test_user_language_ignored_without_real_identity():
    # Same trust boundary as user_name/ltv — only read alongside a real
    # user_id, never from an unverified/pseudo-id session.
    ctx = resolve_session(
        {"user_language": "hi"}, session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language is None


def test_user_language_absent_when_not_sent():
    ctx = resolve_session(
        {"user_id": "8236"}, session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language is None


def test_user_language_normalizes_the_links_full_name_to_a_code():
    # The real production link sends the full name, not a code:
    # .../?user_id=376&name=...&ltv=0&language=Hindi
    ctx = resolve_session(
        {"user_id": "8236", "user_language": "Hindi"},
        session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language == "hi"


@pytest.mark.parametrize("name, code", [("Tamil", "ta"), ("Telugu", "te"), ("Malayalam", "ml")])
def test_user_language_normalizes_every_routing_languages_full_name(name, code):
    ctx = resolve_session(
        {"user_id": "8236", "user_language": name},
        session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language == code


def test_user_language_passes_through_an_unrecognized_name_unchanged():
    # 'English' isn't one of the routing languages (dashboard.db's
    # LANGUAGE_CODES only covers Hindi/Tamil/Telugu/Malayalam) — no crash,
    # just never matches any support agent's own languages list, same as
    # the no-preference case.
    ctx = resolve_session(
        {"user_id": "8236", "user_language": "English"},
        session_id="sess-1", language="en", history=[],
    )
    assert ctx.user_language == "english"


# --- agent/tool_registry.py: ticket routing prefers the declared language -

def test_ticket_routes_by_declared_language_not_message_language():
    hindi_agent = dashboard_db.create_admin_user(
        "hindi@x.com", "hash", "support_agent", languages=["hi"]
    )
    dashboard_db.create_admin_user("general@x.com", "hash", "support_agent")

    # The visitor typed this message in English, but their link-declared
    # preference is Hindi — routing must follow the declared preference,
    # not the auto-detected message language ('en' here).
    ctx = SessionContext(
        user_id="u_hindi_pref", session_id="sess-routing", language="en", user_language="hi",
    )
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {"category": "technical", "sub_category": "login_issue", "description": "can't log in"},
        ctx,
    )
    assert result.get("ok") is True, result

    ticket = dashboard_db.list_tickets_for_user("u_hindi_pref")[0]
    assert ticket["language"] == "hi"
    assert ticket["assigned_admin_id"] == hindi_agent["id"]


def test_ticket_has_no_language_preference_without_a_declared_one():
    # The visitor typed this message in Tamil, but no `language` link
    # param was ever declared — routing must NOT fall back to guessing
    # from the message itself; it has no preference to route by at all.
    ctx = SessionContext(user_id="u_no_pref", session_id="sess-routing2", language="ta")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {"category": "technical", "sub_category": "x", "description": "d"},
        ctx,
    )
    assert result.get("ok") is True, result

    ticket = dashboard_db.list_tickets_for_user("u_no_pref")[0]
    assert ticket["language"] is None
