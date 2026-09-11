"""SessionContext: built once per request from a verified identity source,
never from tool input or the model. executor.py is what actually enforces
this (see its docstring) — this module just defines the shape.

MOCKED AUTH: there's no real JWT/session verification wired up yet (no
DB, no login) — user_id is resolved from a query/body param with a stable
pseudo-id fallback so the same browser session always maps to the same
mock user. Replace resolve_session() with real JWT verification once
auth exists; nothing else needs to change, same "mocked-then-real"
pattern as the integrations.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionContext:
    user_id: str
    session_id: str
    language: str  # detected from the CURRENT message, never a stored preference
    last_attachment_url: Optional[str] = None
    has_prior_reply: bool = False
    trace: list = field(default_factory=list)
    ui_action: Optional[dict] = None  # set by a tool handler that opened a card/sheet
    show_feedback: bool = False  # set by mark_issue_resolved — closes the thread client-side


def resolve_session(payload: dict, session_id: str, language: str, history: list) -> SessionContext:
    """Resolves the verified user identity for this request.

    MOCKED: real deployments would decode a JWT here (matching AstroHelp's
    astrologer-token pattern) and never trust a user_id the client sends.
    Until that exists, a stable pseudo-id is derived from session_id alone
    — deliberately ignoring any user_id the client might send, so the
    security boundary in executor.py is exercised the same way it will be
    once real auth lands, rather than trusting client input today and
    tightening it later.
    """
    pseudo_user_id = "u_" + hashlib.sha256(session_id.encode()).hexdigest()[:12]
    has_prior_reply = any(
        (turn.get("sender") or turn.get("role")) == "bot" for turn in (history or [])
    )
    return SessionContext(
        user_id=pseudo_user_id,
        session_id=session_id,
        language=language,
        has_prior_reply=has_prior_reply,
    )
