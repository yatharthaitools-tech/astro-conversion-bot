"""Session-end free coins: real LTV tier from Redash sizes the credit,
at most once per user per day, and only for a real app user who actually
chatted. The at-most-once gate runs against real Postgres (same as
test_coin_credit_client.py); Redash and the coin API are mocked."""
from unittest.mock import patch

import pytest

import app as app_module
from dashboard import db as dashboard_db
from integrations import coin_credit_client, redash_client
from services import refund_service


@pytest.fixture(autouse=True)
def _clean_db(monkeypatch):
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE coin_credit_attempts, messages, ticket_status_history, tickets, conversations")
    monkeypatch.setattr(coin_credit_client, "COIN_CREDIT_API_AUTH", "")
    monkeypatch.delenv("COIN_CREDIT_API_AUTH", raising=False)
    monkeypatch.delenv("REDASH_LTV_QUERY_ID", raising=False)
    monkeypatch.delenv("REDASH_LTV_API_KEY", raising=False)
    yield


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


def _rows(rows):
    return {"query_result": {"data": {"rows": rows}}}


# --- Real LTV lookup ------------------------------------------------------

def _ltv_env(monkeypatch):
    monkeypatch.setenv("REDASH_LTV_QUERY_ID", "999")
    monkeypatch.setenv("REDASH_LTV_API_KEY", "k")


@pytest.mark.parametrize("spend,tier", [(0, "new_unpaid"), (150, "new_low"), (600, "mid"), (2500, "high")])
def test_real_ltv_tier_from_cached_result(monkeypatch, spend, tier):
    _ltv_env(monkeypatch)
    with patch.object(redash_client.requests, "post", return_value=_Resp(_rows([{"lifetime_spend": spend}]))) as post:
        assert redash_client.get_ltv_tier("4851070") == tier
    assert post.call_args.kwargs["json"]["parameters"] == {"user_id": "4851070"}


def test_real_ltv_no_row_means_never_paid(monkeypatch):
    _ltv_env(monkeypatch)
    with patch.object(redash_client.requests, "post", return_value=_Resp(_rows([]))):
        assert redash_client.get_ltv_tier("1") == "new_unpaid"


def test_real_ltv_polls_job_until_result(monkeypatch):
    _ltv_env(monkeypatch)
    monkeypatch.setattr(redash_client, "_LTV_JOB_POLL_S", 0)
    job_pending = {"job": {"id": "j1", "status": 2}}
    job_done = {"job": {"id": "j1", "status": 3, "query_result_id": 77}}
    gets = [_Resp(job_done), _Resp(_rows([{"lifetime_spend": 1500}]))]
    with patch.object(redash_client.requests, "post", return_value=_Resp(job_pending)), \
         patch.object(redash_client.requests, "get", side_effect=gets) as get:
        assert redash_client.get_ltv_tier("1") == "high"
    assert get.call_args.args[0].endswith("/api/query_results/77.json")


def test_real_ltv_failure_gives_no_bonus_tier(monkeypatch):
    _ltv_env(monkeypatch)
    with patch.object(redash_client.requests, "post", side_effect=redash_client.requests.ConnectionError("down")):
        assert redash_client.get_ltv_tier("1") == "new_unpaid"


def test_no_fake_tiers_once_real_credits_are_on(monkeypatch):
    monkeypatch.setenv("COIN_CREDIT_API_AUTH", "live-creds")
    # Would hash to a paying tier under the mock; with real credits on and
    # no LTV query configured it must not size real coins.
    assert redash_client.get_lifetime_spend("u_fail_retention_mid_tier_x") is None
    assert redash_client.get_ltv_tier("u_fail_retention_mid_tier_x") == "new_unpaid"


# --- Session-end credit rule ----------------------------------------------

@pytest.mark.parametrize("tier,coins", [("new_unpaid", 0), ("new_low", 20), ("mid", 35), ("high", 50)])
def test_session_end_amount_by_tier(tier, coins):
    with patch.object(redash_client, "get_ltv_tier", return_value=tier):
        assert refund_service.decide_session_end(f"u_{tier}")["total_coins"] == coins


def test_session_end_coins_once_per_day_and_separate_from_retention():
    with patch.object(redash_client, "get_ltv_tier", return_value="mid"):
        first = refund_service.decide_session_end("u_daily")
        second = refund_service.decide_session_end("u_daily")
        retention = refund_service.decide_retention("u_daily")
    assert first["total_coins"] == 35
    assert second["total_coins"] == 0 and second["reason_code"] == "session_end_not_credited"
    assert retention["total_coins"] == 35  # its own daily slot


# --- /session-end endpoint ------------------------------------------------

def _chat(session_id, user_id):
    dashboard_db.record_turn(session_id, user_id, "hi", "hello", "en", "agent", [], False)


def _post(body):
    return app_module.app.test_client().post("/session-end", json=body).get_json()


def test_endpoint_credits_real_user_who_chatted():
    _chat("s1", "4851070")
    with patch.object(redash_client, "get_ltv_tier", return_value="high"):
        data = _post({"session_id": "s1", "user_id": "4851070", "oauth_token": "t"})
    assert data["coins"] == 50
    assert data["action"]["type"] == "free_coins_bottomsheet"
    assert data["action"]["freeCoins"]["coins"] == 50.0


def test_endpoint_no_coins_without_app_identity():
    _chat("s2", "4851070")
    with patch.object(redash_client, "get_ltv_tier", return_value="high"):
        assert _post({"session_id": "s2", "user_id": "4851070"})["coins"] == 0


def test_endpoint_no_coins_without_a_chat_for_that_user():
    _chat("s3", "someone_else")
    with patch.object(redash_client, "get_ltv_tier", return_value="high"):
        assert _post({"session_id": "s3", "user_id": "4851070", "oauth_token": "t"})["coins"] == 0
        assert _post({"session_id": "nope", "user_id": "4851070", "oauth_token": "t"})["coins"] == 0
