"""IDA Korrektur: korrigiert Tippfehler in Chat-Nachrichten mit einem lokalen T5-Modell (docs/KORREKTUR.md).
Nur im Stack erreichbar (kein Port nach außen); IDA Chats fragt hier, sobald du kurz nicht tippst."""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import model
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.autoload:
        threading.Thread(target=model.load, daemon=True).start()
    yield


class Req(BaseModel):
    text: str = Field(max_length=8000)


def create_app(autoload: bool = True) -> FastAPI:
    app = FastAPI(title="IDA Korrektur", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.autoload = autoload

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True, **model.status()}

    @app.post("/api/v1/korrigieren")
    async def korrigieren(body: Req):
        text = body.text
        if not text.strip() or len(text) > get_settings().korrektur_max_chars:
            return {"text": text, "parts": [{"t": text, "neu": False}], "changed": False}
        if model.status()["status"] != "bereit":
            raise HTTPException(503, f"Modell {model.status()['status']}")
        return await asyncio.to_thread(model.correct, text)

    return app


app = create_app()
