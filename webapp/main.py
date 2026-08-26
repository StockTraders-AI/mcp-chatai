import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from webapp.byok import save_key, has_key, delete_key
from webapp.chat_service import chat, MissingApiKeyError

app = FastAPI(title="StockTraders AI - BYOK web chat")


class SaveKeyRequest(BaseModel):
    user_id: str
    openai_api_key: str


class ChatRequest(BaseModel):
    user_id: str
    message: str
    model: str | None = None


@app.post("/auth/key")
def set_key(req: SaveKeyRequest):
    save_key(req.user_id, req.openai_api_key)
    return {"ok": True}


@app.get("/auth/key/status")
def key_status(user_id: str):
    return {"has_key": has_key(user_id)}


@app.delete("/auth/key")
def remove_key(user_id: str):
    delete_key(user_id)
    return {"ok": True}


@app.post("/chat")
def chat_endpoint(req: ChatRequest):
    try:
        answer = chat(req.user_id, req.message, req.model)
    except MissingApiKeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"answer": answer}


@app.get("/health")
def health():
    return {"status": "ok"}
