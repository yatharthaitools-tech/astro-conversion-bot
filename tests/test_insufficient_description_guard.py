"""create_support_ticket's own tool description instructs the model to
ask the visitor what happened FIRST, before ever calling this tool (see
agent/tool_schemas.py's CREATE_SUPPORT_TICKET) — but live testing showed
the model sometimes calls it anyway on a bare topic with no real detail,
self-admitted in its own description text ("wants to report an astrologer
but hasn't provided details yet"). agent/tool_registry.py's
_handle_create_support_ticket now rejects a call whose description admits
this, as a tool error the model sees and can react to within the same
turn (see agent/orchestrator.py's loop) — same prompt-reliability-gap
precedent as app.py's is_prediction_intent/is_severe_complaint.
"""
import pytest

from agent import executor as agent_executor
from agent.context import SessionContext
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


def test_rejects_a_ticket_whose_description_admits_no_real_detail():
    ctx = SessionContext(user_id="u_bare_topic", session_id="sess-bare", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "The user wants to report an astrologer but hasn't provided details yet.",
        },
        ctx,
    )

    assert "error" in result
    assert dashboard_db.list_tickets_for_user("u_bare_topic") == []
    assert ctx.ticket_raised is False


def test_rejects_a_description_with_an_infix_word_between_negation_and_verb():
    # Confirmed live: a naive substring check for "hasn't provided" missed
    # this real phrasing because of the word "yet" in between.
    ctx = SessionContext(user_id="u_infix", session_id="sess-infix", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Visitor wants to report an astrologer, but hasn't yet provided the details or who they are.",
        },
        ctx,
    )

    assert "error" in result
    assert dashboard_db.list_tickets_for_user("u_infix") == []


def test_rejects_a_description_using_a_different_info_giving_verb():
    # Confirmed live: the model varies the verb ("shared" instead of
    # "provided") for the exact same underlying case.
    ctx = SessionContext(user_id="u_verb_variant", session_id="sess-verb", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Visitor wants to report an astrologer, but hasn't yet shared the specific details or name.",
        },
        ctx,
    )

    assert "error" in result
    assert dashboard_db.list_tickets_for_user("u_verb_variant") == []


def test_allows_a_ticket_with_a_real_specific_description():
    ctx = SessionContext(user_id="u_real_detail", session_id="sess-real", language="en")
    dashboard_db.ensure_conversation(ctx.session_id, ctx.user_id)

    result = agent_executor.execute(
        "create_support_ticket",
        {
            "category": "report",
            "sub_category": "astrologer_misbehavior",
            "description": "Astrologer Samrat was rude and used bad language during the call.",
        },
        ctx,
    )

    assert result.get("ok") is True, result
    assert len(dashboard_db.list_tickets_for_user("u_real_detail")) == 1
    assert ctx.ticket_raised is True
