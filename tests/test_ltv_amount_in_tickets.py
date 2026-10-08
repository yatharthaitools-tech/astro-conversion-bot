"""The visitor's real ₹ lifetime-spend figure (SessionContext.ltv, sourced
from the link that opened the chat) now reaches both get_ltv_tier's own
tier bucketing and create_support_ticket's Zoho payload — previously both
went through a mocked Redash-derived tier that ignored the real figure
entirely (services/ltv_service.get_tier(user_id) alone, no real ltv in
sight). See services/ltv_service.py, agent/tool_registry.py, and
integrations/zoho_client.py's create_ticket for the actual fix.
"""
from unittest.mock import patch

from agent import tool_registry
from agent.context import SessionContext
from integrations import zoho_client
from services import ltv_service


# --- services/ltv_service.py: real-amount bucketing -------------------

def test_tier_from_amount_thresholds():
    assert ltv_service.tier_from_amount(0) == "new_unpaid"
    assert ltv_service.tier_from_amount(200) == "new_low"
    assert ltv_service.tier_from_amount(1000) == "mid"
    assert ltv_service.tier_from_amount(1000.01) == "high"
    assert ltv_service.tier_from_amount(None) is None


def test_get_tier_prefers_real_ltv_over_mocked_redash():
    # A real ltv figure must win even though redash_client.get_ltv_tier
    # would (deterministically, per its own hash-seeded mock) return
    # something different for this same user_id.
    assert ltv_service.get_tier("u_whatever_hash_this_makes", ltv=13924.00) == "high"


def test_get_tier_falls_back_to_mocked_redash_without_real_ltv():
    with patch("services.ltv_service.redash_client.get_ltv_tier", return_value="mid") as mocked:
        assert ltv_service.get_tier("u1", ltv=None) == "mid"
        mocked.assert_called_once_with("u1")


# --- agent/tool_registry.py: get_ltv_tier tool uses ctx.ltv ------------

def test_get_ltv_tier_tool_uses_real_ltv_from_session_context():
    ctx = SessionContext(user_id="u1", session_id="s1", language="en", ltv=13924.00)
    result = tool_registry._handle_get_ltv_tier({}, ctx)
    assert result == {"tier": "high"}


# --- integrations/zoho_client.py: exact amount reaches the real ticket -

def _capture_post(**ticket_kwargs):
    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "zoho-ticket-1"}

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return Resp()

    with patch("integrations.zoho_client.requests.post", side_effect=fake_post):
        zoho_client.create_ticket(**ticket_kwargs)
    return captured


def test_exact_ltv_amount_reaches_subject_and_description(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    captured = _capture_post(
        user_id="u1", category="payment", sub_category="recharge failed",
        description="coins missing", ltv_tier="high", ltv_amount=13924.00,
    )

    assert "13,924.00" in captured["json"]["subject"]
    assert "13,924.00" in captured["json"]["description"]


def test_ltv_amount_survives_alongside_evidence_url(monkeypatch):
    # Regression check: the evidence_url branch used to overwrite
    # payload["description"] from scratch with the raw description param,
    # silently dropping the LTV line prepended just above it.
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    captured = _capture_post(
        user_id="u1", category="technical", sub_category="app crash",
        description="desc", ltv_tier="mid", ltv_amount=500.0,
        evidence_url="https://example.com/screenshot.png",
    )

    assert "500.00" in captured["json"]["description"]
    assert "Evidence: https://example.com/screenshot.png" in captured["json"]["description"]


def test_missing_ltv_amount_shows_unranked(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    captured = _capture_post(
        user_id="u1", category="technical", sub_category="x", description="d",
    )

    assert "unranked" in captured["json"]["subject"]
    assert "unranked" in captured["json"]["description"]
