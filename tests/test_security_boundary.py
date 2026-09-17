"""Proves agent/executor.py's identity override actually works — not just
by inspection, but by driving a real tool call end to end and checking
what actually got persisted. Same pattern as AstroHelp/dostt-support-chat's
own test_account_id_in_tool_input_is_ignored_and_overridden.

Without this, the strip-and-overwrite in executor.py was only ever
eyeballed, never proven: a future refactor could silently reintroduce a
path that trusts tool_input's user_id, and nothing would catch it.
"""
import pytest

from agent import executor as agent_executor
from agent.context import SessionContext
from dashboard import db as dashboard_db


@pytest.fixture(autouse=True)
def _clean_db():
    """Runs against a real Postgres instance (DATABASE_URL, default: the
    local dev database) rather than a throwaway SQLite file, so each test
    starts from empty tables instead of a fresh temp file."""
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE ticket_status_history, tickets, messages, conversations CASCADE")
    yield


def test_create_support_ticket_ignores_spoofed_user_id():
    real_user_id = "u_real_verified_user"
    ctx = SessionContext(user_id=real_user_id, session_id="sess-security-test", language="en")
    # Real requests get this from app.py's /ask route before the agent
    # runs — tickets.session_id FK-references this row (see the "first
    # turn" bug fixed earlier: a ticket raised before this row exists
    # fails silently).
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    # A malicious or hallucinated tool call claiming to be someone else —
    # this is exactly the shape a compromised/confused model could produce,
    # since nothing stops it from putting arbitrary extra keys in its
    # function-call arguments even though the schema never defines this one.
    result = agent_executor.execute(
        "create_support_ticket",
        {
            "user_id": "attacker_spoofed_id",
            "category": "technical",
            "sub_category": "login_issue",
            "description": "test ticket for security boundary check",
        },
        ctx,
    )

    assert result.get("ok") is True, result

    real_tickets = dashboard_db.list_tickets_for_user(real_user_id)
    spoofed_tickets = dashboard_db.list_tickets_for_user("attacker_spoofed_id")

    # If the override didn't happen, this ticket would have landed under
    # "attacker_spoofed_id" instead — this succeeding at all is what proves
    # executor.py's overwrite actually ran before the handler did.
    assert len(real_tickets) == 1
    assert len(spoofed_tickets) == 0
    assert real_tickets[0]["ticket_ref"] == result["ticket_id"]
    assert real_tickets[0]["user_id"] == real_user_id


def test_resolve_session_requires_both_user_id_and_oauth_token():
    from agent.context import resolve_session

    # A bare user_id with no token is exactly what a spoofed request would
    # send — resolve_session must not trust it on its own.
    ctx = resolve_session({"user_id": "claimed_identity"}, "sess-1", "en", [])
    assert ctx.user_id != "claimed_identity"

    # Both present together is what the real app handoff looks like.
    ctx2 = resolve_session(
        {"user_id": "claimed_identity", "oauth_token": "some-token"}, "sess-1", "en", []
    )
    assert ctx2.user_id == "claimed_identity"
    assert ctx2.oauth_token == "some-token"
