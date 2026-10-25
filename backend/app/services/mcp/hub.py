"""Internal MCP server for running agents (reached by the CLI at /mcp/{token}).

Rights depend on the caller (see identity.Identity):
  main agent:  everything; managing agents/skills only in a live chat.
  sub-agents:  messages, notifications, own notes, see all routines, edit/create only their own routines.
  chat_*:      every agent may search and read all chats (also those of other agents) for context.
  memory_*:    only when memory is switched on for the run.
Triggers and connector access are never settable here – only by the user in the UI.
"""
import json

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db import session_scope
from ...models import USER, Agent, Chat, ChatMessage, Routine, RoutineRun, Skill
from .. import agents as agent_svc
from .. import antigravity
from ..claude import models as claude_models
from .. import reminders as reminder_svc
from .. import routines as routine_svc
from .. import skills as skill_svc
from ..claude import prompts
from ..errors import Invalid, NotFound
from ..memory import store as memory
from .identity import Identity, get

E = {"type": "string"}
ALL, MAIN, MAIN_LIVE = "all", "main", "main_live"


def _spec(ids: list[str]) -> list[tuple[str, str, str, dict]]:
    s = get_settings()
    to = {"type": "string", "description": "Empfänger: user (der Nutzer) oder eine Agent-ID: " + ", ".join(ids)}
    model = {"type": "string", "enum": claude_models.ids() + antigravity.cached_models(),
             "description": "Claude-Modelle laufen auf Claude-Accounts, agy:… auf Antigravity-Accounts"}
    agent_enum = {"type": "string", "enum": ids, "description": "Besitzer-Agent der Routine"}
    on_home = {"type": "boolean", "description": "Im Tagesplan auf der Startseite zeigen? Nur, was der Nutzer dort sehen will (Termine/Aufgaben für ihn) – "
               "Hintergrund-Checks nicht. Standard: Routinen nein, Erinnerungen ja"}
    routine_fields = {"name": E, "prompt": {**E, "description": "Vollständige Aufgabe, die bei jedem Lauf ausgeführt wird (muss allein verständlich sein)"},
                      "interval_min": {"type": "integer", "minimum": 1, "description": "Intervall in Minuten (statt times)"},
                      "times": {"type": "array", "items": E, "description": f"Feste Uhrzeiten HH:MM ({get_settings().timezone}), z. B. [\"07:00\"] – statt interval_min"},
                      "days": {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 6}, "description": "Wochentage zu times: 0=Mo … 6=So, leer = täglich"},
                      "on_demand": {"type": "boolean", "description": "true = ohne Zeitplan: läuft nur, wenn sie per routine_run_now (oder vom Nutzer) gestartet wird – z. B. als fester Ablauf, den du bei Bedarf auslöst"},
                      "model": model,
                      "memory_enabled": {"type": "boolean"}, "fallback": {"type": "boolean"}, "show_on_home": on_home}
    when = {**E, "description": f"Zeitpunkt JJJJ-MM-TTTHH:MM in Ortszeit ({s.timezone}), z. B. 2026-10-26T09:00 – rechne „morgen“, „Freitag“ usw. mit der aktuellen Uhrzeit um"}
    reminder_fields = {"text": {**E, "description": "Worum es geht – bei kind=remind steht genau das in der Benachrichtigung, bei task ist es die Aufgabe (muss allein verständlich sein)"},
                       "when": when,
                       "kind": {"type": "string", "enum": ["remind", "task"], "description": "remind = nur erinnern (Benachrichtigung, keine KI); task = der Agent erledigt es zu dem Zeitpunkt"},
                       "agent_id": {**agent_enum, "description": "wer es erledigt (nur bei task wichtig)"}, "show_on_home": on_home}
    mem_scope = {"type": "string", "description": "own = dein eigenes, shared = gemeinsames Gedächtnis; der Haupt-Agent zusätzlich eine Agent-ID (fremdes Gedächtnis!)"}
    mem_type = {"type": "string", "enum": ["fact", "preference", "rule", "event"]}
    mem_exp = {"type": "integer", "minimum": 0, "maximum": 3650, "description": "nur so viele Tage gültig (0 = immer)"}
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    return [
        (ALL, "agent_list", "Listet alle Agenten mit Aufgabe und Modell.", obj({})),
        (ALL, "agent_send", "Schickt eine Nachricht an einen Agenten. Nur senden, was nötig ist; priority=high nur für Wichtiges.", obj({"to": to, "text": E, "priority": {"type": "string", "enum": ["normal", "high"]}}, ("to", "text"))),
        (ALL, "agent_delegate", "Gibt einem Agenten eine Aufgabe. Sein Ergebnis kommt automatisch als Nachricht zurück (asynchron).", obj({"to": to, "task": E}, ("to", "task"))),
        (ALL, "notify_user", "Benachrichtigung an den Nutzer (erscheint in der App unter Infos/Nachrichten). Nur für Wichtiges, kein Spam.", obj({"text": E, "title": {**E, "description": "kurze Überschrift"}, "priority": {"type": "string", "enum": ["normal", "high"]}}, ("text",))),
        (ALL, "inbox_read", "Liest deine ungelesenen Nachrichten und markiert sie als gelesen.", obj({})),
        (ALL, "notes_get", "Liest deine Notizen (Arbeitszettel zwischen Läufen).", obj({})),
        (ALL, "notes_set", "Ersetzt deine Notizen (max. 4000 Zeichen).", obj({"text": E}, ("text",))),
        (ALL, "reminder_create", "Für alles, was EINMAL passieren soll: „erinnere mich morgen um 9 an …“ (kind=remind) oder „mach das am Freitag um 8“ (kind=task). Feuert genau einmal, keine Routine.", obj(reminder_fields, ("text", "when"))),
        (ALL, "reminder_list", "Listet offene und kürzlich erledigte Erinnerungen.", obj({})),
        (ALL, "reminder_update", "Ändert eine Erinnerung (Text, Zeitpunkt, Art).", obj({"id": E, **reminder_fields}, ("id",))),
        (ALL, "reminder_delete", "Löscht eine Erinnerung.", obj({"id": E}, ("id",))),
        (ALL, "routine_list", "Listet alle Routinen mit Besitzer, Zeitplan/Auslöser und letztem Ergebnis.", obj({})),
        (ALL, "routine_create", "Legt eine Routine an – nur für Dinge, die sich WIEDERHOLEN (Intervall, feste Uhrzeiten, Auslöser oder auf Abruf). Einmaliges -> reminder_create. Sub-Agenten nur für sich selbst.", obj({**routine_fields, "agent_id": agent_enum}, ("name", "prompt"))),
        (ALL, "routine_update", "Ändert eine Routine (Sub-Agenten nur eigene; Zuteilen nur der Haupt-Agent).", obj({"id": E, **routine_fields, "enabled": {"type": "boolean"}, "agent_id": agent_enum}, ("id",))),
        (MAIN, "routine_delete", "Löscht eine Routine.", obj({"id": E}, ("id",))),
        (ALL, "routine_run_now", "Führt eine Routine sofort aus (Sub-Agenten nur eigene). So startest du auch Routinen „nur auf Abruf“. Optional info: zusätzlicher Hinweis für diesen Lauf.", obj({"id": E, "info": E}, ("id",))),
        (ALL, "chat_search", "Durchsucht alle Chats (auch die der anderen Agenten) nach Wörtern – um Zusammenhang aus früheren Gesprächen zu finden. Ohne query: die neuesten Chats.", obj({"query": {**E, "description": "Suchwörter; alle müssen vorkommen"}, "agent_id": {**E, "description": "nur Chats dieses Agenten (optional): " + ", ".join(ids)}, "limit": {"type": "integer", "minimum": 1, "maximum": 30}})),
        (ALL, "chat_read", "Liest die Nachrichten eines Chats (id aus chat_search), neueste zuletzt. Mit offset ältere Teile nachladen.", obj({"chat_id": E, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "offset": {"type": "integer", "minimum": 0, "description": "so viele der neuesten Nachrichten überspringen"}}, ("chat_id",))),
        (ALL, "skill_list", "Listet alle Skills.", obj({})),
        (ALL, "skill_get", "Lädt die Anleitung eines Skills.", obj({"id": E}, ("id",))),
        (MAIN_LIVE, "skill_create", "Legt einen Skill an (Anleitung, die Agenten bei passenden Aufgaben befolgen; der Nutzer aktiviert ihn mit /id).", obj({"name": E, "description": E, "body": E, "agents": {"type": "array", "items": E}}, ("name", "body"))),
        (MAIN_LIVE, "skill_update", "Ändert einen Skill.", obj({"id": E, "name": E, "description": E, "body": E, "enabled": {"type": "boolean"}, "agents": {"type": "array", "items": E}}, ("id",))),
        (MAIN_LIVE, "skill_delete", "Löscht einen Skill.", obj({"id": E}, ("id",))),
        (MAIN_LIVE, "agent_create", "Legt einen Agenten an (ohne Tool-Zugriff; den gibt der Nutzer in den Einstellungen).", obj({"name": E, "description": E, "role": {**E, "description": "Aufgabe als Prompt"}, "personality": E, "instructions": E, "model": model, "palette": {"type": "string", "enum": list(agent_svc.PALETTES)}}, ("name", "role"))),
        (MAIN_LIVE, "agent_update", "Ändert einen Agenten.", obj({"id": E, "name": E, "description": E, "role": E, "personality": E, "instructions": E, "model": model, "enabled": {"type": "boolean"}, "memory_enabled": {"type": "boolean"}, "fallback": {"type": "boolean"}}, ("id",))),
        (MAIN_LIVE, "agent_delete", "Löscht einen Agenten; seine Routinen gehen pausiert an dich.", obj({"id": E}, ("id",))),
        (ALL, "memory_save", "Merkt sich dauerhaft etwas (ein Fakt pro Eintrag). Ähnliches wird aktualisiert statt doppelt angelegt.", obj({"text": E, "scope": mem_scope, "type": mem_type, "pinned": {"type": "boolean"}, "importance": {"type": "integer", "minimum": 1, "maximum": 5}, "expiresInDays": mem_exp}, ("text",))),
        (ALL, "memory_search", "Durchsucht das Gedächtnis (eigenes + gemeinsames; ohne query die wichtigsten/neuesten).", obj({"query": E, "scope": mem_scope, "limit": {"type": "integer", "minimum": 1, "maximum": 30}})),
        (ALL, "memory_update", "Ändert einen Gedächtnis-Eintrag (id aus memory_search/Prompt).", obj({"id": E, "scope": mem_scope, "text": E, "type": mem_type, "pinned": {"type": "boolean"}, "importance": {"type": "integer", "minimum": 1, "maximum": 5}, "expiresInDays": mem_exp}, ("id",))),
        (ALL, "memory_delete", "Löscht einen veralteten oder falschen Eintrag.", obj({"id": E, "scope": mem_scope}, ("id",))),
    ]


def _allowed(who: str, name: str, me: Identity) -> bool:
    if name.startswith("memory_") and not me.memory:
        return False
    if who == ALL:
        return True
    if who == MAIN:
        return me.is_main
    return me.is_main and me.interactive


async def _ids(db: AsyncSession) -> list[str]:
    return list((await db.scalars(select(Agent.id).order_by(Agent.position))).all())


def _routine_view(r: Routine, last: RoutineRun | None) -> dict:
    t = r.trigger
    return {"id": r.id, "name": r.name, "besitzer": r.agent_id, "aufgabe": r.prompt[:300], "enabled": r.enabled, "show_on_home": r.show_on_home,
            "zeitplan": routine_svc.when_text(r), "naechsterLauf": r.next_run.isoformat() if r.next_run else None,
            "model": r.model, "letzterLauf": r.last_run.isoformat() if r.last_run else None,
            "letztesErgebnis": (last.text[:300] if last else None)}


def _snippet(text: str, words: list[str], width: int = 220) -> str:
    low = text.lower()
    pos = min((low.find(w) for w in words if low.find(w) >= 0), default=0)
    start = max(0, pos - width // 3)
    return ("…" if start else "") + text[start:start + width].replace("\n", " ") + ("…" if start + width < len(text) else "")


async def _chat_search(db: AsyncSession, query: str, agent_id: str | None, limit: int, labels: dict) -> dict:
    limit = max(1, min(limit, 30))
    words = [w.lower() for w in query.split() if w.strip()][:8]
    if not words:  # newest chats
        q = select(Chat).order_by(Chat.updated_at.desc()).limit(limit)
        if agent_id:
            q = q.where(Chat.agent_id == agent_id)
        rows = (await db.scalars(q)).all()
        return {"chats": [{"chat_id": c.id, "titel": c.title, "agent": labels.get(c.agent_id, c.agent_id), "zuletzt": c.updated_at.isoformat()} for c in rows]}
    q = (select(ChatMessage, Chat).join(Chat, Chat.id == ChatMessage.chat_id)
         .where(*[or_(func.lower(ChatMessage.text).contains(w), func.lower(Chat.title).contains(w)) for w in words])
         .where(ChatMessage.role.in_(("user", "assistant")))
         .order_by(ChatMessage.at.desc()).limit(limit * 3))
    if agent_id:
        q = q.where(Chat.agent_id == agent_id)
    hits, per_chat = [], {}
    for m, c in (await db.execute(q)).all():
        if per_chat.get(c.id, 0) >= 3 or len(hits) >= limit:
            continue
        per_chat[c.id] = per_chat.get(c.id, 0) + 1
        hits.append({"chat_id": c.id, "titel": c.title, "agent": labels.get(c.agent_id, c.agent_id), "zeit": m.at.isoformat(),
                     "von": "Nutzer" if m.role == "user" else labels.get(c.agent_id, c.agent_id), "ausschnitt": _snippet(m.text, words)})
    return {"treffer": hits, "hinweis": "Mit chat_read(chat_id) den ganzen Zusammenhang lesen." if hits else "Nichts gefunden."}


async def _chat_read(db: AsyncSession, chat_id: str, limit: int, offset: int, labels: dict) -> dict:
    c = await db.get(Chat, chat_id)
    if not c:
        raise NotFound("Chat nicht gefunden")
    limit, offset = max(1, min(limit, 100)), max(0, offset)
    total = await db.scalar(select(func.count()).select_from(ChatMessage).where(ChatMessage.chat_id == chat_id))
    rows = (await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id)
                             .order_by(ChatMessage.at.desc()).offset(offset).limit(limit))).all()
    who = labels.get(c.agent_id, c.agent_id)
    msgs = []
    for m in reversed(rows):
        tools = [p.get("name") for p in (m.parts or []) if p.get("t") == "tool"]
        msgs.append({"zeit": m.at.isoformat(), "von": "Nutzer" if m.role == "user" else who if m.role == "assistant" else m.role,
                     "text": m.text[:3000] + ("…" if len(m.text) > 3000 else ""), **({"tools": tools} if tools else {})})
    return {"chat_id": c.id, "titel": c.title, "agent": who, "nachrichten_gesamt": total,
            "gezeigt": f"{offset + 1}–{offset + len(rows)} von den neuesten", "nachrichten": msgs,
            **({"aelter": f"offset={offset + limit} für ältere"} if offset + limit < (total or 0) else {})}


async def _call(db: AsyncSession, me: Identity, name: str, a: dict) -> dict:
    spec = {n: who for who, n, _, _ in _spec(await _ids(db))}
    if name not in spec or not _allowed(spec[name], name, me):
        return {"error": f"Keine Berechtigung oder unbekanntes Tool: {name}"}
    labels = await prompts.labels(db)

    async def own_routine(rid: str) -> Routine:
        r = await db.get(Routine, rid)
        if not r:
            raise NotFound("Routine nicht gefunden")
        if not me.is_main and r.agent_id != me.agent_id:
            raise Invalid("Keine Berechtigung: nur eigene Routinen")
        return r

    if name == "agent_list":
        rows = (await db.scalars(select(Agent).order_by(Agent.position))).all()
        return {"ich": me.agent_id, "agents": [{"id": x.id, "name": x.name, "aufgabe": (x.description or x.role)[:200], "haupt": x.is_main,
                                                "model": x.model, "aktiv": x.enabled} for x in rows]}
    if name == "agent_send":
        m = await agent_svc.send(db, from_id=me.agent_id, to=a.get("to"), text=a.get("text"), priority=a.get("priority", "normal"), depth=me.depth + 1)
        return {"ok": True, "id": m.id, "an": m.to_id}
    if name == "agent_delegate":
        m = await agent_svc.send(db, from_id=me.agent_id, to=a.get("to"), text=a.get("task"), expect_reply=True, depth=me.depth + 1)
        return {"ok": True, "id": m.id, "an": m.to_id, "hinweis": "Das Ergebnis kommt als Nachricht in dein Postfach."}
    if name == "notify_user":
        m = await agent_svc.send(db, from_id=me.agent_id, to=USER, text=a.get("text"), title=a.get("title", ""), priority=a.get("priority", "normal"), kind="notice", depth=me.depth + 1)
        return {"ok": True, "id": m.id}
    if name == "inbox_read":
        msgs = await agent_svc.unread(db, me.agent_id)
        await agent_svc.mark_read(db, [m.id for m in msgs])
        return {"nachrichten": [{"id": m.id, "von": labels.get(m.from_id, m.from_id), "vonId": m.from_id, "art": m.kind, "prioritaet": m.priority,
                                 "zeit": m.at.isoformat(), "text": m.text} for m in msgs]}
    if name == "notes_get":
        return {"notes": (await agent_svc.get(db, me.agent_id)).notes}
    if name == "notes_set":
        return {"ok": True, "notes": await agent_svc.set_notes(db, me.agent_id, a.get("text", ""))}
    if name == "routine_list":
        out = []
        for r in (await db.scalars(select(Routine).order_by(Routine.created_at))).all():
            last = await db.scalar(select(RoutineRun).where(RoutineRun.routine_id == r.id).order_by(RoutineRun.at.desc()).limit(1))
            out.append(_routine_view(r, last))
        return {"routinen": out}
    if name == "routine_create":
        fields = {k: v for k, v in a.items() if k != "trigger"}
        if not me.is_main:
            if fields.get("agent_id") not in (None, me.agent_id):
                raise Invalid("Keine Berechtigung: Sub-Agenten legen Routinen nur für sich selbst an")
            fields["agent_id"] = me.agent_id
        else:
            fields.setdefault("agent_id", me.agent_id)
        r = await routine_svc.create(db, fields)
        return {"ok": True, "id": r.id, "zeitplan": routine_svc.when_text(r)}
    if name == "routine_update":
        r = await own_routine(str(a.get("id")))
        fields = {k: v for k, v in a.items() if k not in ("id", "trigger")}
        if not me.is_main and fields.get("agent_id") not in (None, me.agent_id):
            raise Invalid("Keine Berechtigung: Zuteilen darf nur der Haupt-Agent")
        await routine_svc.update(db, r.id, fields)
        return {"ok": True}
    if name == "routine_delete":
        await routine_svc.remove(db, str(a.get("id")))
        return {"ok": True}
    if name == "routine_run_now":
        r = await own_routine(str(a.get("id")))
        info = str(a.get("info") or "").strip()
        routine_svc.enqueue(r.id, "manual", f"Gestartet von {me.agent_id}." + (f" Hinweis dazu: {info}" if info else ""))
        return {"ok": True, "hinweis": "In die Warteschlange gestellt."}
    if name == "reminder_create":
        f = {k: a.get(k) for k in ("text", "when", "kind", "agent_id", "show_on_home") if a.get(k) is not None}
        if not me.is_main:
            f["agent_id"] = me.agent_id
        f.setdefault("agent_id", me.agent_id)
        r = await reminder_svc.create(db, f, created_by=me.agent_id)
        return {"ok": True, "id": r.id, "wann": reminder_svc.when_text(r),
                "art": "nur Erinnerung (Benachrichtigung)" if r.kind == "remind" else "Aufgabe – wird dann ausgeführt"}
    if name == "reminder_list":
        return {"erinnerungen": [{"id": r.id, "text": r.text[:300], "art": r.kind, "wann": reminder_svc.when_text(r), "status": r.status,
                                  "agent": r.agent_id, "show_on_home": r.show_on_home, "ergebnis": r.result[:200] or None} for r in await reminder_svc.listing(db)][:50]}
    if name == "reminder_update":
        f = {k: a.get(k) for k in ("text", "when", "kind", "agent_id", "show_on_home") if a.get(k) is not None}
        if not me.is_main:
            f.pop("agent_id", None)
        r = await reminder_svc.update(db, str(a.get("id")), f)
        return {"ok": True, "wann": reminder_svc.when_text(r)}
    if name == "reminder_delete":
        await reminder_svc.remove(db, str(a.get("id")))
        return {"ok": True}
    if name == "chat_search":
        return await _chat_search(db, str(a.get("query") or ""), a.get("agent_id"), int(a.get("limit") or 10), labels)
    if name == "chat_read":
        return await _chat_read(db, str(a.get("chat_id")), int(a.get("limit") or 30), int(a.get("offset") or 0), labels)
    if name == "skill_list":
        rows = (await db.scalars(select(Skill).order_by(Skill.name))).all()
        return {"skills": [{"id": s.id, "name": s.name, "beschreibung": s.description, "aktiv": s.enabled} for s in rows]}
    if name == "skill_get":
        s = await db.get(Skill, str(a.get("id")).lstrip("/"))
        if not s:
            raise NotFound("Skill nicht gefunden")
        return {"id": s.id, "name": s.name, "anleitung": s.body}
    if name == "skill_create":
        s = await skill_svc.create(db, {**a, "agents": a.get("agents") or [me.agent_id]})
        return {"ok": True, "id": s.id}
    if name == "skill_update":
        await skill_svc.update(db, str(a.get("id")), a)
        return {"ok": True}
    if name == "skill_delete":
        await skill_svc.remove(db, str(a.get("id")))
        return {"ok": True}
    if name == "agent_create":
        x = await agent_svc.create(db, a)
        return {"ok": True, "id": x.id, "hinweis": "Tool-Zugriff gibt der Nutzer unter Einstellungen → Agents."}
    if name == "agent_update":
        await agent_svc.update_agent(db, str(a.get("id")), {k: v for k, v in a.items() if k != "id"})
        return {"ok": True}
    if name == "agent_delete":
        await agent_svc.remove(db, str(a.get("id")))
        return {"ok": True}
    if name.startswith("memory_"):
        return await memory.call_tool(db, me.agent_id, me.is_main, name, a, labels)
    return {"error": f"Unbekanntes Tool {name}"}


async def handle(token: str, msg: dict) -> dict | None | bool:
    """JSON-RPC in, response out. None = notification (202), False = unknown token (404)."""
    me = get(token)
    if not me:
        return False
    if msg.get("id") is None:
        return None
    reply = lambda result: {"jsonrpc": "2.0", "id": msg["id"], "result": result}  # noqa: E731
    method = msg.get("method")
    if method == "initialize":
        return reply({"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-03-26"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "ida", "version": "1"}})
    if method == "ping":
        return reply({})
    if method == "tools/list":
        async with session_scope() as db:
            ids = await _ids(db)
        return reply({"tools": [{"name": n, "description": d, "inputSchema": schema} for who, n, d, schema in _spec(ids) if _allowed(who, n, me)]})
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            async with session_scope() as db:
                out = await _call(db, me, p.get("name", ""), p.get("arguments") or {})
                if out.get("error"):
                    raise _ToolError(out)
        except _ToolError as e:
            out = e.out
        except (Invalid, NotFound) as e:
            out = {"error": str(e)}
        return reply({"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}], "isError": bool(out.get("error"))})
    return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "Method not found"}}


class _ToolError(Exception):
    """Rolls the session back for tool calls that returned an error."""

    def __init__(self, out: dict):
        super().__init__(out.get("error"))
        self.out = out
