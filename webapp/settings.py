import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

# Symmetric key used ONLY to encrypt/decrypt user-supplied OpenAI keys at
# rest in this app's own sqlite db. Must be set via env in production - a
# fresh random key is generated for local dev convenience so the app still
# boots, but that means BYOK keys saved before a restart become
# unreadable after an unset-env restart (by design: never fall back to a
# guessable/shared secret for something this sensitive).
_env_key = os.getenv("BYOK_ENCRYPTION_KEY", "").strip()
BYOK_ENCRYPTION_KEY: bytes = _env_key.encode("utf-8") if _env_key else Fernet.generate_key()

DEFAULT_CHAT_MODEL = os.getenv("DEFAULT_CHAT_MODEL", "gpt-4o").strip()
DEFAULT_ANTHROPIC_MODEL = os.getenv("DEFAULT_ANTHROPIC_MODEL", "claude-sonnet-5").strip()
MAX_TOOL_LOOPS = int(os.getenv("MAX_TOOL_LOOPS", "10"))
ANTHROPIC_MAX_TOKENS = int(os.getenv("ANTHROPIC_MAX_TOKENS", "2048"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
