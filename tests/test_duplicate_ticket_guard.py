"""Confirmed live: a visitor pushing back repeatedly on an already-raised
issue kept getting a FRESH duplicate ticket every turn (agent/tool_registry.py's
_handle_create_support_ticket's own call, and separately app.py's
_raise_safety_net_ticket, which bypasses tool_registry.py entirely) — see
services/ticket_service.py's get_open_ticket and dashboard/db.py's
get_open_ticket_for_session. Scoped by category: a genuinely different
complaint in the same session still gets its own ticket, only a repeat of
the SAME category is blocked. The safety-net path (category=None) is
deliberately session-wide instead, since its own category is always a
hardcoded 'refund'/'escalation' regardless of the real issue.
"""
import pytest

from agent import executor as agent_executor
from agent.context import SessionContext
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


def test_rejects_a_second_ticket_in_the_same_category():
    ctx = SessionContext(user_id="u_cat_a", session_id="sess-cat-a", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    first = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Astrologer Samrat was rude and used bad language during the call.",
        },
        ctx,
    )
    assert first.get("ok") is True, first

    second = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Still upset about the same astrologer misbehavior incident.",
        },
        ctx,
    )
    assert "error" in second
    tickets = dashboard_db.list_tickets_for_user("u_cat_a")
    assert len(tickets) == 1


def test_allows_a_different_category_ticket_in_the_same_session():
    ctx = SessionContext(user_id="u_cat_b", session_id="sess-cat-b", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Astrologer Samrat was rude and used bad language during the call.",
        },
        ctx,
    )
    second = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "payment",
            "sub_category": "recharge_failed",
            "description": "Separately, money was deducted but coins never landed for a recharge.",
        },
        ctx,
    )
    assert second.get("ok") is True, second
    tickets = dashboard_db.list_tickets_for_user("u_cat_b")
    assert len(tickets) == 2
    assert {t["category"] for t in tickets} == {"report", "payment"}


def test_safety_net_scope_blocks_on_any_open_ticket_regardless_of_category():
    # Mirrors app.py's _raise_safety_net_ticket — category=None deliberately
    # ignores category, since its own category is always hardcoded and
    # doesn't reflect the visitor's real (possibly different-category) issue.
    ctx = SessionContext(user_id="u_cat_c", session_id="sess-cat-c", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Astrologer Samrat was rude and used bad language during the call.",
        },
        ctx,
    )
    assert ticket_service.get_open_ticket(ctx.session_id) is not None
    assert ticket_service.get_open_ticket(ctx.session_id, "refund") is None
