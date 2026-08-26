import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RULES_DIR = DATA_DIR / "knowledge" / "rules"
BOOKS_DIR = DATA_DIR / "knowledge" / "books"
OPENAPI_DIR = DATA_DIR / "openapi"
OPENAPI_PATH = OPENAPI_DIR / "stock_api.json"
SQLITE_PATH = DATA_DIR / "app.db"

# Company-side key: used ONLY by the MCP server transport itself if it ever
# needs to call OpenAI directly (it normally doesn't - MCP just serves data).
# The webapp NEVER uses this; each web user supplies their own key (BYOK).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4o").strip()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8100"))


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RULES_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
