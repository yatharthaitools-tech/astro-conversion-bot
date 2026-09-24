"""integrations/recommend_flow_client.py's trigger() — specifically the
fallback when a visitor names an astrologer who isn't actually
available. Written after a real bug found via live testing: the model
consistently stated the offline person's ETA correctly in text (3/3
runs) but the connect card kept routing to that same offline person,
since it reliably didn't make the second tool call the prompt asked
for. Fixed by deciding the fallback in code instead of relying on the
model — these tests lock that in.
"""
from integrations import recommend_flow_client as rfc


def test_naming_an_available_astrologer_routes_to_them():
    action = rfc.trigger("en", astrologer_id="mahalakshmi")
    assert action["display_mode"] == "specific"
    assert action["astrologer"]["id"] == "mahalakshmi"
    assert action["astrologer"]["availability"] == "Available now"
    assert action["requested_but_unavailable"] is None


def test_naming_an_unavailable_astrologer_falls_back_to_someone_online():
    action = rfc.trigger("en", astrologer_id="nidhi")
    # The card must never route "Connect now" to someone who isn't
    # actually online -- that just fails on the native side.
    assert action["display_mode"] == "general"
    assert action["astrologer"]["availability"] == "Available now"
    assert action["astrologer"]["id"] != "nidhi"
    # But the originally-named person's own status is still surfaced,
    # so the caller can state it truthfully alongside the fallback card.
    requested = action["requested_but_unavailable"]
    assert requested["name"] == "Nidhi"
    assert requested["availability"] == "Offline right now"
    assert requested["next_available_at"] is not None


def test_no_astrologer_named_has_no_requested_but_unavailable():
    action = rfc.trigger("en")
    assert action["display_mode"] == "general"
    assert action["requested_but_unavailable"] is None
