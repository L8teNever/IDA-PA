"""Belegte Adressen im Wiki-Gedächtnis (nur IDA Server): je Website und je Kurzlink eine Seite unter memory/projekte/domains/.

So können die KIs beantworten, welche Domains und Subdomains belegt sind und seit wann. Der Stand wird abgeglichen statt einzeln
geschrieben: Nach jeder Änderung und alle paar Minuten vergleicht sync() Websites und Links mit dem Gedächtnis, legt Fehlendes an
und löscht Überflüssiges. Dadurch kommen auch ältere Websites hinein, und eine Änderung geht nicht verloren, wenn das Wiki gerade weg ist.
Gelöscht wird nur, was dieses Modul selbst anlegt (Präfixe website-/link-); eigene Notizen im Ordner bleiben.
"""
import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI

from .config import get_settings

log = logging.getLogger("dashboard.wiki_memory")

FOLDER = "memory/projekte/domains"
OWN = ("website-", "link-")
BY = "server"            # erscheint im Wiki als Autor
EVERY = 600              # Abgleich alle 10 Minuten

_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()
_written: dict[str, str] = {}  # Pfad → zuletzt geschriebener Inhalt, spart das Nachlesen


def enabled() -> bool:
    return bool(get_settings().wiki_url)


def when(sec: int) -> str:
    return datetime.fromtimestamp(sec, ZoneInfo(get_settings().timezone)).strftime("%d.%m.%Y %H:%M")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def plain_url(url: str) -> str:
    """Adresse ohne Anmeldedaten, Parameter und Anker: Dort stehen gern Tokens, und die gehören nicht ins Gedächtnis."""
    u = urlsplit(url)
    if not u.scheme or not u.hostname:  # z. B. git@github.com:name/repo.git
        return url
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}{u.path}"


def page(name: str, title: str, summary: str, facts: list[tuple[str, str]], tags: list[str]) -> dict:
    body = "\n".join(f"- **{k}:** {v}" for k, v in facts if v)
    return {"path": f"{FOLDER}/{name}", "title": title, "body": body, "summary": summary, "tags": ["domain", *tags]}


def site_page(s: dict) -> dict:
    url = f"https://{s['hostname']}{s.get('path') or ''}"
    made = when(s["created"]) if s.get("created") else ""
    source = plain_url(s["repo"]) if s.get("source") == "github" and s.get("repo") else "Dateien auf dem Server"
    return page("website-" + slug(s["hostname"] + (s.get("path") or "")), f"Website {s['hostname']}{s.get('path') or ''}",
                f"{s['hostname']}{s.get('path') or ''} ist durch die Website „{s['name']}“ im IDA Server belegt" + (f" (erstellt {made})." if made else "."),
                [("Adresse", url), ("Domain", s["hostname"]), ("Belegt durch", f"Website „{s['name']}“ im IDA Server"), ("Erstellt", made),
                 ("Quelle", source), ("Zugriff", "nur mit Login (Zero Trust)" if s.get("zero_trust") else "öffentlich"),
                 ("Haupt-Adresse", f"https://{s['main']}" if s.get("main") else ""),
                 ("Weitere Domains", ", ".join(f"https://{a['hostname']}{a.get('path') or ''}" for a in s.get("aliases") or []))], ["website"])


def link_page(link: dict, domains: list[str]) -> dict:
    code, made = link["code"], when(link["created"])
    return page("link-" + slug(code), f"Kurzlink {code}", f"Kurzlink /{code} führt zu {plain_url(link['target'])} (erstellt {made}).",
                [("Kürzel", code), ("Adressen", ", ".join(f"https://{d}/{code}" for d in domains)), ("Ziel", plain_url(link["target"])),
                 ("Erstellt", made), ("Notiz", link.get("note") or "")], ["kurzlink"])


def desired(sites: list[dict], links: list[dict], domains: list[str]) -> dict[str, dict]:
    # weitere Domains einer Website bekommen eine eigene Seite: auch sie sind belegt
    extra = [{**s, "hostname": d["hostname"], "path": d.get("path") or "", "aliases": [], "main": s["hostname"] + (s.get("path") or "")}
             for s in sites for d in s.get("aliases") or []]
    pages = [site_page(s) for s in [*sites, *extra]] + [link_page(x, domains) for x in links]
    pages.append(page("link-domains", "Domains der Kurzlinks", f"Für Kurzlinks belegt: {', '.join(domains)}.",
                      [("Domains", ", ".join(domains)), ("Belegt durch", "Dienst „links“, verwaltet im IDA Server unter Weiterleitungen")], ["kurzlink"]))
    return {p["path"]: p for p in pages}


async def call(c: httpx.AsyncClient, method: str, path: str, **kw) -> httpx.Response:
    r = await c.request(method, path, **kw)
    r.raise_for_status()
    return r


async def save(c: httpx.AsyncClient, p: dict) -> None:
    await call(c, "POST", "/api/v1/pages", json={
        "path": p["path"], "title": p["title"], "body": p["body"], "note": "IDA Server: Abgleich der belegten Adressen",
        "force_new": True,  # sonst hält die Dubletten-Sperre app.… und app2.… für dieselbe Seite
        "frontmatter": {"summary": p["summary"], "category": "projekte", "tags": p["tags"], "importance": 2}})


async def apply(c: httpx.AsyncClient, want: dict[str, dict]) -> None:
    listing = (await call(c, "GET", "/api/v1/pages", params={"space": "memory", "folder": FOLDER, "limit": 1000})).json()
    have = {p["path"] for p in listing.get("pages") or []}
    for path, p in want.items():
        try:
            if path in have and _written.get(path) == p["body"]:
                continue
            if path in have and path not in _written:  # nach einem Neustart: nur schreiben, wenn sich wirklich etwas geändert hat
                cur = (await call(c, "GET", "/api/v1/page", params={"ref": path})).json()
                if (cur.get("body") or "").strip() == p["body"].strip():
                    _written[path] = p["body"]
                    continue
            await save(c, p)
            _written[path] = p["body"]
        except httpx.HTTPStatusError as e:  # eine abgelehnte Seite hält die anderen nicht auf
            log.warning("Gedächtnis: %s nicht gespeichert (%s): %s", path, e.response.status_code, e.response.text[:200])
    for path in have - want.keys():
        if path.rsplit("/", 1)[1].startswith(OWN):
            await call(c, "POST", "/api/v1/pages/delete", json={"ref": path, "note": "IDA Server: Adresse nicht mehr belegt"})
            _written.pop(path, None)


async def sync(app: FastAPI) -> None:
    if not enabled():
        return
    from .tools import links, sites  # erst hier: beide Tools rufen dieses Modul auf
    s = get_settings()
    headers = {"X-Wiki-By": BY, **({"Authorization": f"Bearer {s.wiki_ida_token}"} if s.wiki_ida_token else {})}
    async with _lock:
        want = desired(sites.load(), links.store(app).all(), s.link_domains)
        try:
            async with httpx.AsyncClient(base_url=s.wiki_url.rstrip("/"), headers=headers, timeout=15) as c:
                await apply(c, want)
        except httpx.HTTPError as e:  # Wiki gerade weg: der nächste Abgleich holt es nach
            log.warning("Gedächtnis-Abgleich fehlgeschlagen: %s", e)


def kick(app: FastAPI) -> None:
    """Nach einer Änderung im Hintergrund abgleichen; die Oberfläche wartet nicht aufs Wiki."""
    if enabled():
        t = asyncio.create_task(sync(app))
        _tasks.add(t)
        t.add_done_callback(_tasks.discard)


async def loop(app: FastAPI) -> None:
    while True:
        try:
            await sync(app)
        except Exception:  # der Abgleich darf den Server nie mitreißen
            log.exception("Gedächtnis-Abgleich")
        await asyncio.sleep(EVERY)


def reset() -> None:
    _written.clear()
