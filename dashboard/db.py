"""PostgreSQL persistence for the admin dashboard — conversations, messages,
tool-call traces, tickets, and ticket status history.

This is a NEW store, separate from integrations/s3_client.py's existing
fire-and-forget event log (kept as-is, still feeds Redash). Nothing here
replaces that — this is the queryable store the admin dashboard actually
reads from, mirroring what astrohelp's chat_sessions/chat_messages/tickets
tables do for its own admin dashboard.

Was SQLite (a local dashboard.db file) until this was moved to Postgres —
a pod on Devtron/Kubernetes has an ephemeral filesystem, so every
redeploy/restart was silently wiping the entire chat/ticket history. A
managed Postgres instance survives that.

Schema:
    conversations(session_id PK, user_id, first_seen_at, last_seen_at,
                   turn_count, last_language, resolved_by, resolved_at,
                   rating, rated_at)
    messages(id PK, session_id FK, role, text, source, tool_trace,
             card_shown, created_at)
    tickets(id PK, ticket_ref, session_id FK, user_id, category,
            sub_category, description, evidence_url, ltv_tier, status,
            created_at, resolved_at)
    ticket_status_history(id PK, ticket_id FK, status, note, changed_at)
    coin_credit_attempts(id PK, idempotency_key UNIQUE, user_id, booking_id,
                          amount, status, http_status, response, error,
                          created_at, updated_at) — at-most-once firing gate
        for integrations/coin_credit_client.py, same pattern as the
        daily-cast app's own coin_credit_attempts table for the identical
        no-idempotency-of-its-own Coin API: an attempt row is inserted
        (UNIQUE idempotency_key) BEFORE the real HTTP call, so a
        concurrent/repeated credit_coins call loses the insert race and
        never fires twice. A non-success row is never auto-retried — it's
        the reconciliation worklist for ops.
    events(id PK, session_id, user_id, event_type, event_data JSONB,
           created_at) — client-side UI interaction analytics (quick-reply
        taps, send/upload/close/connect-card taps, ratings given), fed by
        app.py's /event route and static/script.js's trackEvent(). Free-
        form: event_type isn't an enum here, script.js is the source of
        truth for which types actually get fired. Feeds get_event_
        analytics() below, including the D0D/W0W/M0M comparisons.
    admin_users(id PK, email UNIQUE, password_hash, role, created_at) —
        per-person dashboard logins (dashboard/auth.py), replacing the
        old single shared ADMIN_PASSWORD. role is one of 'admin' /
        'support_agent' / 'analyst' (see auth.py's ROLES).
    dashboard_config(key PK, value) — small KV store for values that must
        be identical across every worker/pod; today just 'session_secret'
        (see get_or_create_session_secret below).

`tool_trace` is stored as JSONB (list of {"tool": str, "ok": bool} dicts,
exactly ctx.trace's shape) — read back for display, never queried into.
"""
import json
import logging
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def _normalize_database_url(url: str) -> str:
    """Strips a SQLAlchemy-style '+driver' off the scheme (e.g.
    'postgresql+psycopg2://' -> 'postgresql://') — psycopg2.connect() is
    called directly here, no SQLAlchemy involved, and rejects the
    '+driver' form outright ('invalid dsn: missing "=" after ...').
    Ops-provided connection strings for this org's other services use
    that SQLAlchemy form, so tolerate it rather than requiring it be
    hand-edited before it's pasted into Devtron."""
    return re.sub(r'^(postgres(?:ql)?)\+\w+://', r'\1://', url)


DATABASE_URL = _normalize_database_url(os.environ.get(
    'DATABASE_URL', 'postgresql://astrobot:astrobot@localhost:5432/astro_conversion_bot'
))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    last_language TEXT,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    rating INTEGER,
    rated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES conversations(session_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'bot')),
    text TEXT NOT NULL,
    source TEXT,
    tool_trace JSONB,
    card_shown BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id SERIAL PRIMARY KEY,
    ticket_ref TEXT UNIQUE NOT NULL,
    session_id TEXT REFERENCES conversations(session_id),
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    sub_category TEXT,
    description TEXT,
    evidence_url TEXT,
    ltv_tier TEXT,
    status TEXT NOT NULL DEFAULT 'Open',
    created_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    zoho_ticket_id TEXT
);

CREATE TABLE IF NOT EXISTS ticket_status_history (
    id SERIAL PRIMARY KEY,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    status TEXT NOT NULL,
    note TEXT,
    changed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS coin_credit_attempts (
    id SERIAL PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    user_id TEXT NOT NULL,
    booking_id TEXT,
    amount INTEGER NOT NULL,
    purpose TEXT,
    status TEXT NOT NULL,
    http_status INTEGER,
    response JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    session_id TEXT,
    user_id TEXT,
    event_type TEXT NOT NULL,
    event_data JSONB,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'support_agent', 'analyst')),
    created_at TIMESTAMPTZ NOT NULL
);

-- Single-row-per-key store for small values that must be identical across
-- every worker/pod (see get_or_create_session_secret below) — deliberately
-- not a bigger config system, just a KV escape hatch for this one need.
CREATE TABLE IF NOT EXISTS dashboard_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_last_seen ON conversations(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_coin_credit_attempts_status ON coin_credit_attempts(status);
CREATE INDEX IF NOT EXISTS idx_events_type_created ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""

# Columns added after the initial release — safe to re-run against a
# database that already has them (IF NOT EXISTS is native here, unlike
# SQLite's ALTER TABLE ADD COLUMN, so no try/except dance is needed).
_MIGRATIONS = [
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS resolved_by TEXT",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS rating INTEGER",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS rated_at TIMESTAMPTZ",
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS zoho_ticket_id TEXT",
    "ALTER TABLE coin_credit_attempts ADD COLUMN IF NOT EXISTS purpose TEXT",
    # admin_users doesn't exist yet when the CREATE TABLE tickets statement
    # above runs (it's defined later in _SCHEMA), so this FK has to be
    # added here instead, after both tables exist.
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS assigned_admin_id INTEGER REFERENCES admin_users(id)",
    # Ticket's own language (from conversations.last_language at creation
    # time, not live-tracked afterward) — what #4's language-based
    # round robin actually matches against.
    "ALTER TABLE tickets ADD COLUMN IF NOT EXISTS language TEXT",
    # Which of Hindi/Tamil/Telugu/Malayalam (language codes, see
    # LANGUAGE_CODES below) a Support Agent/Admin actually handles —
    # empty/NULL means "no language preference set", not "handles
    # nothing" (see _pick_round_robin_assignee's fallback).
    "ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS languages TEXT[] NOT NULL DEFAULT '{}'",
    # 'agent' = a real Zoho Desk agent's reply, synced in via the webhook
    # (see app.py's /webhooks/zoho) — was user/bot only. Postgres names a
    # column-level CHECK this way by default; DROP+ADD is the standard
    # idempotent way to widen one (there's no ALTER ... IF NOT EXISTS
    # equivalent for constraints).
    "ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_role_check",
    "ALTER TABLE messages ADD CONSTRAINT messages_role_check CHECK (role IN ('user', 'bot', 'agent'))",
]

# Hindi/Tamil/Telugu/Malayalam — the languages #4 asked for ticket routing
# to cover. Deliberately not the bot's full LANGUAGE_NAMES set (English/
# Hinglish/Marathi/Bengali aren't part of this routing requirement).
LANGUAGE_CODES = {'hi': 'Hindi', 'ta': 'Tamil', 'te': 'Telugu', 'ml': 'Malayalam'}


@contextmanager
def _connect():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
            for stmt in _MIGRATIONS:
                cur.execute(stmt)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dict(row) -> dict:
    """RealDictCursor rows come back with native datetime objects for
    TIMESTAMPTZ columns (psycopg2, unlike sqlite3, doesn't hand back
    strings) — the dashboard templates/JSON responses all still expect
    ISO-8601 strings (e.g. `created_at[:16]`), same as before this was
    Postgres, so convert here rather than touch every caller."""
    if row is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in dict(row).items()}


def ensure_conversation(session_id: str, user_id: str) -> None:
    """Guarantees a conversations row exists for this session before the
    agent's tool loop runs. record_turn() (which does the real turn_count/
    last_seen_at upsert) only runs after that loop finishes, but a tool
    called mid-loop — create_support_ticket — inserts into tickets with a
    FK reference to conversations.session_id. Without this, a ticket
    raised on a session's very first turn would fail that FK check."""
    now = _now()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO conversations (session_id, user_id, first_seen_at, last_seen_at, turn_count)
                       VALUES (%s, %s, %s, %s, 0)
                       ON CONFLICT (session_id) DO NOTHING""",
                    (session_id, user_id, now, now),
                )
    except psycopg2.Error:
        logger.warning("ensure_conversation failed for session %s", session_id, exc_info=True)


def record_turn(session_id: str, user_id: str, question: str, answer: str,
                language: str, source: str, tool_trace: list, card_shown: bool) -> None:
    """Called once per /ask turn — writes the user's question and the
    bot's answer as two message rows, and upserts the conversation's
    summary row. Also detects a resolution outcome (mark_issue_resolved
    -> resolved by the bot itself; create_support_ticket -> escalated to
    a human) straight from this turn's tool trace. Best-effort: a logging
    failure must never break the visitor's chat reply, same posture as
    s3_client.log_event."""
    now = _now()
    trace_json = json.dumps(tool_trace or [])
    resolved_by = None
    for call in (tool_trace or []):
        if call.get('tool') == 'mark_issue_resolved' and call.get('ok'):
            resolved_by = 'bot'
        elif call.get('tool') == 'create_support_ticket' and call.get('ok'):
            resolved_by = 'escalated'

    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO conversations (session_id, user_id, first_seen_at, last_seen_at, turn_count, last_language)
                       VALUES (%s, %s, %s, %s, 1, %s)
                       ON CONFLICT (session_id) DO UPDATE SET
                           last_seen_at = EXCLUDED.last_seen_at,
                           turn_count = conversations.turn_count + 1,
                           last_language = EXCLUDED.last_language""",
                    (session_id, user_id, now, now, language),
                )
                if resolved_by:
                    cur.execute(
                        "UPDATE conversations SET resolved_by = %s, resolved_at = %s WHERE session_id = %s",
                        (resolved_by, now, session_id),
                    )
                cur.execute(
                    "INSERT INTO messages (session_id, role, text, created_at) VALUES (%s, 'user', %s, %s)",
                    (session_id, question, now),
                )
                cur.execute(
                    """INSERT INTO messages (session_id, role, text, source, tool_trace, card_shown, created_at)
                       VALUES (%s, 'bot', %s, %s, %s, %s, %s)""",
                    (session_id, answer, source, trace_json, bool(card_shown), now),
                )
    except psycopg2.Error:
        logger.warning("record_turn failed for session %s", session_id, exc_info=True)


def record_rating(session_id: str, rating: int) -> bool:
    """Called by the /feedback endpoint once the visitor rates the chat."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE conversations SET rating = %s, rated_at = %s WHERE session_id = %s",
                    (rating, _now(), session_id),
                )
                return cur.rowcount > 0
    except psycopg2.Error:
        logger.warning("record_rating failed for session %s", session_id, exc_info=True)
        return False


def record_event(session_id: str, user_id: str, event_type: str, event_data: dict) -> None:
    """Fire-and-forget UI interaction logging — quick-reply taps, send/
    upload/close/connect-card taps, ratings given, called by app.py's
    /event route. Best-effort like every other write here: a logging
    failure must never surface to the visitor or block the interaction
    it's recording. Feeds get_event_analytics()'s breakdown and
    D0D/W0W/M0M numbers on the admin Analytics page."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO events (session_id, user_id, event_type, event_data, created_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (session_id, user_id, event_type, json.dumps(event_data or {}), _now()),
                )
    except psycopg2.Error:
        logger.warning("record_event failed for event_type %s", event_type, exc_info=True)


def list_conversations(limit: int = 50, offset: int = 0, language: str = None,
                        date_from: str = None, date_to: str = None) -> list:
    query = "SELECT * FROM conversations WHERE 1=1"
    params = []
    if language:
        query += " AND last_language = %s"
        params.append(language)
    if date_from:
        query += " AND last_seen_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND last_seen_at <= %s"
        params.append(date_to + "T23:59:59")
    query += " ORDER BY last_seen_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [_to_dict(r) for r in cur.fetchall()]


def count_conversations(language: str = None, date_from: str = None, date_to: str = None) -> int:
    query = "SELECT COUNT(*) AS n FROM conversations WHERE 1=1"
    params = []
    if language:
        query += " AND last_language = %s"
        params.append(language)
    if date_from:
        query += " AND last_seen_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND last_seen_at <= %s"
        params.append(date_to + "T23:59:59")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()['n']


def get_conversation(session_id: str) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM conversations WHERE session_id = %s", (session_id,))
            conv = cur.fetchone()
            if not conv:
                return None
            cur.execute(
                "SELECT * FROM messages WHERE session_id = %s ORDER BY id ASC", (session_id,)
            )
            messages = cur.fetchall()
        result = _to_dict(conv)
        result['messages'] = []
        for m in messages:
            msg = _to_dict(m)
            msg['tool_trace'] = msg['tool_trace'] or []
            result['messages'].append(msg)
        return result


# --- Tickets -----------------------------------------------------------

def _pick_round_robin_assignee(cur, language: str = None) -> int:
    """Whoever can actually work a ticket (admin or support_agent) AND has
    gone longest without a new one — a teammate with zero tickets yet
    (NULL last-assigned) always comes before anyone with a real history,
    so a newly-added agent gets pulled into rotation immediately instead
    of starting at the back of the line. Returns None if there's nobody
    eligible yet (e.g. a fresh install with only an analyst so far) —
    callers leave assigned_admin_id NULL in that case, same as an
    unassigned ticket always looked before this existed.

    language (one of dashboard.db.LANGUAGE_CODES, e.g. 'hi') prefers
    whoever has that language in admin_users.languages over anyone who
    doesn't — but never excludes non-matching agents outright: when
    nobody covers this language (or language is None/unsupported), the
    ORDER BY's first key is a no-op for everyone and this degrades
    straight to the plain longest-since-assigned pick, same as the no-
    language case always worked. That's the explicit product decision
    for the no-coverage case — route to whoever's around rather than
    leave it unassigned."""
    cur.execute(
        """SELECT au.id FROM admin_users au
           LEFT JOIN (
               SELECT assigned_admin_id, MAX(created_at) AS last_assigned
               FROM tickets WHERE assigned_admin_id IS NOT NULL
               GROUP BY assigned_admin_id
           ) t ON t.assigned_admin_id = au.id
           WHERE au.role IN ('admin', 'support_agent')
           ORDER BY
               CASE WHEN %(language)s IS NOT NULL AND %(language)s = ANY(au.languages) THEN 0 ELSE 1 END,
               t.last_assigned ASC NULLS FIRST,
               au.id ASC
           LIMIT 1""",
        {"language": language},
    )
    row = cur.fetchone()
    return row['id'] if row else None


def record_ticket(ticket_ref: str, session_id: str, user_id: str, category: str,
                   sub_category: str, description: str, evidence_url: str, ltv_tier: str,
                   zoho_ticket_id: str = None, language: str = None) -> None:
    now = _now()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                assignee_id = _pick_round_robin_assignee(cur, language)
                cur.execute(
                    """INSERT INTO tickets (ticket_ref, session_id, user_id, category, sub_category,
                                             description, evidence_url, ltv_tier, status, created_at,
                                             zoho_ticket_id, assigned_admin_id, language)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Open', %s, %s, %s, %s)
                       RETURNING id""",
                    (ticket_ref, session_id, user_id, category, sub_category, description,
                     evidence_url, ltv_tier, now, zoho_ticket_id, assignee_id, language),
                )
                ticket_id = cur.fetchone()['id']
                cur.execute(
                    "INSERT INTO ticket_status_history (ticket_id, status, note, changed_at) VALUES (%s, 'Open', 'Ticket created', %s)",
                    (ticket_id, now),
                )
    except psycopg2.Error:
        logger.warning("record_ticket failed for ticket %s", ticket_ref, exc_info=True)


def bulk_update_ticket_assignee(ticket_ids: list, admin_id) -> int:
    """Bulk reassignment for admins — e.g. moving everything off someone
    who's on leave. Returns how many rows actually changed."""
    if not ticket_ids:
        return 0
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET assigned_admin_id = %s WHERE id = ANY(%s)",
                (admin_id, ticket_ids),
            )
            return cur.rowcount


def update_ticket_assignee(ticket_id: int, admin_id: int) -> bool:
    """admin_id may be None to explicitly unassign."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE tickets SET assigned_admin_id = %s WHERE id = %s", (admin_id, ticket_id))
            return cur.rowcount > 0


TICKET_STATUSES = ["Open", "In Progress", "Resolved", "Closed"]


def list_tickets_for_user(user_id: str, limit: int = 20) -> list:
    """Used by get_tickets — the local dashboard DB is the queryable
    source of truth for a visitor's own ticket history, not a live Zoho
    Desk search (which would need a contact/lookup this app doesn't have)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            )
            return [_to_dict(r) for r in cur.fetchall()]


def list_tickets(limit: int = 50, offset: int = 0, status: str = None,
                  category: str = None, date_from: str = None, date_to: str = None,
                  assigned_admin_id=None, language: str = None) -> list:
    query = (
        "SELECT t.*, au.email AS assigned_admin_email FROM tickets t "
        "LEFT JOIN admin_users au ON au.id = t.assigned_admin_id WHERE 1=1"
    )
    params = []
    if status:
        query += " AND t.status = %s"
        params.append(status)
    if category:
        query += " AND t.category = %s"
        params.append(category)
    if language:
        query += " AND t.language = %s"
        params.append(language)
    if date_from:
        query += " AND t.created_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND t.created_at <= %s"
        params.append(date_to + "T23:59:59")
    if assigned_admin_id == 'unassigned':
        query += " AND t.assigned_admin_id IS NULL"
    elif assigned_admin_id:
        query += " AND t.assigned_admin_id = %s"
        params.append(assigned_admin_id)
    query += " ORDER BY t.created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [_to_dict(r) for r in cur.fetchall()]


def count_tickets(status: str = None, category: str = None, date_from: str = None, date_to: str = None,
                   assigned_admin_id=None, language: str = None) -> int:
    query = "SELECT COUNT(*) AS n FROM tickets t WHERE 1=1"
    params = []
    if status:
        query += " AND t.status = %s"
        params.append(status)
    if category:
        query += " AND t.category = %s"
        params.append(category)
    if language:
        query += " AND t.language = %s"
        params.append(language)
    if assigned_admin_id == 'unassigned':
        query += " AND t.assigned_admin_id IS NULL"
    elif assigned_admin_id:
        query += " AND t.assigned_admin_id = %s"
        params.append(assigned_admin_id)
    if date_from:
        query += " AND created_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND created_at <= %s"
        params.append(date_to + "T23:59:59")
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()['n']


def get_ticket(ticket_id: int) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.*, au.email AS assigned_admin_email FROM tickets t "
                "LEFT JOIN admin_users au ON au.id = t.assigned_admin_id WHERE t.id = %s",
                (ticket_id,),
            )
            ticket = cur.fetchone()
            if not ticket:
                return None
            cur.execute(
                "SELECT * FROM ticket_status_history WHERE ticket_id = %s ORDER BY id ASC", (ticket_id,)
            )
            history = cur.fetchall()
        result = _to_dict(ticket)
        result['history'] = [_to_dict(h) for h in history]
        return result


def update_ticket_status(ticket_id: int, status: str, note: str = None) -> bool:
    now = _now()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE tickets SET status = %s, resolved_at = COALESCE(%s, resolved_at) WHERE id = %s",
                    (status, now if status in ("Resolved", "Closed") else None, ticket_id),
                )
                cur.execute(
                    "INSERT INTO ticket_status_history (ticket_id, status, note, changed_at) VALUES (%s, %s, %s, %s)",
                    (ticket_id, status, note, now),
                )
                return True
    except psycopg2.Error:
        logger.warning("update_ticket_status failed for ticket %s", ticket_id, exc_info=True)
        return False


def update_ticket_fields(ticket_id: int, category: str = None, sub_category: str = None) -> bool:
    """Category/sub-category as corrected by a Zoho agent (see #3's
    webhook sync — app.py's /webhooks/zoho) — only touches fields that
    were actually provided, same COALESCE pattern as update_ticket_status's
    resolved_at."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE tickets SET
                       category = COALESCE(%s, category),
                       sub_category = COALESCE(%s, sub_category)
                   WHERE id = %s""",
                (category, sub_category, ticket_id),
            )
            return cur.rowcount > 0


def get_ticket_by_zoho_id(zoho_ticket_id: str) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM tickets WHERE zoho_ticket_id = %s", (zoho_ticket_id,))
            return _to_dict(cur.fetchone())


def get_latest_resolved_ticket(user_id: str) -> dict:
    """Most recently Resolved/Closed ticket for this visitor — what #5's
    72-hour reopen window checks against. None if they have no
    resolved/closed ticket at all (nothing to reopen)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM tickets WHERE user_id = %s AND status IN ('Resolved', 'Closed')
                   ORDER BY resolved_at DESC NULLS LAST LIMIT 1""",
                (user_id,),
            )
            return _to_dict(cur.fetchone())


# --- Agent messages (Zoho -> visitor chat sync, #3) -----------------------

def record_agent_message(session_id: str, text: str, author_name: str = None) -> None:
    """A real Zoho agent's reply, synced in via the webhook — same
    `messages` row shape as a bot turn, role='agent' so the visitor's
    chat (polling /conversations/<session_id>/agent-messages) and the
    admin transcript view can both tell it apart from the bot's own
    replies. author_name is folded into the text itself (e.g. 'Priya: ...')
    rather than a new column — one extra field for a cosmetic label isn't
    worth a schema change, and it reads naturally either way."""
    now = _now()
    display_text = f"{author_name}: {text}" if author_name else text
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO messages (session_id, role, text, source, card_shown, created_at)
                       VALUES (%s, 'agent', %s, 'zoho_agent', FALSE, %s)""",
                    (session_id, display_text, now),
                )
    except psycopg2.Error:
        logger.warning("record_agent_message failed for session %s", session_id, exc_info=True)


def get_new_agent_messages(session_id: str, since: str) -> list:
    """Polled by the visitor's own chat widget (app.py's
    /conversations/<session_id>/agent-messages) — only ever returns rows
    for the session_id the caller already has (that's the same trust
    model the rest of this app's visitor-facing routes use; there's no
    stronger per-visitor auth to check against). `since` is best-effort:
    a bad/missing value just returns everything rather than erroring, so
    a client with no prior checkpoint still gets the full backlog once."""
    query = "SELECT * FROM messages WHERE session_id = %s AND role = 'agent'"
    params = [session_id]
    if since:
        query += " AND created_at > %s"
        params.append(since)
    query += " ORDER BY created_at ASC"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [_to_dict(r) for r in cur.fetchall()]


# --- Coin credit at-most-once gate ---------------------------------------

def reserve_coin_credit_attempt(idempotency_key: str, user_id: str, booking_id: str,
                                 amount: int, status: str, purpose: str = None) -> bool:
    """Returns True if THIS call won the insert race and may fire the real
    credit — False if an attempt with this idempotency_key already exists
    (a concurrent or repeated credit_coins call), which must NOT fire.
    Fails closed on a DB error: refusing to fire is always the safe choice
    here, since firing twice against a no-idempotency API is the exact
    failure mode this table exists to prevent. `purpose` is the caller's
    own reason string (e.g. "refund_v1", "retention") — stored so
    count_successful_credits_today() can scope its count to one purpose
    at a time, rather than lumping every kind of credit into one cap."""
    now = _now()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO coin_credit_attempts
                           (idempotency_key, user_id, booking_id, amount, purpose, status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO NOTHING""",
                    (idempotency_key, user_id, booking_id, amount, purpose, status, now, now),
                )
                return cur.rowcount > 0
    except psycopg2.Error:
        logger.warning("reserve_coin_credit_attempt failed for key %s", idempotency_key, exc_info=True)
        return False


def count_successful_credits_today(user_id: str, purpose: str) -> int:
    """How many successful (or stub-credited, in mock mode) credits of
    this purpose this user already has today (server-local calendar day)
    — the daily-cap check for services/refund_service.py's
    decide_v1_refund(). Fails closed: on a DB error, returns a value
    guaranteed to be >= any real cap, so the caller denies rather than
    risks over-crediting when it can't actually check."""
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COUNT(*) AS n FROM coin_credit_attempts
                       WHERE user_id = %s AND purpose = %s
                         AND status IN ('success', 'stub_credited')
                         AND created_at >= date_trunc('day', now())""",
                    (user_id, purpose),
                )
                return cur.fetchone()['n']
    except psycopg2.Error:
        logger.warning("count_successful_credits_today failed for user %s", user_id, exc_info=True)
        return 10**9


def finalize_coin_credit_attempt(idempotency_key: str, status: str, http_status: int = None,
                                  response: str = None, error: str = None) -> None:
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE coin_credit_attempts
                       SET status = %s, http_status = %s, response = %s, error = %s, updated_at = %s
                       WHERE idempotency_key = %s""",
                    (
                        status, http_status,
                        json.dumps({"body": response[:2000]}) if response else None,
                        error, _now(), idempotency_key,
                    ),
                )
    except psycopg2.Error:
        logger.warning("finalize_coin_credit_attempt failed for key %s", idempotency_key, exc_info=True)


# --- Analytics -----------------------------------------------------------

def _week_month_trend(cur, table: str, date_col: str, resolved_col: str) -> dict:
    """Shared helper: {'weekly': [...], 'monthly': [...]} of {bucket, raised,
    resolved} rows for either tickets or conversations, bucketed by ISO
    year-week / year-month of their creation timestamp."""
    cur.execute(
        f"""SELECT to_char({date_col}, 'IYYY-"W"IW') AS bucket,
                   COUNT(*) AS raised,
                   SUM(CASE WHEN {resolved_col} IS NOT NULL THEN 1 ELSE 0 END) AS resolved
            FROM {table} GROUP BY bucket ORDER BY bucket DESC LIMIT 8"""
    )
    weekly = cur.fetchall()
    cur.execute(
        f"""SELECT to_char({date_col}, 'YYYY-MM') AS bucket,
                   COUNT(*) AS raised,
                   SUM(CASE WHEN {resolved_col} IS NOT NULL THEN 1 ELSE 0 END) AS resolved
            FROM {table} GROUP BY bucket ORDER BY bucket DESC LIMIT 6"""
    )
    monthly = cur.fetchall()
    return {
        'weekly': [dict(r) for r in reversed(weekly)],
        'monthly': [dict(r) for r in reversed(monthly)],
    }


def _period_over_period(cur, table: str, date_col: str, extra_where: str = "", params: list = None) -> dict:
    """today/yesterday, this-week/last-week (Postgres date_trunc('week',
    ...) is ISO-week, Monday start), and this-month/last-month counts +
    % change for `table` — the Analytics page's D0D/W0W/M0M numbers.
    % change is None when the prior period is 0 (nothing to divide by,
    and "infinite% up" is a misleading thing to show) rather than
    raising or faking a number."""
    params = list(params or [])
    where = f"WHERE {extra_where}" if extra_where else ""
    cur.execute(
        f"""SELECT
                SUM(CASE WHEN {date_col} >= date_trunc('day', now()) THEN 1 ELSE 0 END) AS today,
                SUM(CASE WHEN {date_col} >= date_trunc('day', now()) - interval '1 day'
                          AND {date_col} < date_trunc('day', now()) THEN 1 ELSE 0 END) AS yesterday,
                SUM(CASE WHEN {date_col} >= date_trunc('week', now()) THEN 1 ELSE 0 END) AS this_week,
                SUM(CASE WHEN {date_col} >= date_trunc('week', now()) - interval '7 days'
                          AND {date_col} < date_trunc('week', now()) THEN 1 ELSE 0 END) AS last_week,
                SUM(CASE WHEN {date_col} >= date_trunc('month', now()) THEN 1 ELSE 0 END) AS this_month,
                SUM(CASE WHEN {date_col} >= date_trunc('month', now()) - interval '1 month'
                          AND {date_col} < date_trunc('month', now()) THEN 1 ELSE 0 END) AS last_month
            FROM {table} {where}""",
        params,
    )
    row = cur.fetchone()

    def pct(curr, prev):
        curr, prev = curr or 0, prev or 0
        return round(100 * (curr - prev) / prev, 1) if prev else None

    return {
        'today': row['today'] or 0, 'yesterday': row['yesterday'] or 0,
        'dod_pct': pct(row['today'], row['yesterday']),
        'this_week': row['this_week'] or 0, 'last_week': row['last_week'] or 0,
        'wow_pct': pct(row['this_week'], row['last_week']),
        'this_month': row['this_month'] or 0, 'last_month': row['last_month'] or 0,
        'mom_pct': pct(row['this_month'], row['last_month']),
    }


def get_event_analytics() -> dict:
    """Per-event-type totals (all-time) plus D0D/W0W/M0M trend numbers for
    total events and for each event_type actually seen — read by the
    admin Analytics page's Events section. Deliberately NOT scoped to the
    page's date-range filter like get_analytics()'s other sections: D0D/
    W0W/M0M is inherently "vs right now", not a fixed window someone
    picked."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type ORDER BY n DESC"
            )
            totals = cur.fetchall()

            overall = _period_over_period(cur, 'events', 'created_at')

            by_type = {}
            for row in totals:
                event_type = row['event_type']
                by_type[event_type] = {
                    'total': row['n'],
                    **_period_over_period(cur, 'events', 'created_at', 'event_type = %s', [event_type]),
                }

    return {
        'event_totals': [{'event_type': r['event_type'], 'count': r['n']} for r in totals],
        'overall': overall,
        'by_type': by_type,
    }


def get_analytics(date_from: str = None, date_to: str = None) -> dict:
    """Aggregate KPIs the admin dashboard's Analytics page reads —
    conversation/turn counts, source breakdown, language breakdown,
    tool-call frequency/success rate, CSAT, bot-vs-human resolution, and
    week/month trend charts for both conversations and tickets."""
    clause = "WHERE 1=1"
    params = []
    if date_from:
        clause += " AND created_at >= %s"
        params.append(date_from)
    if date_to:
        clause += " AND created_at <= %s"
        params.append(date_to + "T23:59:59")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(DISTINCT session_id) AS n FROM messages {clause}", params)
            total_conversations = cur.fetchone()['n']

            cur.execute(f"SELECT COUNT(*) AS n FROM messages {clause} AND role = 'bot'", params)
            total_turns = cur.fetchone()['n']

            cur.execute(
                f"""SELECT source, COUNT(*) as n FROM messages {clause} AND role = 'bot'
                    GROUP BY source ORDER BY n DESC""", params
            )
            source_rows = cur.fetchall()

            cur.execute(
                f"""SELECT last_language, COUNT(*) as n FROM conversations
                    WHERE session_id IN (SELECT DISTINCT session_id FROM messages {clause})
                    GROUP BY last_language ORDER BY n DESC""", params
            )
            language_rows = cur.fetchall()

            cur.execute(
                f"SELECT COUNT(*) AS n FROM messages {clause} AND role = 'bot' AND card_shown = TRUE", params
            )
            cards_shown = cur.fetchone()['n']

            cur.execute(
                f"SELECT tool_trace FROM messages {clause} AND role = 'bot' AND tool_trace IS NOT NULL", params
            )
            trace_rows = cur.fetchall()

            cur.execute(
                """SELECT resolved_by, COUNT(*) as n FROM conversations
                   WHERE resolved_by IS NOT NULL GROUP BY resolved_by"""
            )
            resolved_by = {r['resolved_by']: r['n'] for r in cur.fetchall()}

            cur.execute(
                "SELECT AVG(rating) as avg_rating, COUNT(rating) as n_rated FROM conversations WHERE rating IS NOT NULL"
            )
            rating_row = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS n FROM conversations")
            total_convos_all_time = cur.fetchone()['n']

            conversations_period = _period_over_period(cur, 'conversations', 'first_seen_at')

            cur.execute(
                "SELECT category, COUNT(*) as n FROM tickets GROUP BY category ORDER BY n DESC LIMIT 8"
            )
            category_rows = cur.fetchall()

            conversation_trend = _week_month_trend(cur, 'conversations', 'first_seen_at', 'resolved_at')
            ticket_trend = _week_month_trend(cur, 'tickets', 'created_at', 'resolved_at')

            cur.execute("SELECT COUNT(*) AS n FROM tickets WHERE status NOT IN ('Resolved', 'Closed')")
            open_tickets = cur.fetchone()['n']

            cur.execute("SELECT COUNT(*) AS n FROM tickets")
            total_tickets_all_time = cur.fetchone()['n']

    tool_stats = {}
    for row in trace_rows:
        calls = row['tool_trace'] or []
        for call in calls:
            name = call.get('tool')
            if not name:
                continue
            stat = tool_stats.setdefault(name, {'calls': 0, 'ok': 0})
            stat['calls'] += 1
            if call.get('ok'):
                stat['ok'] += 1

    pct_rated = round(100 * rating_row['n_rated'] / total_convos_all_time, 1) if total_convos_all_time else 0.0

    return {
        'total_conversations': total_conversations,
        'total_turns': total_turns,
        'cards_shown': cards_shown,
        'source_breakdown': [{'source': r['source'] or 'unknown', 'count': r['n']} for r in source_rows],
        'language_breakdown': [{'language': r['last_language'] or 'unknown', 'count': r['n']} for r in language_rows],
        'tool_stats': sorted(
            [{'tool': name, **stat} for name, stat in tool_stats.items()],
            key=lambda x: x['calls'], reverse=True,
        ),
        'resolved_by_bot': resolved_by.get('bot', 0),
        'resolved_by_escalation': resolved_by.get('escalated', 0),
        'avg_rating': round(rating_row['avg_rating'], 2) if rating_row['avg_rating'] is not None else None,
        'rated_count': rating_row['n_rated'],
        'pct_rated': pct_rated,
        'ticket_categories': [{'category': r['category'], 'count': r['n']} for r in category_rows],
        'conversation_trend': conversation_trend,
        'ticket_trend': ticket_trend,
        'open_tickets': open_tickets,
        'total_tickets': total_tickets_all_time,
        'conversations_period': conversations_period,
    }


# --- Admin users (email/password + RBAC) ---------------------------------

def create_admin_user(email: str, password_hash: str, role: str, languages: list = None) -> dict:
    """Returns the new row, or None if that email is already taken (a
    UNIQUE-violation, not an exception — callers show a friendly 'already
    exists' message instead of a 500). languages is a subset of
    LANGUAGE_CODES' keys (e.g. ['hi', 'ta']) — which tickets round-robin
    routes to this person; empty/omitted means no language preference,
    not "handles nothing" (see _pick_round_robin_assignee)."""
    email = email.strip().lower()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO admin_users (email, password_hash, role, languages, created_at)
                       VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                    (email, password_hash, role, languages or [], _now()),
                )
                return _to_dict(cur.fetchone())
    except psycopg2.errors.UniqueViolation:
        return None


def get_admin_user_by_email(email: str) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_users WHERE email = %s", (email.strip().lower(),))
            return _to_dict(cur.fetchone())


def get_admin_user_by_id(user_id: int) -> dict:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_users WHERE id = %s", (user_id,))
            return _to_dict(cur.fetchone())


def list_admin_users() -> list:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM admin_users ORDER BY created_at ASC")
            return [_to_dict(r) for r in cur.fetchall()]


def list_assignable_admins() -> list:
    """Who a ticket can actually be assigned to — the same eligibility
    _pick_round_robin_assignee uses, for the tickets filter dropdown and
    the ticket detail reassignment form."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM admin_users WHERE role IN ('admin', 'support_agent') ORDER BY email ASC"
            )
            return [_to_dict(r) for r in cur.fetchall()]


def count_admin_users(role: str = None) -> int:
    query = "SELECT COUNT(*) AS n FROM admin_users"
    params = []
    if role:
        query += " WHERE role = %s"
        params.append(role)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()['n']


def update_admin_user_role(user_id: int, role: str) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE admin_users SET role = %s WHERE id = %s", (role, user_id))
            return cur.rowcount > 0


def update_admin_user_languages(user_id: int, languages: list) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE admin_users SET languages = %s WHERE id = %s", (languages or [], user_id))
            return cur.rowcount > 0


def delete_admin_user(user_id: int) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM admin_users WHERE id = %s", (user_id,))
            return cur.rowcount > 0


# --- Cross-process shared config ------------------------------------------

def get_or_create_session_secret() -> str:
    """A Flask secret_key that's identical across every worker/pod without
    needing an env var set anywhere — generated once (32 random bytes,
    hex-encoded) and persisted here, so it survives restarts/redeploys and
    is the same value everywhere Postgres is. Without this, each process
    fell back to its own random key, which broke the admin session cookie
    the moment a request landed on a different worker than the one that
    signed it (looked like a random logout on every click)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM dashboard_config WHERE key = 'session_secret'")
            row = cur.fetchone()
            if row:
                return row['value']
            cur.execute(
                """INSERT INTO dashboard_config (key, value) VALUES ('session_secret', %s)
                   ON CONFLICT (key) DO NOTHING""",
                (secrets.token_hex(32),),
            )
    # Re-read rather than trust the value just generated — a concurrent
    # worker doing this same race at startup may have won the insert.
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM dashboard_config WHERE key = 'session_secret'")
            return cur.fetchone()['value']
