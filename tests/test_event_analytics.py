"""dashboard/db.py's record_event() + get_event_analytics() — the store
behind the admin Analytics page's Events section and D0D/W0W/M0M numbers.
Runs against a real Postgres instance, same pattern as the other dashboard
tests here.
"""
import pytest

from dashboard import db as dashboard_db


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE events, ticket_status_history, tickets, messages, conversations CASCADE")
    yield


def test_record_event_persists_type_and_data():
    dashboard_db.record_event("sess-1", "user-1", "quick_reply_tap", {"question": "Career feels stuck"})

    events = dashboard_db.get_event_analytics()
    assert events["event_totals"] == [{"event_type": "quick_reply_tap", "count": 1}]
    assert events["by_type"]["quick_reply_tap"]["total"] == 1


def test_get_event_analytics_groups_by_type_and_counts_each():
    dashboard_db.record_event("sess-1", "user-1", "quick_reply_tap", {"question": "a"})
    dashboard_db.record_event("sess-1", "user-1", "quick_reply_tap", {"question": "b"})
    dashboard_db.record_event("sess-1", "user-1", "close_tap", {})
    dashboard_db.record_event("sess-2", "user-2", "rating_given", {"rating": 5})

    events = dashboard_db.get_event_analytics()
    totals = {row["event_type"]: row["count"] for row in events["event_totals"]}
    assert totals == {"quick_reply_tap": 2, "close_tap": 1, "rating_given": 1}
    assert events["overall"]["today"] == 4


def test_period_over_period_today_counts_and_null_pct_with_no_prior_period():
    dashboard_db.record_event("sess-1", "user-1", "send_message_tap", {})
    dashboard_db.record_event("sess-1", "user-1", "send_message_tap", {})

    stat = dashboard_db.get_event_analytics()["by_type"]["send_message_tap"]
    assert stat["total"] == 2
    assert stat["today"] == 2
    assert stat["yesterday"] == 0
    # No events yesterday to compare against — a % change would be either
    # a divide-by-zero or a misleading "infinite" jump, so this must be
    # None, not 0 or a huge number.
    assert stat["dod_pct"] is None
    assert stat["this_week"] == 2
    assert stat["this_month"] == 2


def test_record_event_is_best_effort_and_never_raises():
    # No real user/session validation on this table (it's analytics, not
    # a security boundary) — an empty/None session_id or user_id must
    # still record cleanly rather than raising up into the /event route.
    dashboard_db.record_event(None, None, "close_tap", {})
    events = dashboard_db.get_event_analytics()
    assert events["by_type"]["close_tap"]["total"] == 1


def test_conversations_period_reflects_started_today():
    dashboard_db.ensure_conversation("sess-1", "user-1")

    stats = dashboard_db.get_analytics()
    assert stats["conversations_period"]["today"] == 1
    assert stats["conversations_period"]["yesterday"] == 0
    assert stats["conversations_period"]["dod_pct"] is None
