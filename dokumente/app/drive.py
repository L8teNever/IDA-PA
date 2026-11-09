"""Backup nach Google Drive: lädt .idadocs und .md je Dokument in einen Ordner hoch (eigener OAuth-Zugang, Scope drive.file).

Läuft im Hintergrund und entprellt: viele Speichern hintereinander → ein Upload. Fehler stören das Speichern nie, der Status steht in /api/v1/backup.
"""
import asyncio
import json
import logging
import time

import httpx

from . import store
from .config import get_settings

log = logging.getLogger("docs.drive")
API = "https://www.googleapis.com"
_state = {"last_ok": None, "last_error": None, "pending": 0}
_dirty: set[str] = set()
_wake = asyncio.Event()
_token = {"v": "", "exp": 0.0}


def status() -> dict:
    return {"enabled": get_settings().drive_on, **_state, "pending": len(_dirty)}


def mark(i: str) -> None:
    if get_settings().drive_on:
        _dirty.add(i)
        _wake.set()


def _map_path():
    return get_settings().data_dir / "drive.json"


def _load_map() -> dict:
    try:
        return json.loads(_map_path().read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


async def _access(c: httpx.AsyncClient) -> str:
    if _token["v"] and time.time() < _token["exp"] - 60:
        return _token["v"]
    s = get_settings()
    r = await c.post("https://oauth2.googleapis.com/token", data={
        "client_id": s.google_client_id, "client_secret": s.google_client_secret,
        "refresh_token": s.google_refresh_token, "grant_type": "refresh_token"})
    r.raise_for_status()
    j = r.json()
    _token.update(v=j["access_token"], exp=time.time() + int(j.get("expires_in", 3000)))
    return _token["v"]


async def _folder(c: httpx.AsyncClient, h: dict, m: dict) -> str:
    s = get_settings()
    if s.drive_folder_id:
        return s.drive_folder_id
    if m.get("_folder"):
        return m["_folder"]
    r = await c.post(f"{API}/drive/v3/files", headers=h, json={"name": "IDA Docs", "mimeType": "application/vnd.google-apps.folder"})
    r.raise_for_status()
    m["_folder"] = r.json()["id"]
    return m["_folder"]


async def _put(c: httpx.AsyncClient, h: dict, folder: str, m: dict, key: str, name: str, mime: str, body: bytes) -> None:
    """Anlegen oder überschreiben (multipart); die Drive-ID je Datei merken wir uns in drive.json."""
    fid = m.get(key)
    meta = {"name": name} if fid else {"name": name, "parents": [folder]}
    boundary = "idadocs-b"
    payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
               f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + body + f"\r\n--{boundary}--".encode()
    ch = {**h, "Content-Type": f"multipart/related; boundary={boundary}"}
    url = f"{API}/upload/drive/v3/files" + (f"/{fid}" if fid else "") + "?uploadType=multipart&fields=id"
    r = await (c.patch if fid else c.post)(url, headers=ch, content=payload)
    if r.status_code == 404 and fid:   # in Drive gelöscht → neu anlegen
        m.pop(key, None)
        return await _put(c, h, folder, m, key, name, mime, body)
    r.raise_for_status()
    m[key] = r.json()["id"]


async def _sync(i: str) -> None:
    s = get_settings()
    m = _load_map()
    async with httpx.AsyncClient(timeout=30) as c:
        h = {"Authorization": f"Bearer {await _access(c)}"}
        folder = await _folder(c, h, m)
        d = store.load(i)
        if not d:
            return
        safe = "".join(ch if ch.isalnum() or ch in " -_äöüÄÖÜß" else "_" for ch in d["title"]).strip() or i
        ida, mdp = store._paths(i)
        await _put(c, h, folder, m, f"{i}.idadocs", f"{safe}.idadocs", "application/json", ida.read_bytes())
        await _put(c, h, folder, m, f"{i}.md", f"{safe}.md", "text/markdown", mdp.read_bytes())
    _map_path().write_text(json.dumps(m), encoding="utf8")


async def worker() -> None:
    while True:
        await _wake.wait()
        await asyncio.sleep(20)     # entprellen
        _wake.clear()
        for i in list(_dirty):
            try:
                await _sync(i)
                _dirty.discard(i)
                _state.update(last_ok=time.time(), last_error=None)
            except Exception as e:  # noqa: BLE001 – Backup darf nie etwas kaputt machen
                _state["last_error"] = str(e)[:200]
                log.warning("Drive-Backup %s fehlgeschlagen: %s", i, e)
                await asyncio.sleep(60)
                _wake.set()
                break
