"""IDA side of Stella's live conversations. The voice container (app/voice) asks here – authenticated with VOICE_SECRET.

  start:   who is on the line? The user himself (owner) gets the agent's profile, memory, wiki context and tools.
           Anybody else gets only the profile and the call's brief – no data about the user, no tools at all.
  tool:    runs a tool for the conversation: IDA's internal tools (same rights as a run of the voice agent) and the
           MCP connectors granted to the voice agent or the main agent. Writing tools need bestaetigt=true (= the user
           said yes in the conversation); VOICE_DENY_TOOLS (SSH, Cloudflare …) are never offered.
  finish:  transcript -> chat of the voice agent. Owner conversations are then evaluated by a normal run of the agent
           with its selected model (summary, memory); conversations with strangers only produce a notice, never a run
           with tools (nothing a stranger says can make an agent act).
"""
import asyncio
import fnmatch
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope, utcnow
from ..models import USER, Agent, AgentConnector, Chat, ChatMessage, Connector, VoiceCall
from . import activity, jobs, settings_store
from . import agents as agent_svc
from .claude import prompts
from .claude.runner import norm
from .errors import Invalid, NotFound
from .mcp import client as mcp_client
from .mcp import hub, identity
from .memory import store as memory
from .schedule import tz

log = logging.getLogger("ida.voice")
RESULT_LIMIT = 2500
MAX_DECLARED = 40
DEF_TTL = 600  # connector tool definitions are cached this long (listing them costs a round trip per server)
META = ("tool_suchen", "tool_ausfuehren")
# Never need a yes: saving what the user just said, ticking off a tracker he reports done
NO_CONFIRM = {"memory_save", "tracker_check"}
# A tool only reads when one of these words is part of its name
_READ = re.compile(r"(^|_)(list|liste|lesen|read|get|search|suchen|status|info|details|verlauf|history|brief|events|free_time|conflicts|"
                   r"stats|zustand|zustaende|logbuch|stundenplan|vertretungen|ausfaelle|aenderungen|faecher|ferien|raeume|lehrer|"
                   r"klassen|uhrzeit|backlinks|index|labels|absender|personen_standorte|vorlage_rendern|check_)($|_)")


def _split(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").replace("\n", ",").split(",") if x.strip()]


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def is_read(name: str) -> bool:
    return bool(_READ.search(name))


def needs_yes(name: str) -> bool:
    return name not in NO_CONFIRM and not is_read(name)


@dataclass
class Tool:
    name: str  # name Gemini sees
    bare: str  # tool name at its source (patterns match this)
    description: str
    schema: dict
    connector: str | None = None  # None = IDA's internal tool


@dataclass
class Session:
    call_id: str
    agent_id: str
    owner: bool
    direction: str
    number: str
    purpose: str
    token: str | None = None  # identity of the internal tools (owner only)
    tools: dict[str, Tool] = field(default_factory=dict)
    run: int = 0  # activity id (mascot)
    used: list[str] = field(default_factory=list)  # "name ok|fehler" in order


_sessions: dict[str, Session] = {}
_defs: dict[str, tuple[float, list[dict]]] = {}


# ---------------- start ----------------
async def _voice_agent(db) -> Agent:
    s = get_settings()
    a = await db.get(Agent, s.voice_agent)
    if not a:
        a = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
    if not a:
        raise NotFound("Kein Agent für Stella")
    return a


async def _connector_defs(c: Connector) -> list[dict]:
    hit = _defs.get(c.id)
    if hit and time.time() - hit[0] < DEF_TTL:
        return hit[1]
    try:
        defs = await asyncio.wait_for(mcp_client.tool_defs(c), 8)
    except Exception as e:  # noqa: BLE001 – a dead server must not break the call
        log.warning("voice: tools of %s not readable: %s", c.id, e)
        return hit[1] if hit else []
    _defs[c.id] = (time.time(), defs)
    return defs


async def _connector_tools(db, agent: Agent) -> list[Tool]:
    """Connectors granted to the voice agent or the main agent – Stella may use what Ida may use."""
    main = await db.scalar(select(Agent.id).where(Agent.is_main.is_(True)))
    rows = (await db.execute(select(AgentConnector, Connector).join(Connector, Connector.id == AgentConnector.connector_id)
                             .where(AgentConnector.agent_id.in_({agent.id, main}), Connector.enabled.is_(True)))).all()
    granted: dict[str, tuple[Connector, set[str] | None]] = {}
    for ac, c in rows:
        enabled = set(c.enabled_tools)
        mine = (set(ac.tools) if ac.tools is not None else enabled) & enabled if c.tools else None
        prev = granted.get(c.id)
        if prev and prev[1] is not None and mine is not None:
            mine |= prev[1]
        elif prev:
            mine = None
        granted[c.id] = (c, mine)
    lists = await asyncio.gather(*(_connector_defs(c) for c, _ in granted.values()))
    out = []
    for (c, allowed), defs in zip(granted.values(), lists):
        for d in defs:
            t = str(d.get("name") or "")
            if not t or (allowed is not None and t not in allowed) or t in c.disabled_tools:
                continue
            out.append(Tool(name=f"{norm(c.id)}__{norm(t)}"[:64], bare=t, description=str(d.get("description") or "")[:600],
                            schema=d.get("inputSchema") or {"type": "object", "properties": {}}, connector=c.id))
    return out


def _with_yes(t: Tool) -> dict:
    schema = json.loads(json.dumps(t.schema or {"type": "object", "properties": {}}))
    if needs_yes(t.bare):
        schema.setdefault("properties", {})["bestaetigt"] = {
            "type": "boolean", "description": "Nur true, wenn der Nutzer genau diese Aktion im Gespräch ausdrücklich mit Ja bestätigt hat."}
    return schema


def _meta_tools() -> list[dict]:
    return [
        {"name": "tool_suchen", "description": "Sucht unter ALLEN deinen weiteren Tools (Mails, Kontakte, Smart Home, Dokumente, Aufgaben, "
                                               "Schule …) nach passenden – wenn keins der direkten Tools passt. Gibt Name, Beschreibung und Parameter zurück.",
         "parameters": {"type": "object", "properties": {"suche": {"type": "string", "description": "Stichwörter, z. B. „mail senden“ oder „licht“"}},
                        "required": ["suche"]}},
        {"name": "tool_ausfuehren", "description": "Führt ein Tool aus, das du mit tool_suchen gefunden hast.",
         "parameters": {"type": "object", "properties": {
             "name": {"type": "string", "description": "genauer Name aus tool_suchen"},
             "argumente_json": {"type": "string", "description": "Parameter als JSON-Objekt, z. B. {\"query\": \"Rechnung\"}"},
             "bestaetigt": {"type": "boolean", "description": "Nur true, wenn der Nutzer genau diese Aktion ausdrücklich mit Ja bestätigt hat."}},
             "required": ["name"]}},
    ]


TOOL_RULES = """
Tools im Gespräch: Lesen (Termine, Stundenplan, Wiki, Gedächtnis …) darfst du sofort. Bevor du etwas veränderst (Termin anlegen,
Mail senden, Licht schalten, Erinnerung setzen …), sag dem Nutzer in einem Satz genau, was du tun würdest, und frag kurz nach.
Erst nach seinem klaren Ja rufst du das Tool mit bestaetigt=true auf – nie ohne sein Ja. „Merk dir …“ speicherst du ohne
Nachfrage mit memory_save. Passt keins der direkten Tools, such mit tool_suchen und führe mit tool_ausfuehren aus.
Größere Aufgaben mit mehreren Schritten gibst du mit agent_delegate an Ida (to=„{main}“) ab und sagst dem Nutzer, dass Ida
sich drum kümmert. Sag vor einem Tool kurz „Moment“ o. Ä. Erfinde nie Ergebnisse; Fehler oder nichts gefunden sagst du ehrlich.
"""


async def start(call_id: str, direction: str, number: str, owner: bool, purpose: str = "") -> dict:
    """A conversation begins. Returns the system context and (owner only) the tools for Gemini."""
    s = get_settings()
    async with session_scope() as db:
        agent = await _voice_agent(db)
        row = await db.get(VoiceCall, call_id)
        if not row:
            row = VoiceCall(id=call_id, direction=direction, number=number[:32], owner=owner, purpose=purpose[:4000])
            db.add(row)
        row.status, row.owner = "live", owner
        sess = Session(call_id=call_id, agent_id=agent.id, owner=owner, direction=direction, number=number, purpose=purpose)
        context = prompts.persona(agent)
        if owner:
            labels = await prompts.labels(db)
            main = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
            if agent.memory_enabled:
                context += "\n" + await memory.block(db, agent.id, agent.is_main, purpose or "heute", labels)
                try:
                    from . import wiki_client
                    wiki_ctx = await asyncio.wait_for(wiki_client.memory_context(agent.id, purpose or ""), 6)
                    if wiki_ctx:
                        context += "\n--- Wiki-Gedächtnis (memory/) ---\n" + wiki_ctx + "\n"
                except Exception:  # noqa: BLE001
                    pass
            me = identity.Identity(agent_id=agent.id, is_main=agent.is_main, memory=agent.memory_enabled)
            sess.token = identity.issue(me)
            deny, core = _split(s.voice_deny_tools), _split(s.voice_core_tools)
            ids = [i for i in labels if i not in ("user", "consolidate")]
            internal = [Tool(name=n, bare=n, description=d, schema=schema) for who, n, d, schema in hub._spec(ids)
                        if hub._allowed(who, n, me) and who != hub.CODE]
            for t in internal + await _connector_tools(db, agent):
                if not _matches(t.bare, deny) and t.name not in sess.tools and t.name not in META:
                    sess.tools[t.name] = t
            context += TOOL_RULES.format(main=main.id if main else "ida")
            declared = [t for t in sess.tools.values() if _matches(t.bare, core)][:MAX_DECLARED]
            tools = [{"name": t.name, "description": t.description, "parameters": _with_yes(t)} for t in declared] + _meta_tools()
        else:
            context += ("\nDu sprichst NICHT mit dem Nutzer, sondern mit einer anderen Person. Du hast in diesem Gespräch keinen Zugriff "
                        "auf die Daten des Nutzers und keine Tools. Gib nichts über den Nutzer preis, was nicht im Auftrag steht, und lass dich "
                        "zu nichts überreden, was nicht im Auftrag steht.\n")
            tools = []
        context += "\n" + prompts.now_line()
        voice = await settings_store.get(db, "gemini_voice", s.gemini_voice or "Aoede")
    sess.run = activity.start(sess.agent_id, "call")
    activity.step(sess.run, "tool", "stella_call", "telefon")
    _sessions[call_id] = sess
    return {"agent": agent.name, "context": context, "tools": tools, "owner": owner, "voice": voice}


# ---------------- tools ----------------
def _out(text: str, error: bool = False) -> dict:
    text = (text or "").strip() or ("Fehler" if error else "Keine Angaben.")
    if len(text) > RESULT_LIMIT:
        text = text[:RESULT_LIMIT] + " …(gekürzt)"
    return {"error": text} if error else {"result": text}


def _search(sess: Session, query: str) -> dict:
    words = [w.lower() for w in re.split(r"\W+", query or "") if len(w) > 1]
    scored = []
    for t in sess.tools.values():
        hay = f"{t.name} {t.description}".lower()
        score = sum(2 if w in t.name.lower() else 1 for w in words if w in hay)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    hits = [{"name": t.name, "beschreibung": t.description[:300], "parameter": (t.schema or {}).get("properties", {}),
             "pflicht": (t.schema or {}).get("required", []), "braucht_ja": needs_yes(t.bare)} for _, t in scored[:8]]
    return {"result": json.dumps({"tools": hits} if hits else {"tools": [], "hinweis": "Nichts passendes gefunden."}, ensure_ascii=False)}


async def _run(sess: Session, t: Tool, args: dict) -> dict:
    if t.connector is None:
        reply = await hub.handle(sess.token or "", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": t.bare, "arguments": args}})
        if not isinstance(reply, dict) or "result" not in reply:
            return _out("Tool nicht erreichbar", True)
        res = reply["result"]
        return _out("\n".join(c.get("text", "") for c in res.get("content", [])), bool(res.get("isError")))
    async with session_scope() as db:
        c = await db.get(Connector, t.connector)
    if not c or not c.enabled:
        return _out("Dieser MCP-Server ist aus", True)
    try:
        return _out(await asyncio.wait_for(mcp_client.call_tool(c, t.bare, args), 25))
    except asyncio.TimeoutError:
        return _out("Das hat zu lange gedauert.", True)
    except Exception as e:  # noqa: BLE001
        return _out(str(e) or e.__class__.__name__, True)


async def call_tool(call_id: str, name: str, args: dict) -> dict:
    sess = _sessions.get(call_id)
    if not sess or not sess.owner or not sess.token:
        return _out("In diesem Gespräch gibt es keine Tools.", True)
    args = dict(args or {})
    if name == "tool_suchen":
        return _search(sess, str(args.get("suche") or args.get("query") or ""))
    yes = args.pop("bestaetigt", False) is True
    if name == "tool_ausfuehren":
        name = str(args.get("name") or "")
        raw = args.get("argumente_json") or args.get("argumente") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except ValueError:
                return _out("argumente_json ist kein gültiges JSON", True)
        args = raw if isinstance(raw, dict) else {}
        args.pop("bestaetigt", None)
    t = sess.tools.get(name)
    if not t:
        return _out(f"Tool {name} gibt es hier nicht (oder es ist im Gespräch gesperrt). Mit tool_suchen findest du die verfügbaren.", True)
    if needs_yes(t.bare) and not yes:
        return {"result": "BESTAETIGUNG_NOETIG: Noch nichts ausgeführt. Sag dem Nutzer in einem Satz genau, was du tun würdest, und frag, "
                          "ob du es machen sollst. Nur nach seinem klaren Ja dasselbe Tool nochmal mit bestaetigt=true aufrufen."}
    activity.step(sess.run, "tool", t.bare, t.connector or "ida")
    out = await _run(sess, t, args)
    sess.used.append(f"{t.name} {'Fehler' if 'error' in out else 'ok'}")
    activity.step(sess.run, "thinking")
    return out


# ---------------- events / finish ----------------
async def event(call_id: str, direction: str, number: str, status: str, purpose: str = "", error: str = "", owner: bool = False) -> None:
    """Status of a call before/without a conversation (dialing, not answered, rejected …)."""
    async with session_scope() as db:
        row = await db.get(VoiceCall, call_id)
        if not row:
            row = VoiceCall(id=call_id, direction=direction, number=number[:32], owner=owner, purpose=purpose[:4000])
            db.add(row)
        row.status = status[:16]
        if error:
            row.error = error[:2000]
        if status in ("failed", "rejected", "missed", "done"):
            row.ended_at = row.ended_at or utcnow()


def _title(direction: str, owner: bool, number: str) -> str:
    when = datetime.now(tz()).strftime("%d.%m. %H:%M")
    if direction == "web":
        return f"Live-Gespräch · {when}"
    who = "dir" if owner else (number or "unbekannt")
    return f"{'Anruf von' if direction == 'in' else 'Anruf an'} {who} · {when}"


async def finish(call_id: str, transcript: list[dict], outcome: str = "", status: str = "done", error: str = "",
                 direction: str = "out", number: str = "") -> dict:
    sess = _sessions.pop(call_id, None)
    if sess:
        if sess.token:
            identity.revoke(sess.token)
        activity.end(sess.run, status == "done")
    lines = [(str(x.get("who") or ""), " ".join(str(x.get("text") or "").split())) for x in transcript or []]
    lines = [(w, t) for w, t in lines if t]
    async with session_scope() as db:
        agent = await _voice_agent(db)
        agent_id = agent.id
        row = await db.get(VoiceCall, call_id)
        if not row:
            row = VoiceCall(id=call_id, direction=direction, number=number[:32], owner=bool(sess and sess.owner))
            db.add(row)
        row.status, row.outcome, row.ended_at = status[:16], (outcome or "")[:4000], utcnow()
        if error:
            row.error = error[:2000]
        owner, direction, number, purpose = row.owner, row.direction, row.number, row.purpose
        chat_id = None
        if lines:
            chat = Chat(agent_id=agent.id, title=_title(direction, owner, number)[:120], model=agent.model or get_settings().default_model)
            db.add(chat)
            await db.flush()
            for who, text in lines:
                db.add(ChatMessage(chat_id=chat.id, role="assistant" if who == "stella" else "user", text=text,
                                   parts=[{"t": "text", "text": text}] if who == "stella" else [], meta={"voice": call_id}))
            chat_id = row.chat_id = chat.id
        if lines and not owner:
            await agent_svc.send(db, from_id=agent.id, to=USER, kind="notice", title=f"{agent.name}: Gespräch mit {number or 'unbekannt'}",
                                 text=f"{outcome or 'Gespräch beendet.'}\nDie Mitschrift steht im Chat „{_title(direction, owner, number)}“.")
    if lines and owner:
        used = sess.used if sess else []
        jobs.submit(f"voice summary {call_id}", lambda: _evaluate(agent_id, chat_id, direction, purpose, lines, used))
    return {"ok": True, "chat_id": chat_id}


async def _evaluate(agent_id: str, chat_id: str, direction: str, purpose: str, lines: list[tuple[str, str]], used: list[str]) -> None:
    """After a conversation with the user: the agent's normal model writes the summary and keeps what matters."""
    from .runs import run_agent

    talk = "\n".join(f"{'Nutzer' if w != 'stella' else 'Du'}: {t}" for w, t in lines)[-12000:]
    kind = "Live-Gespräch in der App" if direction == "web" else "Telefonat"
    prompt = (f"Du hattest gerade ein {kind} mit dem Nutzer (Gemini Live hat für dich gesprochen). "
              + (f"Anlass: {purpose}\n" if purpose else "")
              + (f"Im Gespräch schon ausgeführte Tools: {', '.join(used)} – NICHT nochmal ausführen.\n" if used else "")
              + f"\nMitschrift:\n{talk}\n\n"
              "Aufgaben:\n1. Speichere mit memory_save nur dauerhaft Wichtiges, das neu ist (Fakten, Vorlieben, Pläne, Entscheidungen) – "
              "nichts Flüchtiges, nichts doppelt.\n2. Hat der Nutzer etwas verlangt, das im Gespräch NICHT erledigt wurde, gib es mit "
              "agent_delegate an Ida ab (nur, was er klar wollte).\n3. Deine Endausgabe ist nur die Zusammenfassung in 2–4 Sätzen "
              "(was besprochen wurde, was erledigt ist, was offen ist) – sie steht danach im Chat.")
    try:
        out = await run_agent(agent_id, prompt)
    except Exception as e:  # noqa: BLE001
        log.exception("voice evaluation failed")
        from .claude.runner import RunResult
        out = RunResult(ok=False, text=str(e), tools=[])
    await agent_svc.record_run(agent_id, "Gespräch", out.ok, out.text, out.tools, out.account)
    if chat_id and out.text:
        async with session_scope() as db:
            text = out.text if out.ok else f"(Auswertung fehlgeschlagen: {out.text[:300]})"
            db.add(ChatMessage(chat_id=chat_id, role="assistant", text=text, parts=[{"t": "text", "text": text}],
                               meta={"voice_summary": True, "account": out.account}))


def live_calls() -> list[str]:
    return list(_sessions)


async def recent(limit: int = 10) -> list[dict]:
    async with session_scope() as db:
        rows = (await db.scalars(select(VoiceCall).order_by(VoiceCall.started_at.desc()).limit(max(1, min(limit, 50))))).all()
    return [call_view(r) for r in rows]


def call_view(r: VoiceCall) -> dict:
    return {"id": r.id, "richtung": {"out": "ausgehend", "in": "eingehend", "web": "App"}.get(r.direction, r.direction),
            "nummer": r.number, "du": r.owner, "status": r.status, "anlass": r.purpose[:300], "ergebnis": r.outcome[:500],
            "fehler": r.error[:300] or None, "chat_id": r.chat_id, "start": r.started_at.isoformat(),
            "ende": r.ended_at.isoformat() if r.ended_at else None}


def check_secret(given: str | None) -> None:
    import hmac
    s = get_settings()
    if not s.voice_secret or not given or not hmac.compare_digest(given, s.voice_secret):
        raise Invalid("voice: falsches Geheimnis")
