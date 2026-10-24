"""Command line tools.

  python -m app.cli import-legacy <ordner>   übernimmt Daten des alten Node-Prototyps (legacy/node-app):
      connectors.json + tools.json, agents.json + main-profile.json, skills.json, routines.json, messages.json,
      memory.db (SQLite) oder memory.json. Bereits vorhandene Einträge (gleiche ID) werden übersprungen.
  python -m app.cli set-password <passwort>
"""
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .main import migrate
from .models import Agent, AgentConnector, AgentSkill, Connector, MemoryEntry, Message, Routine, Skill
from .seed import seed
from .services.skills import slugify

PALETTES = ["gelb", "lila", "orange", "pink", "mint"]


def _read(folder: Path, name: str, default):
    try:
        return json.loads((folder / name).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return default


def _dt(v) -> datetime:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


async def import_legacy(folder: Path) -> None:
    s = get_settings()
    await asyncio.to_thread(migrate)
    await seed()
    report: dict[str, int] = {}
    count = lambda k: report.__setitem__(k, report.get(k, 0) + 1)  # noqa: E731
    async with session_scope() as db:
        main = await db.scalar(select(Agent).where(Agent.is_main.is_(True)))
        # connectors + tool lists
        tools = _read(folder, "tools.json", {})
        for name, srv in (_read(folder, "connectors.json", {}).get("mcpServers") or {}).items():
            cid = name.lower()[:40]
            if await db.get(Connector, cid):
                continue
            t = tools.get(name) or {}
            all_tools = t.get("all", [])
            db.add(Connector(id=cid, name=name, url=srv["url"], headers=srv.get("headers") or {}, tools=all_tools,
                             disabled_tools=[x for x in all_tools if x not in t.get("allowed", all_tools)], status="ok" if all_tools else "new"))
            await db.flush()
            db.add(AgentConnector(agent_id=main.id, connector_id=cid, tools=None))
            count("connectors")
        # main agent profile
        prof = _read(folder, "main-profile.json", {})
        for k in ("personality", "instructions"):
            if prof.get(k) and not getattr(main, k):
                setattr(main, k, prof[k])
        # agents
        id_map = {"main": main.id}
        for i, a in enumerate(_read(folder, "agents.json", [])):
            aid = slugify(a["id"])[:24]
            id_map[a["id"]] = aid
            if await db.get(Agent, aid):
                continue
            db.add(Agent(id=aid, name=a["name"], description=a.get("role", "")[:120], intro=a.get("role", "")[:300], role=a.get("role", ""),
                         personality=a.get("personality", ""), instructions=a.get("instructions", ""), notes=a.get("notes", ""),
                         palette=PALETTES[i % len(PALETTES)], model=a.get("model") if a.get("model") in s.model_list else s.default_model,
                         fallback=a.get("fallback", True), memory_enabled=a.get("memory", True), enabled=a.get("enabled", True), position=10 + i))
            await db.flush()
            for conn, allowed in (a.get("tools") or {}).items():
                if allowed and await db.get(Connector, conn.lower()):
                    db.add(AgentConnector(agent_id=aid, connector_id=conn.lower(), tools=allowed))
            count("agents")
        await db.flush()
        # skills
        for k in _read(folder, "skills.json", []):
            if await db.get(Skill, k["name"]):
                continue
            db.add(Skill(id=k["name"], name=k["name"].capitalize(), description=k.get("description", ""), body=k["instructions"], enabled=k.get("enabled", True)))
            await db.flush()
            for ag in [main.id] + [id_map[x["id"]] for x in _read(folder, "agents.json", []) if k["name"] in (x.get("skills") or [])]:
                if not await db.get(AgentSkill, (ag, k["name"])):
                    db.add(AgentSkill(agent_id=ag, skill_id=k["name"]))
            count("skills")
        # routines
        for r in _read(folder, "routines.json", []):
            if await db.get(Routine, r["id"]):
                continue
            trig = r.get("trigger")
            if trig:
                trig = {"type": "mcp", "connector": trig["connector"].lower(), "tool": trig["tool"], "args": trig.get("args") or {},
                        "every_sec": trig.get("checkEverySec") or (trig.get("checkEveryMin") or 5) * 60, "mode": trig.get("mode", "new"),
                        "contains": trig.get("contains", ""), "state": trig.get("state") or {}}
            db.add(Routine(id=r["id"], name=r["name"], prompt=r["prompt"], agent_id=id_map.get(r.get("agent", "main"), main.id),
                           model=r.get("model") if r.get("model") in s.model_list else None, interval_min=r.get("intervalMin", 60), trigger=trig,
                           fallback=r.get("fallback"), memory_enabled=r.get("memory"), enabled=False))  # paused: check before switching on
            count("routines (pausiert)")
        # messages
        for m in _read(folder, "messages.json", []):
            if await db.get(Message, m["id"]):
                continue
            db.add(Message(id=m["id"], from_id=id_map.get(m["from"], m["from"]), to_id=id_map.get(m["to"], m["to"]), kind=m.get("kind", "message"),
                           priority=m.get("priority", "normal"), text=m["text"], expect_reply=m.get("expectReply", False), depth=m.get("depth", 0),
                           at=_dt(m["at"]), read_at=_dt(m["readAt"]) if m.get("readAt") else None))
            count("messages")
        # memory
        rows = []
        if (folder / "memory.db").exists():
            con = sqlite3.connect(folder / "memory.db")
            con.row_factory = sqlite3.Row
            rows = [dict(x) | {"by": x["by_who"], "updatedAt": x["updated_at"], "lastUsed": x["last_used"]} for x in con.execute("SELECT * FROM entries")]
            con.close()
        else:
            for scope, items in _read(folder, "memory.json", {}).items():
                rows += [dict(e, scope=scope) for e in items]
        for e in rows:
            if await db.get(MemoryEntry, e["id"]):
                continue
            db.add(MemoryEntry(id=e["id"], scope=id_map.get(e["scope"], e["scope"]), text=e["text"], type=e.get("type") or "fact",
                               pinned=bool(e.get("pinned")), importance=e.get("importance") or 3, by=id_map.get(e.get("by"), e.get("by") or "user"),
                               at=_dt(e["at"]), updated_at=_dt(e.get("updatedAt") or e["at"]), expires=_dt(e["expires"]) if e.get("expires") else None,
                               uses=e.get("uses") or 0))
            count("memory")
    print("Import fertig:", ", ".join(f"{k}: {v}" for k, v in report.items()) or "nichts Neues")
    print("Claude-Logins werden nicht übernommen: unter Einstellungen -> Claude-Accounts .credentials.json hochladen.")


async def set_password(pw: str) -> None:
    from .security import _hash
    from .services import settings_store
    await asyncio.to_thread(migrate)
    async with session_scope() as db:
        await settings_store.put(db, "password_hash", _hash(pw))
    print("Passwort gesetzt (APP_PASSWORD in der .env hat Vorrang, falls gesetzt).")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "import-legacy":
        asyncio.run(import_legacy(Path(sys.argv[2])))
    elif len(sys.argv) >= 3 and sys.argv[1] == "set-password":
        asyncio.run(set_password(sys.argv[2]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
