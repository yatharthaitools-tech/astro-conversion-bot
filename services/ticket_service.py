"""Wraps zoho_client so every ticket automatically carries the visitor's
real LTV tier — CS triages by tier without the model having to think
about it. All triage/priority/resolution after creation is CS's, not
this bot's, same posture as AstroHelp.

Also persists to the admin dashboard's own Postgres store (dashboard/db.py)
so a real CS/ops person can actually see and work these tickets, linked
back to the conversation that raised them. The dashboard DB is the
source of truth this app itself reads from (get_tickets below, and the
whole /admin/tickets UI) — Zoho Desk is CS's own system of record.

Sync with Zoho is two-way now: ticket creation still pushes out (this
file -> zoho_client), and app.py's /webhooks/zoho pulls status/category/
agent-reply updates back in from Zoho's side (see that route's own
docstring for the webhook contract) — this module doesn't handle the
inbound direction itself, dashboard/db.py's update_ticket_fields/
record_agent_message do.
"""
from datetime import datetime, timedelta, timezone

from dashboard import db as dashboard_db
from integrations import zoho_client
from services import ltv_service

# #5's reopen window — a visitor can reopen a Resolved/Closed ticket
# within this long after it was resolved; past it, they raise a new one
# instead (see reopen_ticket below).
REOPEN_WINDOW_HOURS = 72

# How much of the conversation actually reaches Zoho as context — the
# whole thing could be very long by the time a ticket's raised; this caps
# it to what's actually useful for an agent picking up the case cold,
# not the entire chat history verbatim.
MAX_TRANSCRIPT_MESSAGES = 30


def _build_transcript(session_id: str) -> str:
    """Plain-text 'Visitor: ...' / 'Tara: ...' log — see #2: an agent
    should have context of what already happened in the bot conversation
    without needing this dashboard open in a second tab. Best-effort:
    returns '' (never raises) if the conversation can't be read, same
    posture as the rest of this module's Zoho calls."""
    try:
        conv = dashboard_db.get_conversation(session_id) if session_id else None
    except Exception:
        return ""
    if not conv or not conv.get('messages'):
        return ""
    lines = []
    for msg in conv['messages'][-MAX_TRANSCRIPT_MESSAGES:]:
        speaker = {'user': 'Visitor', 'bot': 'Tara', 'agent': 'Agent'}.get(msg.get('role'), msg.get('role'))
        lines.append(f"{speaker}: {msg.get('text', '')}")
    return "\n".join(lines)


def create_ticket(user_id: str, category: str, sub_category: str, description: str,
                   evidence_url: str = None, session_id: str = None, language: str = None) -> dict:
    tier = ltv_service.get_tier(user_id)
    transcript = _build_transcript(session_id)
    ticket = zoho_client.create_ticket(
        user_id, category, sub_category, description, evidence_url,
        ltv_tier=tier, conversation_transcript=transcript,
    )
    dashboard_db.record_ticket(
        ticket['ticket_id'], session_id, user_id, category, sub_category,
        description, evidence_url, tier, zoho_ticket_id=ticket.get('zoho_ticket_id'),
        language=language,
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


def update_ticket_assignee(ticket_id: int, admin_id) -> bool:
    """Manual reassignment from the ticket detail page — new tickets are
    already auto-assigned round-robin (dashboard_db.record_ticket), this
    is just for correcting/reassigning one after the fact. admin_id may
    be None to unassign. Local-only — Zoho Desk's own agent assignment
    isn't wired up here (this module's inbound sync only pulls status/
    category/replies, not assignment)."""
    return dashboard_db.update_ticket_assignee(ticket_id, admin_id)


def reopen_ticket(user_id: str, note: str = None) -> dict:
    """#5: the visitor's own most recent Resolved/Closed ticket, reopened
    if it's within REOPEN_WINDOW_HOURS of resolution — else left alone,
    with enough info in the result for the caller (agent/tool_registry.py)
    to tell the visitor to raise a new one instead. Always operates on
    THEIR most recent resolved ticket, never a ticket_id the model could
    supply itself — same reasoning as this app's other user-scoped tools
    (e.g. get_tickets): trust the verified identity, not model input, for
    which record this touches.
    """
    ticket = dashboard_db.get_latest_resolved_ticket(user_id)
    if not ticket:
        return {"reopened": False, "reason": "no_resolved_ticket"}

    resolved_at = ticket.get('resolved_at')
    if not resolved_at:
        # Resolved/Closed but somehow no resolved_at (shouldn't happen via
        # the normal update_ticket_status path, but fail closed — can't
        # prove it's within the window, so don't reopen it).
        return {"reopened": False, "reason": "unknown_resolution_time", "ticket_id": ticket['ticket_ref']}

    resolved_dt = datetime.fromisoformat(resolved_at)
    if resolved_dt.tzinfo is None:
        resolved_dt = resolved_dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - resolved_dt

    if age > timedelta(hours=REOPEN_WINDOW_HOURS):
        return {
            "reopened": False, "reason": "past_reopen_window",
            "ticket_id": ticket['ticket_ref'], "resolved_at": resolved_at,
        }

    ok = update_ticket_status(ticket['id'], "Open", note or "Reopened by visitor")
    return {"reopened": ok, "ticket_id": ticket['ticket_ref']}
