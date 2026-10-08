"""zoho_client.create_ticket's real (non-mock) payload — verified against
the actual "Astro Lokal" Desk portal layout (GET /api/v1/layouts, see
zoho_client.py's module docstring): category must be one of the real 15
picklist values, cf_sub_issue and cf_user_type are both mandatory
picklists (cf_sub_issue has no field this bot's free-text sub_category
can reliably match, so a fixed placeholder satisfies it; cf_user_type
must always be "Customer", not Zoho's own "Astrologer" default), and
cf_user_id/cf_ltv_tier don't exist as real custom fields at all —
user_id/ltv_tier instead travel via contact name and the subject line.
Mocks requests.post the same way test_coin_credit_client.py mocks its
own outbound call — no real network, no real Zoho org needed.
"""
from unittest.mock import patch

from integrations import zoho_client


def _capture_post(**ticket_kwargs):
    captured = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "zoho-ticket-99"}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return Resp()

    with patch("integrations.zoho_client.requests.post", side_effect=fake_post):
        result = zoho_client.create_ticket(**ticket_kwargs)
    return result, captured


def test_real_ticket_sends_only_verified_custom_fields(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(zoho_client, "ZOHO_ORG_ID", "12345")
    monkeypatch.setattr(zoho_client, "ZOHO_DEPARTMENT_ID", "dept-1")

    result, captured = _capture_post(
        user_id="u_4851070", category="payment", sub_category="recharge_failed",
        description="coins missing after recharge", ltv_tier="high",
    )

    assert result["zoho_ticket_id"] == "zoho-ticket-99"
    # cf_user_id/cf_ltv_tier are NOT real custom fields on the live
    # layout — sending them risks a silent drop or a validation error,
    # for no gain, since this info already reaches CS another way (below).
    # cf_user_type IS real and mandatory — must always be "Customer",
    # never Zoho's own "Astrologer" default.
    assert captured["json"]["customFields"] == {
        "cf_sub_issue": "General Inquiry", "cf_user_type": "Customer",
    }
    assert "u_4851070" in captured["json"]["contact"]["lastName"]
    assert "high" in captured["json"]["subject"]


def test_ltv_tier_defaults_to_unranked_in_subject(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    _, captured = _capture_post(
        user_id="u_no_tier", category="technical", sub_category="app_crash", description="desc",
    )

    assert "unranked" in captured["json"]["subject"]
    assert captured["json"]["customFields"] == {
        "cf_sub_issue": "General Inquiry", "cf_user_type": "Customer",
    }


def test_category_map_uses_only_real_portal_values():
    # Every mapped value must be one of the 15 real Category picklist
    # values confirmed live against the "Astro Lokal" layout (see
    # zoho_client.py's module docstring) — a value outside this set would
    # make Zoho reject the ticket outright.
    real_category_values = {
        "App Features", "App Guidance", "Astrologer Queries", "Low Visibility",
        "Onboarding", "Payment Queries", "Refund Request", "Profile changes",
        "Tech Issues", "User Queries", "Withdrawal / KYC", "Incomplete Query",
        "Promotional Query", "Transactional Query", "Internal Testing",
    }
    for mapped_value in zoho_client._ZOHO_CATEGORY_MAP.values():
        assert mapped_value in real_category_values
    assert zoho_client._DEFAULT_ZOHO_CATEGORY in real_category_values


def test_unknown_bot_category_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

    _, captured = _capture_post(
        user_id="u1", category="not_a_real_bot_category", sub_category="x", description="d",
    )

    assert captured["json"]["category"] == zoho_client._DEFAULT_ZOHO_CATEGORY
