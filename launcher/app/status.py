"""Seiten-Infos: Titel der Website und ob sie gerade erreichbar ist (grüner/roter Punkt in der Kachel).

Wie bei den Symbolen wird zuerst die interne Tunnel-Adresse abgefragt (Seiten hinter Access sind von außen nicht lesbar),
sonst die öffentliche. Das Ergebnis liegt auf der Platte und wird im Hintergrund erneuert – die Liste wartet nie darauf.
"""
import asyncio
import html
import json
import re
import time

import httpx

from .config import get_settings

TTL = 600  # so lange gilt ein Ergebnis, danach wird es im Hintergrund neu geholt
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_busy: set[str] = set()
_sem = asyncio.Semaphore(8)
_tasks: set[asyncio.Task] = set()


def _file(host: str):
    d = get_settings().data_dir / "status"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^a-z0-9.-]", "_", host.lower()) + ".json")


def clean_title(raw: str) -> str:
    t = re.sub(r"\s+", " ", html.unescape(raw or "")).strip()
    return t[:60].rstrip() if len(t) > 60 else t


def read(host: str) -> dict | None:
    try:
        d = json.loads(_file(host).read_text())
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def fresh(d: dict | None) -> bool:
    return bool(d) and time.time() - d.get("ts", 0) < TTL


async def probe(app: dict) -> dict:
    """{title, up, ts}: up=False bei Verbindungsfehler oder Serverfehler (ab 500)."""
    bases = [b for b in (app.get("service") or "", app.get("url") or f"https://{app['host']}") if b.startswith("http")]
    title, up = "", False
    async with httpx.AsyncClient(timeout=6, follow_redirects=False) as c:
        for base in bases:
            try:
                r = await c.get(base.rstrip("/") + "/")
            except httpx.HTTPError:
                continue
            if r.status_code >= 500:
                continue
            up = True
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                m = _TITLE.search(r.text[:200_000])
                title = clean_title(m.group(1)) if m else ""
            break
    return {"title": title, "up": up, "ts": int(time.time())}


async def _refresh(app: dict) -> None:
    host = app["host"]
    try:
        async with _sem:
            d = await probe(app)
        if not d["title"]:  # kein neuer Titel: den alten behalten, statt ihn bei kurzem Ausfall zu verlieren
            d["title"] = (read(host) or {}).get("title", "")
        _file(host).write_text(json.dumps(d))
    except Exception:  # Hintergrundarbeit darf nie die Seite stören
        pass
    finally:
        _busy.discard(host)


def warm(items: list[dict]) -> bool:
    """Startet für veraltete oder fehlende Einträge eine Abfrage im Hintergrund. True, wenn für Neues noch Daten fehlen."""
    missing = False
    for a in items:
        d = read(a["host"])
        if fresh(d):
            continue
        missing = missing or d is None
        if a["host"] in _busy:
            continue
        _busy.add(a["host"])
        t = asyncio.get_running_loop().create_task(_refresh(a))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)
    return missing
