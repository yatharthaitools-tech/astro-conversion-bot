"""MOCKED — replace with the existing Zoho Desk connection (same one
AstroHelp's app/integrations/zoho_client.py already talks to).

create_ticket pre-fills category/sub-issue/transcript/evidence/LTV tier —
CS owns all triage/priority/resolution after that, same as the AstroHelp
pattern; this bot's job stops at "raise a well-formed ticket."
"""
import logging
import uuid

logger = logging.getLogger(__name__)

_MOCK_TICKETS_BY_USER: dict = {}


def create_ticket(user_id: str, category: str, sub_category: str, description: str,
                   evidence_url: str = None, ltv_tier: str = None) -> dict:
    ticket_id = f"AST-{uuid.uuid4().hex[:6].upper()}"
    ticket = {
        "ticket_id": ticket_id,
        "category": category,
        "sub_category": sub_category,
        "description": description,
        "evidence_url": evidence_url,
        "ltv_tier": ltv_tier,
        "status": "Open",
    }
    _MOCK_TICKETS_BY_USER.setdefault(user_id, []).append(ticket)
    logger.info("[MOCK zoho_client] created ticket %s for user=%s category=%s", ticket_id, user_id, category)
    return ticket


def get_tickets(user_id: str) -> list:
    return _MOCK_TICKETS_BY_USER.get(user_id, [])
