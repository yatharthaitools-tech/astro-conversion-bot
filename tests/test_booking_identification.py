"""Visitors never know booking/astrologer ids: the bot finds the session
from their recent bookings (by astrologer name) and may only act on a
booking_id that is actually one of theirs."""
from agent import executor
from agent.context import SessionContext
from integrations import coin_credit_client, recommend_flow_client, redash_client

USER = "4851070"


def _ctx():
    return SessionContext(user_id=USER, session_id="s", language="en", oauth_token="t")


def test_recent_bookings_are_described_in_visitor_terms():
    bookings = executor.execute("get_recent_bookings", {}, _ctx())["bookings"]
    assert len(bookings) == 5
    for b in bookings:
        assert {"booking_id", "astrologer_name", "mode", "started_at", "duration_minutes", "coins_deducted"} <= b.keys()
    # Newest first, and the same "latest" the details lookup falls back to.
    assert bookings[0]["booking_id"] == redash_client.get_booking_details(USER)["booking_id"]


def test_recent_bookings_filter_by_name_as_the_visitor_said_it():
    everyone = executor.execute("get_recent_bookings", {}, _ctx())["bookings"]
    name = everyone[0]["astrologer_name"]
    result = executor.execute("get_recent_bookings", {"astrologer_name": f"astro {name.lower()}"}, _ctx())
    assert result["bookings"] and all(b["astrologer_name"] == name for b in result["bookings"])


def test_unknown_name_falls_back_to_all_recent_bookings():
    result = executor.execute("get_recent_bookings", {"astrologer_name": "Zzyzx"}, _ctx())
    assert len(result["bookings"]) == 5 and "note" in result


def test_invented_booking_id_is_refused_everywhere():
    for tool, args in [
        ("get_booking_details", {"booking_id": "bk_made_up"}),
        ("check_refund_eligibility", {"booking_id": "bk_made_up"}),
        ("credit_coins", {"booking_id": "bk_made_up", "issue_tag": "astro_did_not_reply", "reason": "x"}),
    ]:
        assert executor.execute(tool, args, _ctx())["error"] == "unknown_booking", tool


def test_someone_elses_booking_is_refused():
    other = redash_client.get_recent_bookings("99999")[0]["booking_id"]
    ctx = _ctx()
    if other not in {b["booking_id"] for b in redash_client.get_recent_bookings(USER, 20)}:
        assert executor.execute("check_refund_eligibility", {"booking_id": other}, ctx)["error"] == "unknown_booking"


def test_confirmed_booking_goes_through(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "")
    booking_id = executor.execute("get_recent_bookings", {}, _ctx())["bookings"][0]["booking_id"]
    assert "error" not in executor.execute("get_booking_details", {"booking_id": booking_id}, _ctx())
    assert "error" not in executor.execute("check_refund_eligibility", {"booking_id": booking_id}, _ctx())


def test_astrologer_search_returns_public_profile():
    match = recommend_flow_client.search("Astro Samrat")[0]
    assert match["name"] == "Samrat"
    assert {"specialty", "languages", "experience", "rating", "price", "availability"} <= match.keys()
