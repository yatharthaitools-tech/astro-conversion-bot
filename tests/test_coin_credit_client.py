"""coin_credit_client.credit() is the one function in this codebase that
moves real money once COIN_CREDIT_API_AUTH is set — these tests mock
requests.post with the exact payload shape confirmed against a working
curl example (dev-api.astrolokal.com, Basic Auth), and also cover the
failure path that refund_service.py's _credit() depends on to avoid
ever reporting a false success or poisoning its dedupe check.
"""
from unittest.mock import patch

import requests

from integrations import coin_credit_client


def test_mock_mode_without_api_auth(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "")
    result = coin_credit_client.credit("u1", "bk1", 50, "refund")
    assert result["success"] is True
    assert result["credit_id"]
    assert result["amount"] == 50


def test_real_request_matches_confirmed_payload_shape(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return Resp()

    with patch("integrations.coin_credit_client.requests.post", side_effect=fake_post):
        result = coin_credit_client.credit("4851070", "bk1", 80, "refund")

    assert result["success"] is True
    assert captured["url"] == "https://dev-api.astrolokal.com/v1/system-transactions/"
    assert captured["json"] == [{
        "userId": 4851070, "amount": "80.00", "purpose": "promo",
        "description": "bk1", "source": "astro_conversion_bot",
    }]
    assert captured["headers"]["Authorization"] == "Basic test-b64-creds"


def test_non_numeric_user_id_stays_a_string(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return Resp()

    with patch("integrations.coin_credit_client.requests.post", side_effect=fake_post):
        coin_credit_client.credit("u_synthetic_hash_id", "bk1", 50, "refund")

    assert captured["json"][0]["userId"] == "u_synthetic_hash_id"


def test_retention_credit_has_no_booking_id_falls_back_to_reason(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["json"] = json
        return Resp()

    with patch("integrations.coin_credit_client.requests.post", side_effect=fake_post):
        coin_credit_client.credit("4851070", None, 20, "retention")

    assert captured["json"][0]["description"] == "retention"


def test_request_failure_reports_no_success(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")
    with patch("integrations.coin_credit_client.requests.post", side_effect=requests.RequestException("down")):
        result = coin_credit_client.credit("4851070", "bk1", 50, "refund")
    assert result["success"] is False
    assert result["credit_id"] is None
    assert result["amount"] == 0
