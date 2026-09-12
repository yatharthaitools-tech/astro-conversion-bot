"""SQLite persistence for the admin dashboard — conversations, messages,
and per-turn tool-call traces.

This is a NEW store, separate from integrations/s3_client.py's existing
fire-and-forget event log (kept as-is, still feeds Redash). Nothing here
replaces that — this is the queryable local store the admin dashboard
actually reads from, mirroring what astrohelp's chat_sessions/
chat_messages tables do for its own admin dashboard, just with SQLite
instead of Postgres (this app's scale doesn't need more, and it's zero
setup — swap the connection layer for Postgres later if it ever does).

Schema:
    conversations(session_id PK, user_id, first_seen_at, last_seen_at,
                   turn_count, last_language)
    messages(id PK, session_id FK, role, text, source, tool_trace,
             card_shown, created_at)

`tool_trace` is stored as a JSON string (list of {"tool": str, "ok": bool}
dicts, exactly ctx.trace's shape) — SQLite has no native array/JSON type,
and this data is only ever read back for display, never queried on.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get('DASHBOARD_DB_PATH', 'dashboard.db')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    last_language TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES conversations(session_id),
    role TEXT NOT NULL CHECK (role IN ('user', 'bot')),
    text TEXT NOT NULL,
    source TEXT,
    tool_trace TEXT,
    card_shown INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_last_seen ON conversations(last_seen_at);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_turn(session_id: str, user_id: str, question: str, answer: str,
                language: str, source: str, tool_trace: list, card_shown: bool) -> None:
    """Called once per /ask turn — writes the user's question and the
    bot's answer as two message rows, and upserts the conversation's
    summary row. Best-effort: a logging failure must never break the
    visitor's chat reply, same posture as s3_client.log_event."""
    now = _now()
    trace_json = json.dumps(tool_trace or [])
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO conversations (session_id, user_id, first_seen_at, last_seen_at, turn_count, last_language)
                   VALUES (?, ?, ?, ?, 1, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       last_seen_at = excluded.last_seen_at,
                       turn_count = turn_count + 1,
                       last_language = excluded.last_language""",
                (session_id, user_id, now, now, language),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, text, created_at) VALUES (?, 'user', ?, ?)",
                (session_id, question, now),
            )
            conn.execute(
                """INSERT INTO messages (session_id, role, text, source, tool_trace, card_shown, created_at)
                   VALUES (?, 'bot', ?, ?, ?, ?, ?)""",
                (session_id, answer, source, trace_json, int(bool(card_shown)), now),
            )
    except sqlite3.Error:
        pass


def list_conversations(limit: int = 50, offset: int = 0, language: str = None,
                        date_from: str = None, date_to: str = None) -> list:
    query = "SELECT * FROM conversations WHERE 1=1"
    params = []
    if language:
        query += " AND last_language = ?"
        params.append(language)
    if date_from:
        query += " AND last_seen_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND last_seen_at <= ?"
        params.append(date_to + "T23:59:59")
    query += " ORDER BY last_seen_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def count_conversations(language: str = None, date_from: str = None, date_to: str = None) -> int:
    query = "SELECT COUNT(*) FROM conversations WHERE 1=1"
    params = []
    if language:
        query += " AND last_language = ?"
        params.append(language)
    if date_from:
        query += " AND last_seen_at >= ?"
        params.append(date_from)
    if date_to:
        query += " AND last_seen_at <= ?"
        params.append(date_to + "T23:59:59")
    with _connect() as conn:
        return conn.execute(query, params).fetchone()[0]


def get_conversation(session_id: str) -> dict:
    with _connect() as conn:
        conv = conn.execute(
            "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not conv:
            return None
        messages = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        ).fetchall()
        result = dict(conv)
        result['messages'] = []
        for m in messages:
            msg = dict(m)
            msg['tool_trace'] = json.loads(msg['tool_trace']) if msg['tool_trace'] else []
            result['messages'].append(msg)
        return result


def get_analytics(date_from: str = None, date_to: str = None) -> dict:
    """Aggregate KPIs the admin dashboard's Analytics page reads —
    conversation/turn counts, source breakdown, language breakdown, and
    tool-call frequency/success rate (parsed out of each bot message's
    stored tool_trace)."""
    clause = "WHERE 1=1"
    params = []
    if date_from:
        clause += " AND created_at >= ?"
        params.append(date_from)
    if date_to:
        clause += " AND created_at <= ?"
        params.append(date_to + "T23:59:59")

    with _connect() as conn:
        total_conversations = conn.execute(
            f"SELECT COUNT(DISTINCT session_id) FROM messages {clause}", params
        ).fetchone()[0]
        total_turns = conn.execute(
            f"SELECT COUNT(*) FROM messages {clause} AND role = 'bot'", params
        ).fetchone()[0]

        source_rows = conn.execute(
            f"""SELECT source, COUNT(*) as n FROM messages {clause} AND role = 'bot'
                GROUP BY source ORDER BY n DESC""", params
        ).fetchall()

        language_rows = conn.execute(
            f"""SELECT last_language, COUNT(*) as n FROM conversations
                WHERE session_id IN (SELECT DISTINCT session_id FROM messages {clause})
                GROUP BY last_language ORDER BY n DESC""", params
        ).fetchall()

        cards_shown = conn.execute(
            f"SELECT COUNT(*) FROM messages {clause} AND role = 'bot' AND card_shown = 1", params
        ).fetchone()[0]

        trace_rows = conn.execute(
            f"SELECT tool_trace FROM messages {clause} AND role = 'bot' AND tool_trace IS NOT NULL", params
        ).fetchall()

    tool_stats = {}
    for row in trace_rows:
        try:
            calls = json.loads(row['tool_trace'])
        except (TypeError, ValueError):
            continue
        for call in calls:
            name = call.get('tool')
            if not name:
                continue
            stat = tool_stats.setdefault(name, {'calls': 0, 'ok': 0})
            stat['calls'] += 1
            if call.get('ok'):
                stat['ok'] += 1

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
    }
