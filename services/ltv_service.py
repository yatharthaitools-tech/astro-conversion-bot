"""LTV-gated coin credit logic. This is where a credit amount actually
gets computed — agent/executor.py never passes an amount through, and
this module never accepts one as input; it always derives it fresh from
real booking/tier data. That's the enforcement point for "never let the
model set a payment amount freely."

Tier formula (bonus %) is this module's own default, not CS-governed
exactly — matches the architecture doc's note that the doc defines when/
whether a credit applies, not the precise formula. Adjust the multipliers
below if CS wants different numbers; nothing else needs to change.
"""
from integrations import coin_credit_client, redash_client

_TIER_MULTIPLIER = {
    "new_low": 1.0,   # 100% of disputed amount
    "mid": 1.2,       # 100% + moderate bonus
    "high": 1.5,      # 100% + higher bonus
}


def get_tier(user_id: str) -> str:
    return redash_client.get_ltv_tier(user_id)


def request_credit(user_id: str, booking_id: str, reason: str, category: str) -> dict:
    """The one path that can actually move coins. Checks tier eligibility,
    checks for a prior credit on this exact booking (no double-crediting),
    computes the amount itself, then calls the real credit API. Never
    accepts an amount argument — there is nothing here for a caller
    (model-driven or otherwise) to influence.
    """
    tier = get_tier(user_id)
    if tier == "new_unpaid":
        return {"approved": False, "reason_code": "new_unpaid_ineligible", "tier": tier}

    existing = redash_client.get_refund_goodwill_history(user_id, booking_id)
    if existing:
        return {"approved": False, "reason_code": "already_credited", "tier": tier, "existing": existing}

    booking = redash_client.get_booking_details(user_id, booking_id)
    deducted = booking.get("coins_deducted", 0)
    if deducted <= 0:
        return {"approved": False, "reason_code": "nothing_to_credit", "tier": tier}

    amount = round(deducted * _TIER_MULTIPLIER.get(tier, 1.0))
    result = coin_credit_client.credit(user_id, booking_id, amount, reason)
    redash_client.record_credit(user_id, booking_id, reason, amount)

    return {
        "approved": True,
        "amount": amount,
        "credit_id": result["credit_id"],
        "tier": tier,
        "category": category,
    }
