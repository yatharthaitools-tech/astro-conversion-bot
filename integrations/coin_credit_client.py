"""MOCKED — replace with a real call to the existing Coin Credit API.

Deliberately does NOT accept or compute an amount from its caller in any
way that could be traced back to the model — services/ltv_service.py
computes the amount before this is ever called; this file just represents
"send the credit". Going live is a one-line change: replace the body with
a real httpx.post to the existing endpoint, gated by a MOCK_MODE flag.
"""
import logging
import uuid

logger = logging.getLogger(__name__)


def credit(user_id: str, booking_id: str, amount: int, reason: str) -> dict:
    credit_id = uuid.uuid4().hex[:10]
    logger.info(
        "[MOCK coin_credit_client] credited user=%s booking=%s amount=%s reason=%s credit_id=%s",
        user_id, booking_id, amount, reason, credit_id,
    )
    return {"success": True, "credit_id": credit_id, "amount": amount}
