"""Encrypted link tokens — lets the link that opens this chat (the
"Chat with us" support/CRM link today, and potentially the native app's
own WebView handoff later) carry user_id/name/ltv without putting them
in the URL as plaintext, where they're visible in browser history,
server access logs, a forwarded/leaked link, or any analytics tool that
logs full request URLs.

Uses Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography`
package) — a single shared symmetric key both sides hold. Whichever
system generates the link (this app's own code, or the separate
support/CRM system) encrypts a small JSON payload with that key; this
app decrypts with the same key. Fernet embeds a timestamp, so a stale or
leaked link can be rejected after LINK_TOKEN_TTL_SECONDS instead of
staying valid forever — encrypt_identity()/decrypt_identity() are a
thin wrapper around that, not a custom scheme.

Fernet has mature implementations in effectively every mainstream
language (Node: `fernet`, Java: `fernet-java8`, Go: `fernet-go`, PHP:
`fernet-php`, Ruby: `fernet`, ...), so whatever language the
link-generating system is written in, it can produce a token this app
reads with nothing more than LINK_TOKEN_KEY shared between the two.

Backward compatible by design: when LINK_TOKEN_KEY isn't set, or a
request carries no token param at all, app.py's / route falls back to
the old plaintext user_id/name/ltv query params untouched. This only
takes over once the link-generating side actually switches to sending
an encrypted token — nothing breaks for a system that hasn't migrated
yet.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

LINK_TOKEN_KEY = os.environ.get('LINK_TOKEN_KEY', '').strip()
# A session-opening link is meant to be used right away — long enough
# that a slow page load/redirect chain doesn't fail, short enough that
# a link caught in a log or forwarded later is worthless by then.
LINK_TOKEN_TTL_SECONDS = int(os.environ.get('LINK_TOKEN_TTL_SECONDS', str(24 * 3600)))


def is_configured() -> bool:
    return bool(LINK_TOKEN_KEY)


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(LINK_TOKEN_KEY.encode())


def generate_key() -> str:
    """One-time setup helper — run this once to produce the value for
    LINK_TOKEN_KEY, then share it (out of band, e.g. a secrets manager —
    never by committing it or pasting it into chat) with whichever
    system(s) will generate tokens. Not called by the app itself."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def encrypt_identity(user_id: str, name: str = '', ltv: str = '') -> str:
    """Produces a token exactly like decrypt_identity() below expects to
    read — mainly for this app's own tests/tooling. The real link
    GENERATOR (the support/CRM system) implements the same Fernet
    encryption independently, in its own language, over the same JSON
    shape: {"user_id": ..., "name": ..., "ltv": ...} (ltv as a plain
    string, e.g. "13924.00" — this app parses it the same way the
    legacy ?ltv= query param already was)."""
    payload = json.dumps({'user_id': user_id, 'name': name, 'ltv': ltv}).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_identity(token: str):
    """Returns {'user_id', 'name', 'ltv'} (each a string, possibly
    empty) on success, or None on anything wrong — expired (past
    LINK_TOKEN_TTL_SECONDS), wrong/rotated key, tampered, or just
    malformed. Never raises: a bad token means "open with no identity
    at all", same as a plain visit with no params — app.py's / route
    deliberately does NOT fall back to reading legacy plaintext params
    when a token was present but failed to decrypt, or a crafted
    `?token=garbage&user_id=...&name=...` could bypass encryption
    entirely by just sending a broken token alongside spoofed plaintext."""
    if not is_configured() or not token:
        return None
    try:
        raw = _fernet().decrypt(token.encode(), ttl=LINK_TOKEN_TTL_SECONDS)
        data = json.loads(raw)
        return {
            'user_id': str(data.get('user_id') or ''),
            'name': str(data.get('name') or ''),
            'ltv': str(data.get('ltv') or ''),
        }
    except Exception:
        logger.warning('Failed to decrypt link token', exc_info=True)
        return None
