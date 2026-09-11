"""The dominant refund pattern: session ended in seconds, astrologer never
actually responded, coins still deducted. This is auto-approved on FACT
(duration + message count), regardless of LTV tier — unlike
ltv_service.request_credit, a New/Unpaid visitor still gets this refund,
because nothing was actually delivered. Keep this separate from
ltv_service: one is "should we extend goodwill for a real session that
went badly" (discretionary, tier-gated), this is "did the astrologer even
show up" (factual, always refunded).
"""
from integrations import coin_credit_client, redash_client

AUTO_REFUND_MAX_DURATION_SECONDS = 30


def check_eligibility(user_id: str, booking_id: str) -> dict:
    booking = redash_client.get_booking_details(user_id, booking_id)
    qualifies = (
        booking["duration_seconds"] < AUTO_REFUND_MAX_DURATION_SECONDS
        and booking["astrologer_message_count"] == 0
        and booking["coins_deducted"] > 0
    )
    if not qualifies:
        return {"qualifies": False, "booking": booking}

    existing = redash_client.get_refund_goodwill_history(user_id, booking_id)
    if existing:
        return {"qualifies": True, "already_refunded": True, "booking": booking, "existing": existing}

    result = coin_credit_client.credit(user_id, booking_id, booking["coins_deducted"], "refund")
    redash_client.record_credit(user_id, booking_id, "refund", booking["coins_deducted"])
    return {
        "qualifies": True,
        "already_refunded": False,
        "amount": booking["coins_deducted"],
        "credit_id": result["credit_id"],
        "booking": booking,
    }
