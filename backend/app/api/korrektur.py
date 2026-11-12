"""Tippfehler-Vorschläge der Chats-App per Claude Haiku. Der Dienst "chats" fragt hier (intern) und fällt bei Fehler/Limit auf das lokale
T5-Modell zurück. Einstellung "korrektur_engine": haiku (Standard), t5 oder aus – die jeweils andere Engine springt ein."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import get_settings
from ..db import session_scope
from ..services import providers, settings_store
from ..services.claude import runner
from ..services.claude.runner import RunSpec
from ..services.errors import Invalid
from .deps import DB

router = APIRouter(prefix="/api/v1/korrektur", tags=["korrektur"])
KEY = "korrektur_engine"
ENGINES = {"haiku": "Claude Haiku 5.5", "t5": "Lokal (T5-Modell)", "aus": "Aus"}
SYSTEM = ("Du korrigierst Tippfehler in einer Chat-Nachricht. Behebe nur Tippfehler, vertauschte oder fehlende Buchstaben und falsch erkannte "
          "Wörter (Spracheingabe). Lass Stil, Umgangssprache, Groß-/Kleinschreibung, Satzzeichen, Emojis, Links, Namen und englische Wörter "
          "unverändert. Nichts umformulieren, nichts hinzufügen, nichts weglassen. Antworte nur mit dem korrigierten Text.")


class Text(BaseModel):
    text: str = Field(max_length=8000)


class Engine(BaseModel):
    engine: str


async def engine(db) -> str:
    v = await settings_store.get(db, KEY, "haiku")
    return v if v in ENGINES else "haiku"


@router.get("/settings")
async def get_settings_(db: DB):
    return {"engine": await engine(db), "engines": [{"id": k, "name": v} for k, v in ENGINES.items()], "model": get_settings().cheap_model}


@router.patch("/settings")
async def set_settings(body: Engine, db: DB):
    if body.engine not in ENGINES:
        raise Invalid("Unbekannte Engine")
    await settings_store.put(db, KEY, body.engine)
    return {"ok": True, "engine": body.engine}


@router.post("/haiku")
async def haiku(body: Text):
    """{ok, text, account} – bei Limit probiert er den nächsten Claude-Account; ok=false, wenn keiner kann (dann nimmt chats das T5-Modell)."""
    text = body.text.strip()
    if not text:
        return {"ok": False}
    model = get_settings().cheap_model
    async with session_scope() as db:
        accs = [a for a in await providers.accounts_for(db, model) if providers.logged_in(a)]
    for acc in accs:
        if await providers.exhausted(acc):
            continue
        out = await runner.run(RunSpec(model=model, account=acc, prompt=text, system=SYSTEM, effort="low"))
        if out.ok and out.text.strip():
            return {"ok": True, "text": out.text.strip(), "account": acc.name}
        if not out.limit_hit:
            break
    return {"ok": False}
