"""get_astrologer_availability() is the one real (non-mocked) function in
redash_client.py — these tests mock requests.get with the exact response
shape confirmed against the live query (see recommend_flow_client.py's
expert_id comment) since the sandbox this was written in can't reach
analytics.getlokalapp.com itself to verify against the real endpoint.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from integrations import redash_client


def _redash_response(rows):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"query_result": {"data": {"rows": rows}}}

    return _Resp()


def test_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("REDASH_AVAILABILITY_API_KEY", raising=False)
    assert redash_client.get_astrologer_availability(100) is None


def test_online_now_has_no_next_available_at(monkeypatch):
    monkeypatch.setenv("REDASH_AVAILABILITY_API_KEY", "test-key")
    rows = [{"expert_id": 100, "is_online_now": True, "predicted_next_available_utc": None}]
    with patch("integrations.redash_client.requests.get", return_value=_redash_response(rows)):
        result = redash_client.get_astrologer_availability(100)
    assert result == {"is_online_now": True, "next_available_at": None}


def test_offline_formats_predicted_time_in_ist(monkeypatch):
    monkeypatch.setenv("REDASH_AVAILABILITY_API_KEY", "test-key")
    # 14:30 UTC -> 20:00 IST (UTC+5:30), same calendar day in IST
    next_utc = datetime.now(timezone.utc).replace(hour=14, minute=30, second=0, microsecond=0)
    rows = [{
        "expert_id": 100,
        "is_online_now": False,
        "predicted_next_available_utc": next_utc.strftime("%Y-%m-%dT%H:%M:%S"),
    }]
    with patch("integrations.redash_client.requests.get", return_value=_redash_response(rows)):
        result = redash_client.get_astrologer_availability(100)
    assert result["is_online_now"] is False
    assert result["next_available_at"] == "8:00 PM today"


def test_unknown_expert_id_returns_none(monkeypatch):
    monkeypatch.setenv("REDASH_AVAILABILITY_API_KEY", "test-key")
    rows = [{"expert_id": 100, "is_online_now": True, "predicted_next_available_utc": None}]
    with patch("integrations.redash_client.requests.get", return_value=_redash_response(rows)):
        result = redash_client.get_astrologer_availability(999)
    assert result is None


def test_request_failure_returns_none(monkeypatch):
    monkeypatch.setenv("REDASH_AVAILABILITY_API_KEY", "test-key")
    with patch("integrations.redash_client.requests.get", side_effect=redash_client.requests.RequestException("timeout")):
        result = redash_client.get_astrologer_availability(100)
    assert result is None
