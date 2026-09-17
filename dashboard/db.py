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

`tool_trace` is stored as JSONB (list of {"tool": str, "ok": bool} dicts,
exactly ctx.trace's shape) — read back for display, never queried into.
"""
import json
import logging
import os
import re
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

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_last_seen ON conversations(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
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
]


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

def record_ticket(ticket_ref: str, session_id: str, user_id: str, category: str,
                   sub_category: str, description: str, evidence_url: str, ltv_tier: str,
                   zoho_ticket_id: str = None) -> None:
    now = _now()
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO tickets (ticket_ref, session_id, user_id, category, sub_category,
                                             description, evidence_url, ltv_tier, status, created_at, zoho_ticket_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Open', %s, %s)
                       RETURNING id""",
                    (ticket_ref, session_id, user_id, category, sub_category, description, evidence_url, ltv_tier, now, zoho_ticket_id),
                )
                ticket_id = cur.fetchone()['id']
                cur.execute(
                    "INSERT INTO ticket_status_history (ticket_id, status, note, changed_at) VALUES (%s, 'Open', 'Ticket created', %s)",
                    (ticket_id, now),
                )
    except psycopg2.Error:
        logger.warning("record_ticket failed for ticket %s", ticket_ref, exc_info=True)


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
                  category: str = None, date_from: str = None, date_to: str = None) -> list:
    query = "SELECT * FROM tickets WHERE 1=1"
    params = []
    if status:
        query += " AND status = %s"
        params.append(status)
    if category:
        query += " AND category = %s"
        params.append(category)
    if date_from:
        query += " AND created_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND created_at <= %s"
        params.append(date_to + "T23:59:59")
    query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    params.extend([limit, offset])
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return [_to_dict(r) for r in cur.fetchall()]


def count_tickets(status: str = None, category: str = None, date_from: str = None, date_to: str = None) -> int:
    query = "SELECT COUNT(*) AS n FROM tickets WHERE 1=1"
    params = []
    if status:
        query += " AND status = %s"
        params.append(status)
    if category:
        query += " AND category = %s"
        params.append(category)
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
            cur.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
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
    }
