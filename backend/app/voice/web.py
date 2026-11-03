"""Live conversation from the IDA app (live button): browser <-> this container <-> Gemini Live.

The browser gets a ticket from IDA (POST /api/v1/voice/ticket, only reachable when logged in) and connects to
/live?ticket=…. Only IDA can make tickets (VOICE_SECRET), each one works once and for one minute – so whoever has a
ticket is the user himself: full rights, like a call from the owner's number.

Protocol: browser -> server binary = PCM 16 bit LE, 16 kHz mono; text = {"type": "end"} | {"type": "text", "text": …}
          server -> browser binary = PCM 16 bit LE, 24 kHz mono; text = {"type": "ready"|"text"|"tool"|"interrupted"|"end"|"error", …}
"""
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import replace

from . import prompt
from .ida import Ida
from .live import LiveSession
from .vad import EnergyVad, config_from_settings, pcm_rms

log = logging.getLogger("ida.voice.web")
_used: dict[str, float] = {}  # nonce -> expiry (a ticket works once)


def check_ticket(ticket: str, secret: str, now: float | None = None) -> bool:
    now = now or time.time()
    for n, exp in list(_used.items()):
        if exp < now:
            _used.pop(n, None)
    try:
        exp_s, nonce, sig = (ticket or "").split(".")
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(secret.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(sig, good) or exp < now or nonce in _used:
        return False
    _used[nonce] = exp
    return True


TICKET_S = 60


def make_ticket(secret: str, now: float | None = None) -> str:
    """exp.nonce.sig – checked by check_ticket with the same secret (no call back to IDA needed)."""
    exp = int((now or time.time()) + TICKET_S)
    nonce = secrets.token_hex(8)
    sig = hmac.new(secret.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{nonce}.{sig}"


def app_key(secret: str) -> str:
    """Key of the stand-alone live app (PWA on the voice host): IDA hands it out inside the install link, the app keeps it."""
    return hmac.new(secret.encode(), b"live-pwa", hashlib.sha256).hexdigest()[:40] if secret else ""


class WebSink:
    def __init__(self, ws):
        self.ws = ws
        self.done = asyncio.Event()
        self.outcome = ""
        self.play_end = 0.0  # wann Stellas bisher gesendete Stimme im Browser ungefähr fertig gespielt ist

    async def _json(self, obj: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            self.done.set()

    async def audio(self, pcm: bytes, rate: int) -> None:
        self.play_end = max(self.play_end, time.monotonic()) + len(pcm) / (rate * 2)
        try:
            await self.ws.send_bytes(pcm)
        except Exception:  # noqa: BLE001
            self.done.set()

    async def interrupted(self) -> None:
        self.play_end = 0.0  # der Browser verwirft, was noch in der Warteschlange lag
        await self._json({"type": "interrupted"})

    async def text(self, who: str, piece: str) -> None:
        await self._json({"type": "text", "who": who, "text": piece})

    async def tool(self, name: str, running: bool) -> None:
        await self._json({"type": "tool", "name": name, "running": running})

    def hang_up(self, outcome: str) -> None:
        self.outcome = outcome
        asyncio.create_task(self._json({"type": "end", "reason": outcome}))  # the browser plays what is queued, then closes
        self.done.set()


class BrowserMic:
    """Browser-Mikro -> Gemini mit dem Geräusch-Filter vom Telefon: Hintergrund und Motor gelten nicht als Sprache."""

    def __init__(self, session, sink: WebSink, settings):
        self.session = session
        self.sink = sink
        self.s = settings
        self.vad = EnergyVad(replace(config_from_settings(settings), rms_threshold=int(settings.voice_web_vad_rms),
                                     silence_ms=int(settings.voice_web_vad_silence_ms)))
        self.turn_open = False

    def _echo(self, rms: int) -> bool:
        """Solange Stella noch zu hören ist, ist ein leiser Beginn ihr eigenes Echo und keine Unterbrechung."""
        need = int(self.s.voice_vad_rms * self.s.voice_echo_rms_factor)
        return need > 0 and self.sink.play_end > time.monotonic() and rms < need

    async def feed(self, pcm: bytes) -> None:
        events = self.vad.feed(pcm)
        rms = pcm_rms(pcm)
        for ev in events:
            if ev == "start":
                if self._echo(rms):
                    self.vad.speaking = False
                    continue
                self.turn_open = True
                await self.session.activity_start()
        await self.session.send_audio(pcm)
        for ev in events:
            if ev == "end" and self.turn_open:
                self.turn_open = False
                await self.session.activity_end()


async def run(ws, settings, ida: Ida | None = None, connect=None, active: set | None = None) -> None:
    """One live conversation, from the accepted WebSocket until it closes."""
    ida = ida or Ida(settings)
    call_id = "w" + secrets.token_hex(6)
    if active is not None:
        active.add(call_id)
    sink = WebSink(ws)
    session = None
    status, error = "done", ""
    try:
        info = await ida.start(call_id, "web", "", True, "")
        voice = info.get("voice") or "Aoede"
        session = LiveSession(settings, instructions=prompt.instructions(info.get("context") or "", owner=True, kind="web"),
                              tools=info.get("tools") or [], run_tool=lambda n, a: ida.tool(call_id, n, a), sink=sink,
                              kickoff=prompt.kickoff(owner=True, kind="web"), client_vad=settings.voice_web_vad, connect=connect, voice=voice)
        mic = BrowserMic(session, sink, settings) if settings.voice_web_vad else None
        await session.open()
        await sink._json({"type": "ready", "rate": 24000, "agent": info.get("agent") or "Stella", "offline": bool(info.get("offline"))})
        await session.kickoff()

        async def browser():
            while not sink.done.is_set():
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await (mic.feed if mic else session.send_audio)(msg["bytes"])
                elif msg.get("text"):
                    try:
                        m = json.loads(msg["text"])
                    except ValueError:
                        continue
                    if m.get("type") == "end":
                        break
                    if m.get("type") == "text" and str(m.get("text") or "").strip():
                        await session.say(str(m["text"])[:2000])
            sink.done.set()

        async def gemini():
            await session.run()
            sink.done.set()

        tasks = [asyncio.create_task(browser()), asyncio.create_task(gemini())]
        await sink.done.wait()
        await asyncio.sleep(0.2)  # let the "end" message go out
        for t in tasks:
            t.cancel()
    except Exception as e:  # noqa: BLE001
        log.warning("Live-Gespräch %s: %s", call_id, e)
        status, error = "failed", str(e)[:300]
        await sink._json({"type": "error", "message": "Gemini Live ist gerade nicht erreichbar." if "Gemini" in str(e) else "Das Gespräch ist abgebrochen."})
    finally:
        if session:
            await session.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
        if active is not None:
            active.discard(call_id)
        await ida.finish(call_id, "web", "", session.transcript if session else [],
                         (session.outcome if session else "") or sink.outcome, status, error)
