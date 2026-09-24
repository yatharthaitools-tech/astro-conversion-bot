"""services/refund_service.py's decide_v1_refund() — LTV-tier + daily-cap
only, no booking validation, no Redash query. Runs against real Postgres
(the daily-cap check reads coin_credit_attempts directly) rather than
mocking the DB, since the cap logic IS the thing under test.
"""
import pytest

from dashboard import db as dashboard_db
from services import refund_service


@pytest.fixture(autouse=True)
def _clean_db():
    dashboard_db.init_db()
    with dashboard_db._connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE coin_credit_attempts")
    yield


def test_ltv_none_is_not_eligible():
    result = refund_service.decide_v1_refund("u1", None, "astrologer never replied")
    assert result["total_coins"] == 0
    assert result["reason_code"] == "ltv_unknown"


@pytest.mark.parametrize("ltv,expected_amount,expected_cap", [
    (0, 50, 1),
    (150, 50, 1),
    (200, 100, 2),
    (999, 100, 2),
    (1000, 150, 3),
    (50000, 150, 3),
])
def test_tier_boundaries(ltv, expected_amount, expected_cap):
    result = refund_service.decide_v1_refund(f"u_tier_{ltv}", ltv, "bad session")
    assert result["total_coins"] == expected_amount
    assert result["daily_cap"] == expected_cap
    assert result["reason_code"] == "refund_v1_credited"


def test_daily_cap_enforced_then_blocks():
    user_id = "u_cap_test"
    ltv = 50  # tier: cap=1, amount=50

    first = refund_service.decide_v1_refund(user_id, ltv, "first complaint")
    assert first["total_coins"] == 50
    assert first["reason_code"] == "refund_v1_credited"

    second = refund_service.decide_v1_refund(user_id, ltv, "second complaint same day")
    assert second["total_coins"] == 0
    assert second["reason_code"] == "daily_cap_reached"
    assert second["used_today"] == 1


def test_higher_tier_allows_more_per_day():
    user_id = "u_high_tier"
    ltv = 1500  # tier: cap=3, amount=150

    results = [refund_service.decide_v1_refund(user_id, ltv, f"complaint {i}") for i in range(3)]
    assert all(r["reason_code"] == "refund_v1_credited" for r in results)

    fourth = refund_service.decide_v1_refund(user_id, ltv, "complaint 4")
    assert fourth["reason_code"] == "daily_cap_reached"
    assert fourth["used_today"] == 3
