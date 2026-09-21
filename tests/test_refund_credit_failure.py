"""Proves refund_service.py never reports a false "coins credited" success
or poisons its own dedupe check when the real coin_credit_client call
fails — the bug this was written to catch: before this fix, a failed
credit still got recorded via redash_client.record_credit(), which
would have permanently blocked any retry for that booking even though
no coins actually moved.
"""
from unittest.mock import patch

import requests

from integrations import coin_credit_client, redash_client
from services import refund_service


def test_step1_credit_failure_reports_zero_and_does_not_poison_dedupe(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_WEBHOOK_URL", "https://n8n.getlokalapp.com/webhook/astro-bot-credit-coins")
    user_id, booking_id = "u_fail_step1", "bk_fail_step1"

    with patch("integrations.coin_credit_client.requests.post", side_effect=requests.RequestException("down")):
        result = refund_service.decide(user_id, booking_id, "astro_did_not_reply")

    assert result["step"] == 1
    assert result["reason_code"] == "step1_credit_failed"
    assert result["total_coins"] == 0
    assert result["credit_id"] is None
    assert result["route_to_ticket"] is True

    assert redash_client.get_refund_goodwill_history(user_id, booking_id) == []


def test_retention_credit_failure_reports_zero(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_WEBHOOK_URL", "https://n8n.getlokalapp.com/webhook/astro-bot-credit-coins")
    # Hashes to the "mid" LTV tier (a nonzero retention amount), so this
    # actually exercises the failed-credit branch rather than short-
    # circuiting on the "new_unpaid, nothing owed" path.
    with patch("integrations.coin_credit_client.requests.post", side_effect=requests.RequestException("down")):
        result = refund_service.decide_retention("u_fail_retention_mid_tier_x")

    assert result["total_coins"] == 0
    assert result["credit_id"] is None
    assert result["reason_code"] == "retention_credit_failed"
