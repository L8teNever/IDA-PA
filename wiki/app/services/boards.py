"""Smart Boards: Ida holt sich zum Thema alles aus Chats, Wiki, Gedächtnis & Co. und liefert ein geordnetes Markdown-Board.

Jeder Lauf ist ein eigener, kurzlebiger Ida-Chat (wird danach gelöscht). „Neu laden“ gibt Ida den bisherigen Stand mit,
damit sie nur nach Neuem und Geändertem sucht und das Board anpasst.
"""
import asyncio
import json
import logging

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..errors import Invalid
from ..models import Board

log = logging.getLogger("wiki.boards")
_tasks: dict[str, asyncio.Task] = {}
MAX_CONTENT = 200_000

RULES = """Du baust ein „Smart Board“ für Simon: eine übersichtliche Seite, die alles Wichtige zu einem Thema bündelt.
Hole die Infos selbst mit deinen Werkzeugen: durchsuche deine früheren Chats (chat_search, chat_read), das Gedächtnis (memory_search), das Wiki (wiki_search, wiki_read) und, wenn es zum Thema passt, Mails, Kalender, Aufgaben und Sofia/Stundenplan. Erfinde nichts; was du nicht findest, lässt du weg.
Antworte AUSSCHLIESSLICH mit dem fertigen Board in Markdown, ohne Einleitung, ohne Schlusssatz, ohne Rückfragen und ohne Code-Zaun um das Ganze.
Wähle den Aufbau frei, so wie er zum Thema passt. Die Oberfläche macht aus jedem „## Überschrift“ automatisch eine eigene kleine Karte (wie Artikel in einer Zeitung, mehrere nebeneinander). Das ist nur eine Möglichkeit, kein Muss:
- Bei vielen verschiedenen Themen: kurze Abschnitte mit „## Überschrift“ (je höchstens etwa 8 Zeilen) sind praktisch.
- Bei einem einzigen zusammenhängenden Thema darfst du ohne Überschriften schreiben, oder nur wenige.
- Nimm die Form, die passt: Checkliste („- [ ] …“, erledigt „- [x] …“) für Aufgaben, Tabelle für Termine und Zahlen, Stichpunkte für Fakten, Absatz für Hinweise. Mische frei.
- Wichtiges und Fristen zuerst, mit Datum. Wo es hilft, nenne kurz die Quelle (z. B. „aus Chat vom 03.11.“).
- Bilder nur, wenn du eine echte Adresse hast: „![Beschreibung](https://…)“. Nichts erfinden.
- Haken, die Simon schon gesetzt hat („- [x]“), bleiben erhalten.
Deutsch, knapp, locker."""


def _files_note(b: Board) -> str:
    d = get_settings().files_dir / "boards" / b.id
    names = sorted(f.name for f in d.iterdir()) if d.is_dir() else []
    if not names:
        return ""
    return ("\nSimon hat diese Bilder/Dateien auf dem Board abgelegt (sie werden unter dem Text gezeigt, du musst sie nicht einbinden): "
            + ", ".join(names[:30]) + "\n")


def _prompt(b: Board) -> str:
    now = utcnow().astimezone().strftime("%d.%m.%Y %H:%M")
    if b.content.strip():
        since = b.last_run_at.astimezone().strftime("%d.%m.%Y %H:%M") if b.last_run_at else "dem letzten Stand"
        return (f"{RULES}\n\nThema/Auftrag des Boards „{b.title}“:\n{b.prompt}\n{_files_note(b)}\nJetzt: {now}. Das Board wurde zuletzt am {since} aktualisiert. "
                f"Das ist der aktuelle Stand:\n<<<BOARD\n{b.content}\n>>>BOARD\n\nSuche gezielt nach Neuem und Geändertem seit damals "
                "(neue Chat-Nachrichten, geänderte Wiki-Seiten, neue Termine, Mails …) und gib das KOMPLETTE aktualisierte Board aus. "
                "Unverändertes übernimmst du, Veraltetes oder Erledigtes passt du an oder streichst es.")
    return f"{RULES}\n\nThema/Auftrag des Boards „{b.title}“:\n{b.prompt}\n{_files_note(b)}\nJetzt: {now}. Erstelle das Board."


async def _ask(b: Board) -> str:
    base = get_settings().ida_chat_url.rstrip("/")
    if not base:
        raise Invalid("Die Verbindung zu Ida (IDA_CHAT_URL) ist nicht eingerichtet.")
    text, chat_id, err = "", None, ""
    body = {"agent_id": "ida", "text": _prompt(b), "model": b.model or None}
    async with httpx.AsyncClient(base_url=base, timeout=httpx.Timeout(connect=10, read=900, write=60, pool=10)) as c:
        try:
            async with c.stream("POST", "/api/v1/chat", json=body) as r:
                if r.status_code >= 400:
                    raise Invalid(f"Ida antwortet mit Fehler {r.status_code}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    if ev.get("chat"):
                        chat_id = ev["chat"].get("id")
                    elif ev.get("text") is not None:
                        text += ev["text"]
                    elif ev.get("done") and not ev["done"].get("ok"):
                        err = ev["done"].get("error") or "Ida konnte nicht antworten"
        finally:
            if chat_id:
                try:
                    await c.delete(f"/api/v1/chats/{chat_id}")
                except httpx.HTTPError:
                    pass
    if err and not text.strip():
        raise Invalid(err)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].removesuffix("```").strip()
    if not text:
        raise Invalid("Ida hat nichts geliefert")
    return text[:MAX_CONTENT]


async def _run(board_id: str) -> None:
    try:
        async with session_scope() as db:
            b = await db.get(Board, board_id)
        if not b:
            return
        try:
            content = await _ask(b)
            fields = {"content": content, "status": "idle", "error": "", "last_run_at": utcnow(), "run_count": b.run_count + 1}
        except (Invalid, httpx.HTTPError, ValueError) as e:
            log.warning("Board %s: %s", board_id, e)
            fields = {"status": "error", "error": (str(e) or "Ida ist nicht erreichbar")[:400]}
        async with session_scope(write=True) as db:
            b = await db.get(Board, board_id)
            if b:
                for k, v in fields.items():
                    setattr(b, k, v)
    except Exception:  # noqa: BLE001 – ein Board darf nie dauerhaft auf „läuft“ hängen
        log.exception("Board %s", board_id)
        async with session_scope(write=True) as db:
            b = await db.get(Board, board_id)
            if b:
                b.status, b.error = "error", "Unerwarteter Fehler"
    finally:
        _tasks.pop(board_id, None)


async def start(board_id: str) -> bool:
    """Startet einen Lauf; False, wenn schon einer läuft."""
    if board_id in _tasks:
        return False
    async with session_scope(write=True) as db:
        b = await db.get(Board, board_id)
        if not b:
            return False
        b.status, b.error = "running", ""
    _tasks[board_id] = asyncio.create_task(_run(board_id))
    return True


async def reset_stuck() -> None:
    """Nach einem Neustart läuft nichts mehr: hängende Boards freigeben."""
    async with session_scope(write=True) as db:
        for b in (await db.scalars(select(Board).where(Board.status == "running"))).all():
            b.status, b.error = "error", "Unterbrochen (Neustart) – bitte neu laden"
