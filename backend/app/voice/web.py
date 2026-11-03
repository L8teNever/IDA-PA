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

from . import prompt
from .ida import Ida
from .live import LiveSession

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


class WebSink:
    def __init__(self, ws):
        self.ws = ws
        self.done = asyncio.Event()
        self.outcome = ""

    async def _json(self, obj: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            self.done.set()

    async def audio(self, pcm: bytes, rate: int) -> None:
        try:
            await self.ws.send_bytes(pcm)
        except Exception:  # noqa: BLE001
            self.done.set()

    async def interrupted(self) -> None:
        await self._json({"type": "interrupted"})

    async def text(self, who: str, piece: str) -> None:
        await self._json({"type": "text", "who": who, "text": piece})

    async def tool(self, name: str, running: bool) -> None:
        await self._json({"type": "tool", "name": name, "running": running})

    def hang_up(self, outcome: str) -> None:
        self.outcome = outcome
        asyncio.create_task(self._json({"type": "end", "reason": outcome}))  # the browser plays what is queued, then closes
        self.done.set()


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
        voice = info.get("voice") or getattr(settings, "gemini_voice", None) or "Aoede"
        session = LiveSession(settings, instructions=prompt.instructions(info.get("context") or "", owner=True, kind="web"),
                              tools=info.get("tools") or [], run_tool=lambda n, a: ida.tool(call_id, n, a), sink=sink,
                              kickoff=prompt.kickoff(owner=True, kind="web"), client_vad=False, connect=connect, voice=voice)
        await session.open()
        await sink._json({"type": "ready", "rate": 24000, "agent": info.get("agent") or "Stella", "offline": bool(info.get("offline"))})
        await session.kickoff()

        async def browser():
            while not sink.done.is_set():
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes"):
                    await session.send_audio(msg["bytes"])
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
