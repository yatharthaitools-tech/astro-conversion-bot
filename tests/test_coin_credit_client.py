"""coin_credit_client.credit() is the one function in this codebase that
moves real money once COIN_CREDIT_API_AUTH is set — these tests mock
requests.post with the exact payload shape confirmed against a working
curl example (dev-api.astrolokal.com, Basic Auth), and run the
at-most-once gate (dashboard.db.reserve_coin_credit_attempt) against a
real Postgres instance rather than mocking it, since insert-race
behavior is exactly the kind of thing a mock can't meaningfully fake.
"""
import pytest
from unittest.mock import patch

import requests

from dashboard import db as dashboard_db
from integrations import coin_credit_client


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE coin_credit_attempts")
    yield


def test_mock_mode_without_api_auth(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "")
    result = coin_credit_client.credit("u1", "bk1", 50, "refund")
    assert result["success"] is True
    assert result["credit_id"]
    assert result["amount"] == 50


def test_duplicate_booking_credit_is_suppressed_even_in_mock_mode(monkeypatch):
    """The gate runs regardless of mock/live, so dedupe behavior — and the
    coin_credit_attempts ledger itself — is identical either way."""
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "")
    first = coin_credit_client.credit("u_dup", "bk_dup", 50, "refund")
    second = coin_credit_client.credit("u_dup", "bk_dup", 50, "refund")
    assert first["success"] is True
    assert second["success"] is False
    assert second["credit_id"] is None


def test_real_request_matches_confirmed_payload_shape(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")

    class Resp:
        status_code = 200
        text = '{"ok": true}'

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
        text = '{"ok": true}'

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
        text = '{"ok": true}'

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


def test_failed_attempt_is_recorded_for_reconciliation(monkeypatch):
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "test-b64-creds")
    with patch("integrations.coin_credit_client.requests.post", side_effect=requests.RequestException("down")):
        coin_credit_client.credit("4851070", "bk_recon", 50, "refund")

    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, error FROM coin_credit_attempts WHERE idempotency_key = %s",
                ("4851070:bk_recon",),
            )
            row = cur.fetchone()

    assert row["status"] == "error"
    assert "down" in row["error"]
