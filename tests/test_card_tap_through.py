"""dashboard/db.py's get_analytics() card tap-through rate — of the
conversations where the recommend/connect card was shown
(messages.card_shown), what share also have a 'tap_connect_card' event
for that session. Deliberately NOT called a conversion rate: tapping
Chat/Call fires a one-way message to the native host with no callback,
so this is the honest ceiling on what's measurable from this backend —
see the long comment in get_analytics() itself. Runs against a real
Postgres instance, same pattern as the other dashboard tests here.
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


def test_tap_through_rate_with_no_cards_shown_is_zero_not_a_crash():
    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 0
    assert stats["card_tapped_sessions"] == 0
    assert stats["pct_card_tap_through"] == 0.0


def test_card_shown_but_not_tapped_counts_as_shown_only():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=True)

    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 1
    assert stats["card_tapped_sessions"] == 0
    assert stats["pct_card_tap_through"] == 0.0


def test_card_shown_and_tapped_counts_toward_tap_through():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=True)
    dashboard_db.record_event("sess-1", "user-1", "tap_connect_card", {"service_type": "chat"})

    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 1
    assert stats["card_tapped_sessions"] == 1
    assert stats["pct_card_tap_through"] == 100.0


def test_tap_through_rate_across_multiple_sessions():
    # 2 of 4 sessions had the card shown; of those, 1 tapped it.
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=True)
    dashboard_db.record_turn("sess-2", "user-2", "q", "a", "en", "bot", [], card_shown=True)
    dashboard_db.record_turn("sess-3", "user-3", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_turn("sess-4", "user-4", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_event("sess-1", "user-1", "tap_connect_card", {"service_type": "chat"})

    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 2
    assert stats["card_tapped_sessions"] == 1
    assert stats["pct_card_tap_through"] == 50.0


def test_a_tap_event_on_a_session_where_card_was_never_shown_does_not_count():
    # Defends the JOIN direction: an event with no matching shown session
    # must not inflate either side of the ratio.
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=False)
    dashboard_db.record_event("sess-1", "user-1", "tap_connect_card", {"service_type": "chat"})

    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 0
    assert stats["card_tapped_sessions"] == 0
    assert stats["pct_card_tap_through"] == 0.0


def test_multiple_taps_in_the_same_session_count_once():
    dashboard_db.record_turn("sess-1", "user-1", "q", "a", "en", "bot", [], card_shown=True)
    dashboard_db.record_event("sess-1", "user-1", "tap_connect_card", {"service_type": "chat"})
    dashboard_db.record_event("sess-1", "user-1", "tap_connect_card", {"service_type": "audio"})

    stats = dashboard_db.get_analytics()
    assert stats["card_shown_sessions"] == 1
    assert stats["card_tapped_sessions"] == 1
    assert stats["pct_card_tap_through"] == 100.0
