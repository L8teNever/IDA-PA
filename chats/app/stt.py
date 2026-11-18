"""Sprachnachrichten als Text. Zwei Wege, in den Einstellungen wählbar (Pref stt_local): Beeper (`transcribe_voice_message`, Beeper schaltet das bald ab)
oder lokal mit dem Dienst „whisper“ (Whisper large-v3-turbo auf dem Prozessor, docs/WHISPER.md). Jede Umwandlung wird in chats.db (Tabelle
`transcripts`, Volume chats-data) gesichert – ein Text wird also nur einmal gerechnet, bleibt nach Neustart und Updates erhalten und steht auf allen
Geräten gleich da (store.sync_transcripts setzt ihn beim Laden der Nachrichten ein).
Pref stt_auto: neue Sprachnachrichten anderer wandelt der Dienst von selbst um (auch bei geschlossener App, über den Archiv-Lauf in live.py); sonst
erst beim Antippen des Text-Symbols."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from functools import partial

import httpx
from fastapi import APIRouter, HTTPException

from . import api, bus, push, store
from .config import get_settings

log = logging.getLogger("chats.stt")
router = APIRouter(prefix="/api/v1")

AUTO_MAX_AGE_S = 24 * 3600     # automatisch nur, was höchstens so alt ist (und nach dem Einschalten kam)
AUTO_MAX_DURATION_S = 600      # längere Sprachnachrichten nur auf Antippen
AUTO_MAX_QUEUE = 20            # so viele darf der Dienst gleichzeitig vormerken
FAIL_COOLDOWN_S = 600          # ging eine automatische Umwandlung schief: so lange nicht noch einmal versuchen
HTTP_TIMEOUT = httpx.Timeout(600.0, connect=5.0)   # große Datei auf dem Prozessor: Minuten
WAIT_S = 50                    # so lange hält eine Anfrage höchstens offen – Cloudflare bricht nach ~100 s ab (Fehler 524); dann heißt es „noch in Arbeit“

_flight: dict[str, asyncio.Task] = {}   # gerade in Arbeit: dieselbe Nachricht nie doppelt (Antippen und Automatik teilen sich die Arbeit)
_fail: dict[str, float] = {}
_gate = asyncio.Semaphore(1)   # die Automatik wandelt immer nur eine um; Antippen darf dazwischen (Whisper reiht es selbst ein)
_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    return _http


async def _local(src: str, mime: str) -> tuple[str, str]:
    """Audio von Beeper holen und an den Whisper-Dienst schicken → (Text, Sprache)."""
    url = get_settings().whisper_url.rstrip("/")
    if not url:
        raise HTTPException(409, "Die lokale Umwandlung ist nicht eingerichtet (WHISPER_URL fehlt)")
    data, ctype = await api.media_bytes(src, mime)
    try:
        r = await _client().post(f"{url}/api/v1/transkribieren", content=data, headers={"content-type": ctype or "application/octet-stream"})
    except httpx.HTTPError as e:
        raise HTTPException(502, "Der Whisper-Dienst ist nicht erreichbar") from e
    if r.status_code == 503:
        raise HTTPException(503, "Das Whisper-Modell lädt noch – in ein paar Minuten noch einmal versuchen")
    if r.status_code == 413:
        raise HTTPException(413, "Die Sprachnachricht ist für die lokale Umwandlung zu groß")
    if r.status_code != 200:
        raise HTTPException(502, "Whisper konnte die Sprachnachricht nicht umwandeln")
    try:
        d = r.json()
    except ValueError as e:
        raise HTTPException(502, "Whisper: unerwartete Antwort") from e
    return str(d.get("text") or "").strip(), str(d.get("language") or "")[:8]


async def _work(src: str, mime: str, gate: asyncio.Semaphore | None) -> str:
    async def run() -> str:
        if push.prefs()["stt_local"]:
            engine = "lokal"
            text, lang = await _local(src, mime)
        else:
            engine, lang = "beeper", ""
            text = (await api.call(api.beeper().text, "transcribe_voice_message", {"srcURL": src})).strip()
        if text or engine == "lokal":   # Whisper „hört nichts“ ist ein Ergebnis (Stille wird übersprungen); ein leerer Beeper-Text heißt eher „noch nicht fertig“
            store.save_transcript(src, text, engine, lang)
        return text
    if gate:
        async with gate:
            return await run()
    return await run()


def _start(src: str, mime: str, gate: asyncio.Semaphore | None = None) -> asyncio.Task:
    k = store.tkey(src)
    t = _flight.get(k)
    if t is None:
        t = _flight[k] = asyncio.get_running_loop().create_task(_work(src, mime, gate))
        t.add_done_callback(lambda x: (_flight.pop(k, None), x.cancelled() or x.exception()))   # Fehler holen, sonst meckert asyncio
    return t


async def text_of(src: str, mime: str = "") -> str:
    """Text einer Sprachnachricht: aus chats.db, sonst jetzt umwandeln (und sichern)."""
    got = store.transcripts([src]).get(store.tkey(src))
    if got is not None:
        return got
    return await asyncio.shield(_start(src, mime))   # shield: bricht die Anfrage ab, läuft die Umwandlung trotzdem fertig und wird gesichert


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _finished(x: asyncio.Task, k: str, chat_id: str) -> None:
    if x.cancelled():
        return
    if x.exception():
        _fail[k] = time.monotonic()
        if len(_fail) > 500:
            _fail.clear()
        log.warning("Automatisch umwandeln fehlgeschlagen: %s", x.exception())
        return
    bus.publish({"type": "stt", "id": chat_id})   # offene Fenster laden den Chat neu und zeigen den Text


def auto(chat_id: str, items: list[dict]) -> None:
    """Neue Sprachnachrichten anderer im Hintergrund umwandeln – nur wenn „Automatisch umwandeln“ an ist, nur was nach dem Einschalten kam
    (höchstens einen Tag alt) und nicht zu lang ist. Läuft bei jedem Laden der Nachrichten mit, auch im Archiv-Lauf bei geschlossener App."""
    if chat_id.startswith("untis:") or chat_id == "ida" or not items:
        return
    try:
        if not push.prefs()["stt_auto"]:
            return
        since = max(float(push._kv("stt_auto_since") or 0), time.time() - AUTO_MAX_AGE_S)
        asyncio.get_running_loop()
    except (RuntimeError, ValueError):
        return
    cand = [f for m in items if not m.get("mine") and not m.get("deleted") and _ts(m.get("at") or "") >= since
            for f in m.get("files") or [] if f.get("kind") == "audio" and not f.get("transcript") and f.get("src")
            and (api.SRC.match(f["src"]) or api.is_local(f["src"])) and (f.get("duration") or 0) <= AUTO_MAX_DURATION_S]
    if not cand:
        return
    known = store.transcripts([f["src"] for f in cand])
    now = time.monotonic()
    for f in cand:
        k = store.tkey(f["src"])
        if k in known or k in _flight or now - _fail.get(k, -1e9) < FAIL_COOLDOWN_S or len(_flight) >= AUTO_MAX_QUEUE:
            continue
        _start(f["src"], f.get("mime") or "", _gate).add_done_callback(partial(_finished, k=k, chat_id=chat_id))


@router.get("/transcript")
async def transcript(src: str, mime: str = ""):
    """Sprachnachricht als Text (Beeper oder lokal, je nach Einstellung); einmal umgewandelt, kommt der Text danach aus chats.db.
    Dauert es länger als WAIT_S, kommt {"text": "", "pending": true} – dieselbe Anfrage später wiederholen."""
    api.check_src(src)
    if src.startswith(("untis:", "ida:")):
        raise HTTPException(400, "Nur Sprachnachrichten aus Beeper lassen sich umwandeln")
    try:
        return {"text": await asyncio.wait_for(text_of(src, mime[:100]), WAIT_S)}
    except asyncio.TimeoutError:   # die Umwandlung rechnet weiter (shield) und wird gesichert; die Seite fragt in ein paar Sekunden noch einmal und hängt sich daran
        return {"text": "", "pending": True}


@router.get("/stt/local")
async def local_status():
    """Zustand des Whisper-Dienstes für die Einstellungen: {configured, status (bereit|lädt|fehler|offline|aus), model, error, saved}."""
    url = get_settings().whisper_url.rstrip("/")
    out = {"configured": bool(url), "status": "aus" if not url else "offline", "model": "", "error": "", "saved": store.count_transcripts()}
    if url:
        try:
            r = await _client().get(f"{url}/api/v1/health", timeout=3)
            d = r.json() if r.status_code == 200 else {}
            out.update(status=str(d.get("status") or "offline"), model=str(d.get("model") or ""), error=str(d.get("error") or "")[:200])
        except (httpx.HTTPError, ValueError):
            pass
    return out
