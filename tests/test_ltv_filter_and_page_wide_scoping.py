"""Analytics page gets a new LTV-tier filter (dashboard/routes.py's
/analytics route, dashboard/templates/analytics.html's filter bar), and
get_analytics()/get_event_analytics() are fixed so EVERY section respects
whatever filter is active, not just some — previously CSAT (resolved_by/
rating), ticket categories, and week/month trend charts were silently
unfiltered by the existing date-range filter while other sections (
conversation counts, source/language breakdown, card tap-through, LTV
breakdown) respected it, so a filtered page only looked partly filtered.
Runs against a real Postgres instance, same pattern as the other
dashboard tests here.
"""
import pytest

from dashboard import db as dashboard_db


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE events, ticket_status_history, tickets, messages, conversations CASCADE"
            )
    yield


def _resolved_conversation(session_id, user_id, ltv, resolved_tool, rating=None):
    dashboard_db.record_turn(
        session_id, user_id, "q", "a", "en", "bot",
        [{"tool": resolved_tool, "ok": True}], card_shown=False, ltv=ltv,
    )
    if rating is not None:
        dashboard_db.record_rating(session_id, rating)


# --- LTV-tier filter scopes the headline conversation count ---------------

def test_ltv_tier_filter_restricts_total_conversations():
    dashboard_db.record_turn("sess-high", "u1", "q", "a", "en", "bot", [], card_shown=False, ltv=5000)
    dashboard_db.record_turn("sess-mid", "u2", "q", "a", "en", "bot", [], card_shown=False, ltv=500)

    all_stats = dashboard_db.get_analytics()
    assert all_stats["total_conversations"] == 2

    high_only = dashboard_db.get_analytics(ltv_tier="High")
    assert high_only["total_conversations"] == 1

    mid_only = dashboard_db.get_analytics(ltv_tier="Mid")
    assert mid_only["total_conversations"] == 1


# --- Regression: CSAT (resolved_by/rating) used to ignore every filter ----

def test_resolved_by_counts_respect_ltv_tier_filter():
    _resolved_conversation("sess-high", "u1", ltv=5000, resolved_tool="mark_issue_resolved")
    _resolved_conversation("sess-mid", "u2", ltv=500, resolved_tool="create_support_ticket")

    all_stats = dashboard_db.get_analytics()
    assert all_stats["resolved_by_bot"] == 1
    assert all_stats["resolved_by_escalation"] == 1

    high_only = dashboard_db.get_analytics(ltv_tier="High")
    assert high_only["resolved_by_bot"] == 1
    assert high_only["resolved_by_escalation"] == 0

    # The same complement-based counting applies to the per-tier
    # ltv_breakdown table, not just the top-level KPI.
    high_row = next(r for r in all_stats["ltv_breakdown"] if r["tier"] == "High")
    assert high_row["resolved_by_bot"] == 1
    assert high_row["escalated"] == 0


def test_resolved_by_bot_counts_conversations_with_no_explicit_tool_call():
    # A plain FAQ-answered conversation — no mark_issue_resolved, no
    # create_support_ticket, just the bot replying. This must still count
    # as "resolved by bot": it never escalated, so under the new
    # complement-based definition it's in the bot bucket, not a silent
    # third "neither" bucket that used to make resolved_by_bot +
    # escalated undercount the real conversation total.
    dashboard_db.record_turn("sess-faq", "u1", "when is my recharge back", "it's instant", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-escalated", "u2", "q", "a", "en", "bot",
                              [{"tool": "create_support_ticket", "ok": True}], card_shown=False)

    stats = dashboard_db.get_analytics()
    assert stats["total_conversations"] == 2
    assert stats["resolved_by_bot"] == 1
    assert stats["resolved_by_escalation"] == 1
    assert stats["resolved_by_bot"] + stats["resolved_by_escalation"] == stats["total_conversations"]


def test_rating_stats_respect_ltv_tier_filter():
    _resolved_conversation("sess-high", "u1", ltv=5000, resolved_tool="mark_issue_resolved", rating=5)
    _resolved_conversation("sess-mid", "u2", ltv=500, resolved_tool="mark_issue_resolved", rating=1)

    high_only = dashboard_db.get_analytics(ltv_tier="High")
    assert high_only["rated_count"] == 1
    assert high_only["avg_rating"] == 5.0
    # pct_rated's denominator must be the SAME filtered scope (1 high-tier
    # conversation, 1 rated) — not an unfiltered all-time conversation
    # count, which would silently understate this once a filter is active.
    assert high_only["pct_rated"] == 100.0


def test_date_range_filter_also_scopes_resolved_by_and_rating():
    _resolved_conversation("sess-1", "u1", ltv=None, resolved_tool="mark_issue_resolved", rating=5)
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET first_seen_at = now() - interval '30 days' WHERE session_id = 'sess-1'"
            )
            cur.execute(
                "UPDATE messages SET created_at = now() - interval '30 days' WHERE session_id = 'sess-1'"
            )

    narrow_window = dashboard_db.get_analytics(date_from="2020-01-01", date_to="2020-01-02")
    assert narrow_window["resolved_by_bot"] == 0
    assert narrow_window["rated_count"] == 0
    assert narrow_window["pct_rated"] == 0.0


# --- Regression: ticket categories/open-tickets used to ignore every filter

def test_ticket_categories_respect_ltv_tier_filter():
    dashboard_db.ensure_conversation("sess-high", "u1", ltv=5000)
    dashboard_db.ensure_conversation("sess-mid", "u2", ltv=500)
    dashboard_db.record_ticket("AST-1", "sess-high", "u1", "payment", "x", "d", None, "high")
    dashboard_db.record_ticket("AST-2", "sess-mid", "u2", "technical", "x", "d", None, "mid")

    all_stats = dashboard_db.get_analytics()
    assert {c["category"] for c in all_stats["ticket_categories"]} == {"payment", "technical"}

    high_only = dashboard_db.get_analytics(ltv_tier="High")
    assert {c["category"] for c in high_only["ticket_categories"]} == {"payment"}
    assert high_only["open_tickets"] == 1
    assert high_only["total_tickets"] == 1


def test_open_tickets_and_total_tickets_respect_date_range_filter():
    dashboard_db.ensure_conversation("sess-1", "u1")
    dashboard_db.record_ticket("AST-1", "sess-1", "u1", "technical", "x", "d", None, "mid")
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tickets SET created_at = now() - interval '30 days'")

    narrow_window = dashboard_db.get_analytics(date_from="2020-01-01", date_to="2020-01-02")
    assert narrow_window["open_tickets"] == 0
    assert narrow_window["total_tickets"] == 0


# --- Regression: week/month trend charts used to ignore every filter ------

def test_conversation_and_ticket_trends_respect_ltv_tier_filter():
    dashboard_db.ensure_conversation("sess-high", "u1", ltv=5000)
    dashboard_db.ensure_conversation("sess-mid", "u2", ltv=500)
    dashboard_db.record_ticket("AST-1", "sess-high", "u1", "technical", "x", "d", None, "high")

    all_stats = dashboard_db.get_analytics()
    total_convos_raised = sum(w["raised"] for w in all_stats["conversation_trend"]["weekly"])
    total_tickets_raised = sum(w["raised"] for w in all_stats["ticket_trend"]["weekly"])
    assert total_convos_raised == 2
    assert total_tickets_raised == 1

    high_only = dashboard_db.get_analytics(ltv_tier="High")
    assert sum(w["raised"] for w in high_only["conversation_trend"]["weekly"]) == 1
    assert sum(w["raised"] for w in high_only["ticket_trend"]["weekly"]) == 1


# --- get_event_analytics: ltv_tier scopes it, date range deliberately not -

def test_event_analytics_totals_respect_ltv_tier_filter():
    dashboard_db.ensure_conversation("sess-high", "u1", ltv=5000)
    dashboard_db.ensure_conversation("sess-mid", "u2", ltv=500)
    dashboard_db.record_event("sess-high", "u1", "tap_connect_card", {})
    dashboard_db.record_event("sess-mid", "u2", "tap_connect_card", {})

    all_events = dashboard_db.get_event_analytics()
    assert all_events["event_totals"][0]["count"] == 2

    high_only = dashboard_db.get_event_analytics(ltv_tier="High")
    assert high_only["event_totals"][0]["count"] == 1


def test_unknown_tier_includes_conversations_with_no_ltv_at_all():
    dashboard_db.record_turn("sess-no-ltv", "u1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-high", "u2", "q", "a", "en", "bot", [], card_shown=False, ltv=5000)

    unknown_only = dashboard_db.get_analytics(ltv_tier="Unknown")
    assert unknown_only["total_conversations"] == 1
