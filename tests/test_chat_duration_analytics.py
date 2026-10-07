"""dashboard/db.py's get_analytics() additions: avg chat duration, avg
turns per conversation, and returning-visitor rate — all computed from
columns conversations already had (first_seen_at/last_seen_at/turn_count/
user_id), no schema change. Runs against a real Postgres instance, same
pattern as the other dashboard tests here. Timestamps/turn_count are set
directly via SQL after record_turn() so duration math is deterministic
rather than depending on real wall-clock timing between test statements.
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


def _set_conversation_window(session_id, duration_seconds, turn_count):
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE conversations
                   SET first_seen_at = now() - %s::interval,
                       last_seen_at = now(), turn_count = %s
                   WHERE session_id = %s""",
                (f"{duration_seconds} seconds", turn_count, session_id),
            )


def test_format_duration_under_an_hour():
    assert dashboard_db._format_duration(0) == "0m 0s"
    assert dashboard_db._format_duration(45) == "0m 45s"
    assert dashboard_db._format_duration(252) == "4m 12s"


def test_format_duration_past_an_hour():
    assert dashboard_db._format_duration(3725) == "1h 2m"


def test_format_duration_handles_none():
    assert dashboard_db._format_duration(None) == "0m 0s"


def test_avg_duration_and_turns_across_conversations():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-2", "user-2", "q", "a", "en", "bot", [], card_shown=False)
    _set_conversation_window("sess-1", duration_seconds=60, turn_count=2)
    _set_conversation_window("sess-2", duration_seconds=120, turn_count=4)

    stats = dashboard_db.get_analytics()

    assert stats["avg_duration_seconds"] == pytest.approx(90, abs=2)
    assert stats["avg_duration_label"] == "1m 30s"
    assert stats["avg_turns_per_conversation"] == 3.0


def test_avg_duration_is_zero_for_a_single_message_conversation():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    _set_conversation_window("sess-1", duration_seconds=0, turn_count=1)

    stats = dashboard_db.get_analytics()
    assert stats["avg_duration_label"] == "0m 0s"
    assert stats["avg_turns_per_conversation"] == 1.0


def test_returning_visitor_rate_counts_users_with_more_than_one_session():
    # user-1 has two separate conversations (returning); user-2 has one
    # (new, not returning).
    dashboard_db.record_turn("sess-1a", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-1b", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-2", "user-2", "q", "a", "en", "bot", [], card_shown=False)

    stats = dashboard_db.get_analytics()

    assert stats["total_visitors"] == 2
    assert stats["returning_visitors"] == 1
    assert stats["pct_returning"] == 50.0


def test_returning_visitor_rate_is_zero_when_nobody_returns():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-2", "user-2", "q", "a", "en", "bot", [], card_shown=False)

    stats = dashboard_db.get_analytics()

    assert stats["total_visitors"] == 2
    assert stats["returning_visitors"] == 0
    assert stats["pct_returning"] == 0.0


def test_returning_visitor_rate_with_no_conversations_is_zero_not_a_crash():
    stats = dashboard_db.get_analytics()
    assert stats["total_visitors"] == 0
    assert stats["returning_visitors"] == 0
    assert stats["pct_returning"] == 0.0
