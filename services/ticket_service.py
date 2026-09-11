"""Wraps zoho_client so every ticket automatically carries the visitor's
real LTV tier — CS triages by tier without the model having to think
about it. All triage/priority/resolution after creation is CS's, not
this bot's, same posture as AstroHelp.
"""
from integrations import zoho_client
from services import ltv_service


def create_ticket(user_id: str, category: str, sub_category: str, description: str,
                   evidence_url: str = None) -> dict:
    tier = ltv_service.get_tier(user_id)
    return zoho_client.create_ticket(
        user_id, category, sub_category, description, evidence_url, ltv_tier=tier
    )


def get_tickets(user_id: str) -> list:
    return zoho_client.get_tickets(user_id)
