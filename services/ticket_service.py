"""Wraps zoho_client so every ticket automatically carries the visitor's
real LTV tier — CS triages by tier without the model having to think
about it. All triage/priority/resolution after creation is CS's, not
this bot's, same posture as AstroHelp.

Also persists to the admin dashboard's own SQLite store (dashboard/db.py)
so a real CS/ops person can actually see and work these tickets, linked
back to the conversation that raised them. The dashboard DB is the
source of truth this app itself reads from (get_tickets below, and the
whole /admin/tickets UI) — Zoho Desk is CS's own system of record, kept
in sync one-way (push only) via zoho_client.
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
        description, evidence_url, tier, zoho_ticket_id=ticket.get('zoho_ticket_id'),
    )
    return ticket


def get_tickets(user_id: str) -> list:
    return dashboard_db.list_tickets_for_user(user_id)


def update_ticket_status(ticket_id: int, status: str, note: str = None) -> bool:
    """Used by the admin dashboard — updates the local record, then
    best-effort pushes the same status to Zoho if this ticket was
    actually created there (zoho_ticket_id set, i.e. ZOHO_MOCK_MODE was
    off at creation time)."""
    ticket = dashboard_db.get_ticket(ticket_id)
    ok = dashboard_db.update_ticket_status(ticket_id, status, note)
    if ok and ticket and ticket.get('zoho_ticket_id'):
        zoho_client.update_status(ticket['zoho_ticket_id'], status)
    return ok
