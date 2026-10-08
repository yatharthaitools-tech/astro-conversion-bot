"""Thin LTV-tier lookup, used to tag tickets and to size the bonuses in
services/refund_service.py's decide() — that module is what actually
computes/moves coins now (the "Refund & Bonus Logic" SOP); this file
used to also own a flat tier-multiplier credit path, retired once
decide() superseded it with the real per-step rules.
"""
from integrations import redash_client

# Same thresholds dashboard/db.py's get_analytics() uses for its LTV-tier
# breakdown — kept in sync so a visitor bucketed "Mid" in a ticket is the
# same "Mid" bucket the Analytics page shows for their conversations.
def tier_from_amount(amount) -> str:
    if amount is None:
        return None
    if amount <= 0:
        return "new_unpaid"
    if amount <= 200:
        return "new_low"
    if amount <= 1000:
        return "mid"
    return "high"


def get_tier(user_id: str, ltv: float = None) -> str:
    """Prefers the visitor's real ₹ lifetime-spend figure (ltv — see
    agent/context.py's SessionContext, sourced from the link that opened
    the chat) when it's available, bucketing it directly rather than
    going through Redash at all. Only falls back to the mocked
    redash_client.get_ltv_tier (a fake tier derived from a hash of
    user_id, not real spend data) when no real ltv came through — e.g.
    local/dev testing outside either real link format."""
    real_tier = tier_from_amount(ltv)
    if real_tier is not None:
        return real_tier
    return redash_client.get_ltv_tier(user_id)
