import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from webapp.byok import save_key, has_key, delete_key, PROVIDERS
from webapp.chat_service import chat, MissingApiKeyError

logger = logging.getLogger("stocktraders.webapp")

app = FastAPI(title="StockTraders AI - BYOK web chat")


class SaveKeyRequest(BaseModel):
    user_id: str
    api_key: str
    provider: str = "openai"


class ChatRequest(BaseModel):
    user_id: str
    message: str
    provider: str = "openai"
    model: str | None = None


@app.post("/auth/key")
def set_key(req: SaveKeyRequest):
    try:
        save_key(req.user_id, req.api_key, req.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.get("/auth/key/status")
def key_status(user_id: str, provider: str = "openai"):
    return {"has_key": has_key(user_id, provider)}


@app.delete("/auth/key")
def remove_key(user_id: str, provider: str = "openai"):
    delete_key(user_id, provider)
    return {"ok": True}


@app.get("/auth/providers")
def list_providers():
    return {"providers": list(PROVIDERS)}


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    try:
        result = chat(req.user_id, req.message, provider=req.provider, model=req.model)
    except MissingApiKeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        # UnicodeEncodeError/UnicodeError are ValueError subclasses in Python -
        # a real bug there was silently turning into a misleading 400 here
        # with no traceback logged, which is exactly what made this bug hard
        # to pin down. Only genuine validation ValueErrors (e.g. "Unknown
        # provider") should become a 400; anything Unicode-related is an
        # actual bug and must be logged with its full traceback and a 500.
        if isinstance(e, UnicodeError):
            logger.exception("chat() failed with a Unicode error")
            raise HTTPException(status_code=500, detail="Loi noi bo (unicode). Da ghi log server de tra cuu.")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("chat() failed unexpectedly")
        raise HTTPException(status_code=500, detail="Loi noi bo. Da ghi log server de tra cuu.")
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
