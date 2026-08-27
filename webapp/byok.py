"""
Bring-Your-Own-Key storage: each web user supplies their own API key for
whichever provider they chat with (OpenAI or Anthropic/Claude). The webapp
never falls back to a company-wide key - if a user has no key saved for the
provider they picked, chat requests fail with a clear "set your API key
first" error rather than silently spending StockTraders' money.

Keys are encrypted at rest with Fernet (symmetric encryption) using a
server-side secret (BYOK_ENCRYPTION_KEY) that only decrypts requests
in-process - the key is never written to logs.

One user can hold a key per provider at once (e.g. both an OpenAI key and
an Anthropic key), keyed on (user_id, provider).
"""

import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from core.settings import SQLITE_PATH, BASE_DIR
from webapp.settings import BYOK_ENCRYPTION_KEY

PROVIDERS = ("openai", "anthropic")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS byok_keys (
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    encrypted_key BLOB NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, provider)
);
"""

_fernet = Fernet(BYOK_ENCRYPTION_KEY)


def _check_provider(provider: str):
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}', expected one of {PROVIDERS}")


def ensure_table():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SQLITE_PATH) as db:
        db.executescript(_SCHEMA)


def save_key(user_id: str, api_key: str, provider: str = "openai"):
    _check_provider(provider)
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("api_key is empty")
    if not api_key.isascii():
        # Real OpenAI/Anthropic keys are always pure ASCII. A non-ASCII
        # character here is a copy-paste artifact (smart dash, stray
        # whitespace variant, etc.) that silently corrupts the key - it
        # decrypts fine later (Fernet doesn't know it's "wrong"), but then
        # crashes deep inside the SDK's HTTP header encoding at chat time
        # with a cryptic UnicodeEncodeError instead of a clear message here.
        raise ValueError(
            "api_key chua ky tu khong phai ASCII (co the do copy-paste dinh ky tu la) - "
            "kiem tra lai va dan lai key."
        )
    ensure_table()
    encrypted = _fernet.encrypt(api_key.encode("utf-8"))
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute(
            """
            INSERT INTO byok_keys (user_id, provider, encrypted_key, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                encrypted_key = excluded.encrypted_key,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, provider, encrypted),
        )


def get_key(user_id: str, provider: str = "openai") -> str | None:
    _check_provider(provider)
    ensure_table()
    with sqlite3.connect(SQLITE_PATH) as db:
        row = db.execute(
            "SELECT encrypted_key FROM byok_keys WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()
    if not row:
        return None
    try:
        return _fernet.decrypt(row[0]).decode("utf-8")
    except InvalidToken:
        return None


def delete_key(user_id: str, provider: str = "openai"):
    _check_provider(provider)
    ensure_table()
    with sqlite3.connect(SQLITE_PATH) as db:
        db.execute(
            "DELETE FROM byok_keys WHERE user_id = ? AND provider = ?", (user_id, provider)
        )


def has_key(user_id: str, provider: str = "openai") -> bool:
    return get_key(user_id, provider) is not None
