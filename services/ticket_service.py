"""Wraps zoho_client so every ticket automatically carries the visitor's
real LTV tier — CS triages by tier without the model having to think
about it. All triage/priority/resolution after creation is CS's, not
this bot's, same posture as AstroHelp.

Also persists to the admin dashboard's own SQLite store (dashboard/db.py)
so a real CS/ops person can actually see and work these tickets, linked
back to the conversation that raised them — Zoho stays the mocked
system-of-record stand-in, the dashboard is what this repo can actually
show today.
"""
from dashboard import db as dashboard_db
from integrations import zoho_client
from services import ltv_service


def create_ticket(user_id: str, category: str, sub_category: str, description: str,
                   evidence_url: str = None, session_id: str = None) -> dict:
    tier = ltv_service.get_tier(user_id)
    ticket = zoho_client.create_ticket(
        user_id, category, sub_category, description, evidence_url, ltv_tier=tier
    )
    dashboard_db.record_ticket(
        ticket['ticket_id'], session_id, user_id, category, sub_category,
        description, evidence_url, tier,
    )
    return ticket


def get_tickets(user_id: str) -> list:
    return zoho_client.get_tickets(user_id)
