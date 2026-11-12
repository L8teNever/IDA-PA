"""Tippfehler-Vorschläge der Chats-App per Claude Haiku. Der Dienst "chats" fragt hier (intern) und fällt bei Fehler/Limit auf das lokale
T5-Modell zurück. Einstellung "korrektur_engine": haiku (Standard), t5 oder aus – die jeweils andere Engine springt ein."""

import re

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
CTX = (" Im <verlauf> stehen die letzten Nachrichten des Chats nur als Kontext, damit du verstehst, was gemeint ist. Korrigiere allein den "
       "Text in <nachricht> und gib nur diesen aus, ohne Tags.")


class Ctx(BaseModel):
    who: str = Field(default="", max_length=80)
    text: str = Field(default="", max_length=1000)


class Text(BaseModel):
    text: str = Field(max_length=8000)
    context: list[Ctx] = Field(default_factory=list, max_length=20)


class Engine(BaseModel):
    engine: str | None = None
    vorfilter: bool | None = None      # erst ein schneller Wörterbuch-Check, Haiku nur wenn ein Wort unbekannt ist
    kontext: bool | None = None        # die letzten Nachrichten des Chats gehen als Kontext mit
    kontext_n: int | None = Field(default=None, ge=1, le=20)


async def engine(db) -> str:
    v = await settings_store.get(db, KEY, "haiku")
    return v if v in ENGINES else "haiku"


async def options(db) -> dict:
    n = await settings_store.get(db, "korrektur_kontext_n", 5)
    return {"engine": await engine(db), "vorfilter": bool(await settings_store.get(db, "korrektur_vorfilter", True)),
            "kontext": bool(await settings_store.get(db, "korrektur_kontext", True)),
            "kontext_n": n if isinstance(n, int) and 1 <= n <= 20 else 5}


@router.get("/settings")
async def get_settings_(db: DB):
    return {**await options(db), "engines": [{"id": k, "name": v} for k, v in ENGINES.items()], "model": get_settings().cheap_model}


@router.patch("/settings")
async def set_settings(body: Engine, db: DB):
    if body.engine is not None:
        if body.engine not in ENGINES:
            raise Invalid("Unbekannte Engine")
        await settings_store.put(db, KEY, body.engine)
    for k in ("vorfilter", "kontext", "kontext_n"):
        if getattr(body, k) is not None:
            await settings_store.put(db, f"korrektur_{k}", getattr(body, k))
    return {"ok": True, **await options(db)}


@router.post("/haiku")
async def haiku(body: Text):
    """{ok, text, account} – bei Limit probiert er den nächsten Claude-Account; ok=false, wenn keiner kann (dann nimmt chats das T5-Modell)."""
    text = body.text.strip()
    if not text:
        return {"ok": False}
    prompt = text
    if body.context:   # Kontext nur zum Verstehen, korrigiert wird allein die neue Nachricht
        lines = "\n".join(f"{c.who or '?'}: {c.text}" for c in body.context if c.text.strip())
        prompt = f"<verlauf>\n{lines}\n</verlauf>\n<nachricht>\n{text}\n</nachricht>"
    model = get_settings().cheap_model
    async with session_scope() as db:
        accs = [a for a in await providers.accounts_for(db, model) if providers.logged_in(a)]
    for acc in accs:
        if await providers.exhausted(acc):
            continue
        out = await runner.run(RunSpec(model=model, account=acc, prompt=prompt, system=SYSTEM + (CTX if body.context else ""), effort="low"))
        if out.ok and out.text.strip():
            new = re.sub(r"</?nachricht>", "", out.text).strip()
            return {"ok": True, "text": new, "account": acc.name}
        if not out.limit_hit:
            break
    return {"ok": False}
