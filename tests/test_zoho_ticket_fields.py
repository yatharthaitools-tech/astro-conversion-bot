"""zoho_client.create_ticket's real (non-mock) payload — specifically that
user_id/ltv_tier travel as structured customFields, not just embedded in
free text (subject/contact name), so CS can actually filter/search on
them in Zoho. Mocks requests.post the same way test_coin_credit_client.py
mocks its own outbound call — no real network, no real Zoho org needed.
"""
from unittest.mock import patch

from integrations import zoho_client


def test_real_ticket_includes_user_id_and_ltv_as_custom_fields(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(zoho_client, "ZOHO_ORG_ID", "12345")
    monkeypatch.setattr(zoho_client, "ZOHO_DEPARTMENT_ID", "dept-1")

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
        result = zoho_client.create_ticket(
            "u_4851070", "payment", "recharge_failed", "coins missing after recharge",
            ltv_tier="high",
        )

    assert result["zoho_ticket_id"] == "zoho-ticket-99"
    assert captured["json"]["customFields"] == {"cf_user_id": "u_4851070", "cf_ltv_tier": "high"}


def test_ltv_tier_defaults_to_unranked_in_custom_fields(monkeypatch):
    monkeypatch.setattr(zoho_client, "ZOHO_MOCK_MODE", False)
    monkeypatch.setattr(zoho_client, "_get_access_token", lambda: "fake-token")

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
        zoho_client.create_ticket("u_no_tier", "technical", "app_crash", "desc")

    assert captured["json"]["customFields"] == {"cf_user_id": "u_no_tier", "cf_ltv_tier": "unranked"}
