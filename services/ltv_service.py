"""Thin LTV-tier lookup, used to tag tickets and to size the bonuses in
services/refund_service.py's decide() — that module is what actually
computes/moves coins now (the "Refund & Bonus Logic" SOP); this file
used to also own a flat tier-multiplier credit path, retired once
decide() superseded it with the real per-step rules.
"""
from integrations import redash_client


def get_tier(user_id: str) -> str:
    return redash_client.get_ltv_tier(user_id)
