"""Zoho Desk REST API v1 — real integration, mirroring the OAuth
refresh-token flow and create_ticket shape already verified live in
AstroHelp's app/integrations/zoho_client.py (same Zoho org). Gated by
ZOHO_MOCK_MODE, same convention as gemini_client's own gate: unset or
"true" stays mocked (an in-memory fake ticket, no network call), and only
an explicit "false" makes a real call.

create_ticket pre-fills category/sub-issue/transcript/evidence/LTV tier —
CS owns all triage/priority/resolution after that, same as the AstroHelp
pattern; this bot's job stops at "raise a well-formed ticket."

IMPORTANT before flipping ZOHO_MOCK_MODE=false for real: the
category/sub-issue labels below (_ZOHO_CATEGORY_MAP, _ZOHO_SUB_ISSUE_
KEYWORDS) and ZOHO_DEPARTMENT_ID are guesses at reasonable values, not
verified against a live Zoho Desk portal the way AstroHelp's were —
Zoho enforces strict validation on custom picklist fields (AstroHelp hit
this directly: a ticket missing/mismatching a required field fails to
even save). Check these against your actual Zoho Desk Category/Sub Issue
picklists before going live, and don't reuse AstroHelp's own
ZOHO_DEPARTMENT_ID as-is — that's its astrologer-support queue; visitor
tickets from this bot belong in a separate department/queue, or they'll
land mixed in with astrologer complaints.
"""
import logging
import os
import time
import uuid

import requests

logger = logging.getLogger(__name__)

ZOHO_MOCK_MODE = os.environ.get('ZOHO_MOCK_MODE', 'true').lower() != 'false'
ZOHO_ACCOUNTS_DOMAIN = os.environ.get('ZOHO_ACCOUNTS_DOMAIN', 'https://accounts.zoho.in')
ZOHO_API_DOMAIN = os.environ.get('ZOHO_API_DOMAIN', 'https://desk.zoho.in')
ZOHO_CLIENT_ID = os.environ.get('ZOHO_CLIENT_ID', '')
ZOHO_CLIENT_SECRET = os.environ.get('ZOHO_CLIENT_SECRET', '')
ZOHO_REFRESH_TOKEN = os.environ.get('ZOHO_REFRESH_TOKEN', '')
ZOHO_ORG_ID = os.environ.get('ZOHO_ORG_ID', '')
ZOHO_DEPARTMENT_ID = os.environ.get('ZOHO_DEPARTMENT_ID', '')

# This app's own categories (see agent/tool_schemas.py's CREATE_SUPPORT_
# TICKET) mapped to placeholder Zoho Category picklist labels — adjust to
# match your real portal, same caveat as the module docstring above.
_ZOHO_CATEGORY_MAP = {
    "payment": "Payment Queries",
    "refund": "Refund Request",
    "account": "App Features",
    "astrologer_queue": "User Queries",
    "billing_dispute": "Payment Queries",
    "quality_complaint": "Trust / Quality Concern",
    "feature_request": "App Features",
    "technical": "Tech Issues",
    "language_change": "App Features",
    "report": "User Queries",
    "escalation": "Escalation Request",
}
_DEFAULT_ZOHO_CATEGORY = "User Queries"

_MOCK_TICKETS_BY_USER: dict = {}

_access_token: str = None
_access_token_expires_at: float = 0.0


def _get_access_token() -> str:
    global _access_token, _access_token_expires_at
    # 60s safety margin so a token doesn't expire mid-request.
    if _access_token and time.monotonic() < _access_token_expires_at - 60:
        return _access_token

    response = requests.post(
        f"{ZOHO_ACCOUNTS_DOMAIN}/oauth/v2/token",
        params={
            "refresh_token": ZOHO_REFRESH_TOKEN,
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    _access_token = data["access_token"]
    _access_token_expires_at = time.monotonic() + data.get("expires_in", 3600)
    return _access_token


def _headers() -> dict:
    return {
        "Authorization": f"Zoho-oauthtoken {_get_access_token()}",
        "orgId": ZOHO_ORG_ID,
    }


def create_ticket(user_id: str, category: str, sub_category: str, description: str,
                   evidence_url: str = None, ltv_tier: str = None) -> dict:
    ticket_ref = f"AST-{uuid.uuid4().hex[:6].upper()}"
    zoho_id = None

    if ZOHO_MOCK_MODE:
        ticket = {
            "ticket_id": ticket_ref, "category": category, "sub_category": sub_category,
            "description": description, "evidence_url": evidence_url, "ltv_tier": ltv_tier,
            "status": "Open", "zoho_ticket_id": None,
        }
        _MOCK_TICKETS_BY_USER.setdefault(user_id, []).append(ticket)
        logger.info("[MOCK zoho_client] created ticket %s for user=%s category=%s", ticket_ref, user_id, category)
        return ticket

    try:
        payload = {
            "subject": f"[{ticket_ref}] {category} / {sub_category} ({ltv_tier or 'unranked'})",
            "description": description,
            "departmentId": ZOHO_DEPARTMENT_ID,
            "status": "Open",
            "category": _ZOHO_CATEGORY_MAP.get(category, _DEFAULT_ZOHO_CATEGORY),
            # Every ticket here originates from the in-app chatbot, not an
            # actual phone call — without this Zoho defaults new tickets to
            # "Phone", which is wrong for all of them (same fix AstroHelp
            # needed for its own chatbot-raised tickets).
            "channel": "Chat",
            "contact": {"lastName": f"AstroLokal visitor {user_id}"},
        }
        if evidence_url:
            payload["description"] = f"{description}\n\nEvidence: {evidence_url}"

        response = requests.post(
            f"{ZOHO_API_DOMAIN}/api/v1/tickets", headers=_headers(), json=payload, timeout=10,
        )
        response.raise_for_status()
        zoho_id = response.json().get("id")
    except Exception:
        logger.exception("Zoho Desk ticket creation failed for %s — falling back to local-only ticket", ticket_ref)

    return {
        "ticket_id": ticket_ref, "category": category, "sub_category": sub_category,
        "description": description, "evidence_url": evidence_url, "ltv_tier": ltv_tier,
        "status": "Open", "zoho_ticket_id": zoho_id,
    }


def update_status(zoho_ticket_id: str, status: str) -> None:
    """Best-effort push of a dashboard status change to Zoho — never
    raises, same posture as create_ticket's real-network path. No-ops
    when mocked or when this ticket was never actually pushed to Zoho
    (zoho_ticket_id is None)."""
    if ZOHO_MOCK_MODE or not zoho_ticket_id:
        return
    try:
        response = requests.patch(
            f"{ZOHO_API_DOMAIN}/api/v1/tickets/{zoho_ticket_id}",
            headers=_headers(), json={"status": status}, timeout=10,
        )
        response.raise_for_status()
    except Exception:
        logger.exception("Zoho Desk status update failed for ticket %s", zoho_ticket_id)


def get_tickets(user_id: str) -> list:
    """Only used in mock mode — see services/ticket_service.get_tickets,
    which reads the local dashboard DB in real mode instead (this app has
    no Zoho contact/search wiring to look a visitor's tickets up live)."""
    return _MOCK_TICKETS_BY_USER.get(user_id, [])
