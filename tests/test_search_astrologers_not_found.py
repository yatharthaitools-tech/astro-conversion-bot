"""When a visitor names someone not on the roster, the model unreliably
followed the prompt-only instruction to avoid saying "not on the roster"
and to actually trigger the connect card — live testing showed 3/3 runs
either leaked that phrase, offered to connect without calling the tool,
or fabricated a status for a nonexistent person. Fixed by deciding this
in code (agent/tool_registry.py's _handle_search_astrologers), same
pattern as trigger_recommend_astrologer's own in-code fallback for a
named-but-unavailable astrologer.
"""
from agent import tool_registry
from agent.context import SessionContext


def test_no_match_triggers_connect_card_in_code():
    ctx = SessionContext(user_id="u1", session_id="s1", language="en")
    result = tool_registry._handle_search_astrologers({"query": "meera"}, ctx)

    assert result["matches"] == []
    assert result["connected_with_someone_else"] is True
    assert result["requested_name"] == "meera"
    # The card must actually be triggered, not just described in the tool
    # result — this is what makes it appear in the response's own
    # 'action' field regardless of what the model says in text.
    assert ctx.ui_action is not None
    assert ctx.ui_action["type"] == "connect_popup"
    # No specific astrologer requested — this is a "nobody by that name"
    # case, not a "named someone who's offline" case, so the card must be
    # the general one, never claiming to be the person who doesn't exist.
    assert ctx.ui_action["display_mode"] == "general"
    assert ctx.ui_action["requested_but_unavailable"] is None


def test_real_match_does_not_trigger_a_card():
    ctx = SessionContext(user_id="u1", session_id="s1", language="en")
    result = tool_registry._handle_search_astrologers({"query": "nidhi"}, ctx)

    assert len(result["matches"]) == 1
    assert result["matches"][0]["name"] == "Nidhi"
    assert "connected_with_someone_else" not in result
    # Finding a real match is just a lookup — connecting is still a
    # separate, deliberate trigger_recommend_astrologer call.
    assert ctx.ui_action is None
