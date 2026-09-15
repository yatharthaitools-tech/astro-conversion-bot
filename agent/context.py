"""SessionContext: built once per request from a verified identity source,
never from tool input or the model. executor.py is what actually enforces
this (see its docstring) — this module just defines the shape.

The native app hands off a real user_id + oauth_token (see app.py's /
route and static/script.js) whenever it opens this chat in a WebView.
resolve_session() below trusts that pair as the real identity — but
"trust" here just means "use it", not "cryptographically verified": there
is no JWT/signature check against the app's own auth service yet, only
requiring both fields be non-empty together (a bare user_id with no
token at all is NOT trusted — see _real_identity_from). Add real
verification (decode/introspect oauth_token against whatever issues it)
before this identity is used for anything money-moving; until then this
is "mocked-then-real" the same way the rest of this app's integrations
are, and this docstring is the flag for that gap.

Without either field (local/dev testing, or the page opened outside the
app), a stable pseudo-id is derived from session_id alone instead, same
as before this app/oauth handoff existed.
"""
import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionContext:
    user_id: str
    session_id: str
    language: str  # detected from the CURRENT message, never a stored preference
    oauth_token: Optional[str] = None  # unverified — see module docstring
    last_attachment_url: Optional[str] = None
    has_prior_reply: bool = False
    trace: list = field(default_factory=list)
    ui_action: Optional[dict] = None  # set by a tool handler that opened a card/sheet
    show_feedback: bool = False  # set by mark_issue_resolved — closes the thread client-side


def _real_identity_from(payload: dict) -> tuple:
    """Both user_id and oauth_token must be present together to be used —
    a bare user_id with no token is exactly what a spoofed request would
    send, so it's treated the same as having neither."""
    user_id = str((payload or {}).get("user_id") or "").strip()
    oauth_token = str((payload or {}).get("oauth_token") or "").strip()
    if user_id and oauth_token:
        return user_id, oauth_token
    return None, None


def resolve_session(payload: dict, session_id: str, language: str, history: list) -> SessionContext:
    """Resolves the identity for this request — the real app-supplied
    user_id when present (see module docstring for what "real" doesn't
    yet mean), else a session-derived pseudo-id."""
    real_user_id, oauth_token = _real_identity_from(payload)
    user_id = real_user_id or ("u_" + hashlib.sha256(session_id.encode()).hexdigest()[:12])
    has_prior_reply = any(
        (turn.get("sender") or turn.get("role")) == "bot" for turn in (history or [])
    )
    return SessionContext(
        user_id=user_id,
        session_id=session_id,
        language=language,
        oauth_token=oauth_token,
        has_prior_reply=has_prior_reply,
    )
