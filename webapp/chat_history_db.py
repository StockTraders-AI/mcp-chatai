"""
Server-side conversation history for the webapp chat, keyed by a
`session_id` the client provides (today: the frontend's existing
`conversation_id` field - currently a hardcoded shared value, but the
exact same field the frontend would just need to make unique-per-browser
to get real per-user history for free, no new API field required).

Deliberately separate from core/local_db.py (that's the 8-API cache,
totally different data/lifetime). Same sqlite3-builtin pattern though -
no new dependency.

Not a general-purpose chat log: only ever reads/writes the last N turns
per session, and rows are pruned on every write so a session can't grow
unbounded. If no session_id is given, callers get no history and nothing
is stored - the webapp stays exactly as stateless as it always was for
any caller that doesn't opt in.
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "chat_history.db"

MAX_TURNS_PER_SESSION = 40  # hard cap kept on disk; only the last few are ever read back

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def get_recent_messages(session_id: str, limit: int = 8) -> List[Dict[str, str]]:
    """Returns the last `limit` messages for this session, oldest first,
    as [{"role": "user"|"assistant", "text": ...}]. Empty list if the
    session_id is falsy or has no history yet."""
    if not session_id:
        return []
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT role, text FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    finally:
        conn.close()
    return [{"role": r["role"], "text": r["text"]} for r in reversed(rows)]


def append_turn(session_id: str, user_text: str, assistant_text: str) -> None:
    """Stores the just-completed exchange, then prunes anything older than
    MAX_TURNS_PER_SESSION rows for this session so it can never grow
    unbounded. No-op if session_id is falsy (caller didn't opt in)."""
    if not session_id:
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, text) VALUES (?, 'user', ?)",
            (session_id, user_text),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, text) VALUES (?, 'assistant', ?)",
            (session_id, assistant_text),
        )
        conn.execute(
            """
            DELETE FROM chat_messages
            WHERE session_id = ? AND id NOT IN (
                SELECT id FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (session_id, session_id, MAX_TURNS_PER_SESSION),
        )
        conn.commit()
    finally:
        conn.close()


def clear_session(session_id: str) -> None:
    if not session_id:
        return
    conn = _connect()
    try:
        conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()
