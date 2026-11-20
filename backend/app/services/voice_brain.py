"""Stellas Gehirn in der eigenen Kette (Einstellungen → Stella → Eigene Kette).

Pro Gespräch läuft ein Claude-Prozess durchgehend (runner.Conversation, Modell aus den Einstellungen, Standard Haiku). Der
voice-Container schickt jeden erkannten Satz her (/voice/internal/brain/turn) und bekommt die Antwort Stück für Stück zurück,
während sie entsteht – so kann Piper den ersten Satz schon sprechen, bevor der Rest fertig ist.
Die Tools sind dieselben wie bei Gemini Live: Claude bekommt sie über den MCP-Endpunkt /mcp/voice/<token> (voice_bridge.mcp_handle).
"""
import asyncio
import logging
import secrets
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Account, Agent
from . import providers, voice_bridge, voice_engine
from .claude import runner
from .errors import Invalid, NotFound

log = logging.getLogger("ida.voice.brain")
SERVER = "stella"
# Der erste Zug eines frischen Claude-Prozesses dauert Sekunden länger (CLI startet, Tools laden, Prompt-Cache kalt).
# Beim zweiten Namen am Lautsprecher kommt der Befehl sofort: also gleich nach dem Start einen Probe-Zug schicken,
# während du noch redest bzw. Google erkennt. Der echte Satz wartet, bis die Probe durch ist, und läuft dann warm.
WARMUP = "[System: Verbindungstest vor dem Gespräch. Antworte nur mit „ok“, ohne Tools. Der Nutzer hört das nicht.]"


@dataclass
class Brain:
    call_id: str
    conv: runner.Conversation
    model: str
    account: str
    last: float
    warm: asyncio.Task | None = None


_brains: dict[str, Brain] = {}


async def _account(db, agent: Agent | None, model: str) -> Account:
    """Ein Account für das Modell, der eingeloggt und nicht am Limit ist – der des Agenten zuerst; sonst der erste passende."""
    accs = await providers.accounts_for(db, model)
    if not accs:
        raise Invalid(f"Kein Claude-Account für {model} eingerichtet (Einstellungen → Accounts)")
    if agent and agent.account_id:
        accs.sort(key=lambda a: a.id != agent.account_id)
    for a in accs:
        if providers.logged_in(a) and not await providers.exhausted(a):
            return a
    return accs[0]


async def reap() -> None:
    """Gespräche, die nichts mehr tun (voice-Container weg, kein finish): Prozess beenden."""
    idle = get_settings().voice_brain_idle_min * 60
    for cid, b in list(_brains.items()):
        if time.monotonic() - b.last > idle or cid not in voice_bridge._sessions:
            await close(cid)


async def _warm_up(b: Brain) -> None:
    t0 = time.monotonic()
    ok = False
    async for ev in b.conv.send(WARMUP):
        if ev["type"] == "done":
            ok = bool(ev.get("ok"))
    log.info("voice brain %s: vorgewärmt in %d ms ok=%s", b.call_id, (time.monotonic() - t0) * 1000, ok)


async def open(call_id: str, instructions: str, warm: bool = False) -> dict:
    await reap()
    sess = voice_bridge._sessions.get(call_id)
    if not sess:
        raise NotFound("Dieses Gespräch gibt es nicht (mehr)")
    await close(call_id)
    async with session_scope() as db:
        cfg = await voice_engine.get(db)
        agent = await db.get(Agent, sess.agent_id) or await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
        model = cfg["llm"]
        acc = await _account(db, agent, model)
    sess.brain_token = secrets.token_urlsafe(24)
    spec = runner.RunSpec(model=model, account=acc, prompt="", system=instructions,
                          mcp_servers={SERVER: {"type": "http", "url": f"{get_settings().internal_url}/mcp/voice/{sess.brain_token}"}},
                          allow=[f"mcp__{SERVER}"], kind="call")
    conv = runner.Conversation(spec)
    await conv.start()
    b = _brains[call_id] = Brain(call_id=call_id, conv=conv, model=model, account=acc.name, last=time.monotonic())
    if warm:
        b.warm = asyncio.create_task(_warm_up(b))
    log.info("voice brain %s: %s über %s", call_id, model, acc.name)
    return {"ok": True, "model": model, "account": acc.name}


def _note(sess, who: str, text: str) -> None:
    text = " ".join((text or "").split())
    if sess is not None and text:
        sess.talk = (sess.talk + [{"who": who, "text": text[:1200]}])[-40:]


async def turn(call_id: str, text: str, who: str = "user") -> AsyncIterator[dict]:
    """Ein Zug: {"t": "text", "text"} während Claude schreibt, {"t": "tool", "name", "running"} um Tool-Aufrufe,
    am Ende {"t": "done", "ok", "error"?, "hangup"?}. who="system": Hinweis vom System (Ida ist fertig, Begrüßung …), nicht vom Nutzer."""
    b = _brains.get(call_id)
    sess = voice_bridge._sessions.get(call_id)
    if not b or not b.conv.alive:
        yield {"t": "done", "ok": False, "error": "Das Gespräch hat kein Gehirn mehr – bitte neu starten"}
        return
    b.last = time.monotonic()
    if who == "user":
        _note(sess, "user", text)
    names: dict[str, str] = {}
    said: list[str] = []
    result: dict = {"ok": False, "result": "Keine Antwort"}
    async for ev in b.conv.send(text):
        b.last = time.monotonic()
        if ev["type"] == "text":
            said.append(ev["text"])
            yield {"t": "text", "text": ev["text"]}
        elif ev["type"] == "tool":
            names[ev["id"]] = ev["name"]
            yield {"t": "tool", "name": ev["name"], "running": True}
        elif ev["type"] == "tool_result":
            yield {"t": "tool", "name": names.get(ev["id"], ""), "running": False}
            while sess is not None and sess.outbox:  # Musik-Befehl an den Lautsprecher (Pause, Lied laden …)
                yield {"t": "music", **sess.outbox.pop(0)}
        elif ev["type"] == "done":
            result = ev
    _note(sess, "stella", "".join(said))
    while sess is not None and sess.outbox:  # falls kein tool_result kam
        yield {"t": "music", **sess.outbox.pop(0)}
    out: dict = {"t": "done", "ok": bool(result.get("ok"))}
    if not result.get("ok"):
        out["error"] = str(result.get("result") or "Fehler")[:300]
        log.warning("voice brain %s: %s", call_id, out["error"])
    if sess is not None and sess.hangup:
        out["hangup"], sess.hangup = sess.hangup, ""
    yield out


async def close(call_id: str) -> None:
    b = _brains.pop(call_id, None)
    if b:
        if b.warm and not b.warm.done():
            b.warm.cancel()
        await b.conv.close()
