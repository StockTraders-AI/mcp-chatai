"""
Bring-Your-Own-Key storage: each web user supplies their own OpenAI API key.
The webapp never falls back to a company-wide key - if a user has no key
saved, chat requests fail with a clear "set your API key first" error
rather than silently spending StockTraders' money.

Keys are encrypted at rest with Fernet (symmetric encryption) using a
server-side secret (BYOK_ENCRYPTION_KEY) that only decrypts requests
in-process - the key is never written to logs.
"""

import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from core.settings import SQLITE_PATH, BASE_DIR
from webapp.settings import BYOK_ENCRYPTION_KEY

_SCHEMA = """
CREATE TABLE IF NOT EXISTS byok_keys (
    user_id TEXT PRIMARY KEY,
    encrypted_openai_key BLOB NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

_fernet = Fernet(BYOK_ENCRYPTION_KEY)


def ensure_table():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH) as db:
        db.executescript(_SCHEMA)


def save_key(user_id: str, openai_api_key: str):
    if not openai_api_key or not openai_api_key.strip():
        raise ValueError("openai_api_key is empty")
    ensure_table()
    encrypted = _fernet.encrypt(openai_api_key.strip().encode("utf-8"))
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute(
            """
            INSERT INTO byok_keys (user_id, encrypted_openai_key, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                encrypted_openai_key = excluded.encrypted_openai_key,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, encrypted),
        )


def get_key(user_id: str) -> str | None:
    ensure_table()
    with sqlite3.connect(SQLITE_PATH) as db:
        row = db.execute(
            "SELECT encrypted_openai_key FROM byok_keys WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return _fernet.decrypt(row[0]).decode("utf-8")
    except InvalidToken:
        return None


def delete_key(user_id: str):
    ensure_table()
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute("DELETE FROM byok_keys WHERE user_id = ?", (user_id,))


def has_key(user_id: str) -> bool:
    return get_key(user_id) is not None
