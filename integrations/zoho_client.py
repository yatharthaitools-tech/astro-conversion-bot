"""Zoho Desk REST API v1 — real integration, mirroring the OAuth
refresh-token flow and create_ticket shape already verified live in
AstroHelp's app/integrations/zoho_client.py (same Zoho org). Gated by
ZOHO_MOCK_MODE, same convention as gemini_client's own gate: unset or
"true" stays mocked (an in-memory fake ticket, no network call), and only
an explicit "false" makes a real call.

create_ticket pre-fills category/sub-issue/transcript/evidence/LTV tier —
CS owns all triage/priority/resolution after that, same as the AstroHelp
pattern; this bot's job stops at "raise a well-formed ticket."

_ZOHO_CATEGORY_MAP below is now verified against the real "Astro Lokal"
Desk portal (department 271863000000010772's ticket layout, id
271863000000011350, pulled live via GET /api/v1/layouts/<id>) — every
value is one of the real Category field's 15 allowed picklist values.

cf_sub_issue (the real "Sub Issue Category" field) is a MANDATORY
picklist with its own fixed 54-value list, verified the same way — but
this bot's own `sub_category` argument is free-form model-generated text
(see agent/tool_schemas.py's CREATE_SUPPORT_TICKET — no enum), which can
never reliably match one of those exact 54 strings. Rather than risk an
INVALID_DATA rejection on every ticket, create_ticket sends a fixed,
always-valid placeholder ("General Inquiry") for cf_sub_issue just to
satisfy the mandatory-field requirement, and keeps the model's actual
free-text sub-category where a human reads it (the subject line and
description) instead of trying to force it into that picklist.

cf_user_type (the real "User Type" field — Astrologer / Customer /
Onboarding / N/A) is ALSO mandatory on the real layout, with
defaultValue "Astrologer" — this app's own AstroHelp sibling raises
tickets for BOTH astrologers and customers, so that field exists to
distinguish them; every ticket this bot raises is about a chat visitor,
never an astrologer, so create_ticket always sends "Customer" explicitly
rather than silently inheriting Zoho's own wrong default.

cf_user_id / cf_ltv_tier do NOT exist as custom fields on the real
layout at all (confirmed from the same layout pull — there's no field
with either of those purposes defined). create_ticket deliberately does
NOT send them in customFields: an unrecognized custom field key can
either be silently dropped or cause a validation error depending on
Zoho's mood, and either way nothing is gained by sending it. That
information still reaches CS via contact.lastName (user_id) and the
subject line (ltv_tier) below. If/when real custom fields are created
for these in Zoho Desk, add their actual apiName back into customFields
here.
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
# TICKET) mapped to real Zoho Category picklist labels — verified against
# the live "Astro Lokal" ticket layout's actual 15 allowed values (see
# module docstring). Every value on the right must be one of: App
# Features, App Guidance, Astrologer Queries, Low Visibility, Onboarding,
# Payment Queries, Refund Request, Profile changes, Tech Issues, User
# Queries, Withdrawal / KYC, Incomplete Query, Promotional Query,
# Transactional Query, Internal Testing.
_ZOHO_CATEGORY_MAP = {
    "payment": "Payment Queries",
    "refund": "Refund Request",
    "account": "Profile changes",
    "astrologer_queue": "Astrologer Queries",
    "billing_dispute": "Payment Queries",
    "quality_complaint": "User Queries",
    "feature_request": "App Features",
    "technical": "Tech Issues",
    "language_change": "App Features",
    "report": "User Queries",
    "escalation": "User Queries",
}
_DEFAULT_ZOHO_CATEGORY = "User Queries"

# The real cf_sub_issue field (see module docstring) is a mandatory
# picklist with its own fixed list this bot's free-text sub_category
# can't reliably match — this exact string is one of its 54 real allowed
# values, used purely to satisfy the mandatory-field requirement.
_ZOHO_SUB_ISSUE_PLACEHOLDER = "General Inquiry"

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
                   evidence_url: str = None, ltv_tier: str = None, ltv_amount: float = None,
                   conversation_transcript: str = None) -> dict:
    ticket_ref = f"AST-{uuid.uuid4().hex[:6].upper()}"
    zoho_id = None

    if ZOHO_MOCK_MODE:
        ticket = {
            "ticket_id": ticket_ref, "category": category, "sub_category": sub_category,
            "description": description, "evidence_url": evidence_url, "ltv_tier": ltv_tier,
            "ltv_amount": ltv_amount, "status": "Open", "zoho_ticket_id": None,
        }
        _MOCK_TICKETS_BY_USER.setdefault(user_id, []).append(ticket)
        logger.info("[MOCK zoho_client] created ticket %s for user=%s category=%s", ticket_ref, user_id, category)
        return ticket

    # No cf_ltv_tier/cf_ltv_amount custom field exists on the real layout
    # (see module docstring), so the exact ₹ figure — not just the tier
    # bucket — goes into the subject (for a glance at the ticket list)
    # and as its own line in the description (for anyone actually reading
    # the ticket). ltv_amount is the real lifetime-spend figure from the
    # link that opened the chat (agent/context.py's SessionContext.ltv),
    # not a mocked value.
    ltv_label = f"₹{ltv_amount:,.2f}" if ltv_amount is not None else "unranked"

    try:
        payload = {
            "subject": f"[{ticket_ref}] {category} / {sub_category} ({ltv_tier or 'unranked'}, {ltv_label})",
            "description": f"Visitor LTV: {ltv_label} (tier: {ltv_tier or 'unranked'})\n\n{description}",
            "departmentId": ZOHO_DEPARTMENT_ID,
            "status": "Open",
            "category": _ZOHO_CATEGORY_MAP.get(category, _DEFAULT_ZOHO_CATEGORY),
            # Every ticket here originates from the in-app chatbot, not an
            # actual phone call — without this Zoho defaults new tickets to
            # "Phone", which is wrong for all of them (same fix AstroHelp
            # needed for its own chatbot-raised tickets).
            "channel": "Chat",
            "contact": {"lastName": f"AstroLokal visitor {user_id}"},
            # cf_sub_issue is mandatory on the real layout but has no
            # field matching this bot's own free-text sub_category (see
            # module docstring) — just satisfies that requirement.
            # cf_user_type is ALSO mandatory on the real layout, with
            # defaultValue "Astrologer" — every ticket this bot raises is
            # about a CHAT VISITOR, never an astrologer, so this must be
            # explicit or every ticket silently gets mistagged as the
            # wrong user type via Zoho's own default. cf_user_id/
            # cf_ltv_tier are deliberately NOT sent here: no such custom
            # fields exist on the real layout (see docstring); that info
            # already reaches CS via contact.lastName and the subject
            # line below instead.
            "customFields": {
                "cf_sub_issue": _ZOHO_SUB_ISSUE_PLACEHOLDER,
                "cf_user_type": "Customer",
            },
        }
        if evidence_url:
            payload["description"] = f"{payload['description']}\n\nEvidence: {evidence_url}"
        if conversation_transcript:
            # #2: agent has context of the issue without a second tool —
            # the actual bot conversation, not just the one-line summary
            # above. Appended to the same description field rather than a
            # separate threadContent/comment call — one less request, and
            # it's visible to the agent from the ticket's very first open.
            payload["description"] = (
                f"{payload['description']}\n\n--- Conversation with Tara (AstroLokal bot) ---\n"
                f"{conversation_transcript}"
            )

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
        "ltv_amount": ltv_amount, "status": "Open", "zoho_ticket_id": zoho_id,
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
