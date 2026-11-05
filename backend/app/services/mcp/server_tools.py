"""server_*-Tools der Code-Threads: Docker-Stack des Projekts auf dem Server über das Dashboard (siehe services/server_stack.py).

Lesen (Status, Logs) geht immer. Befehle in einem Container und Aktionen wie „Aktualisieren“ brauchen im Rechte-Modus
„Nachfragen“ und „Bearbeiten“ die Freigabe des Nutzers im Thread; bei „Voller Zugriff“ laufen sie direkt.
"""
from ...db import session_scope
from ...models import Chat, CodeProject
from .. import server_stack
from ..code import approvals
from ..errors import Invalid

E = {"type": "string"}
CODE = "code"


def enabled() -> bool:
    return server_stack.enabled()


def spec() -> list[tuple[str, str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    svc = {**E, "description": "Dienst (service) im Stack, z. B. app; bei nur einem Dienst weglassen"}
    pre = "Nur wenn das Projekt einen Server-Stack hat (steht im Prompt). "
    return [
        (CODE, "server_status", pre + "Zeigt den Docker-Stack dieses Projekts auf dem Server: Dienste, Zustand, Image, Health und öffentliche URLs "
                                      "(mit Schutz: access = Cloudflare Zero Trust, public = offen).", obj({})),
        (CODE, "server_logs", pre + "Logs eines Dienstes im Server-Stack (die letzten Zeilen, optional gefiltert).",
         obj({"service": svc, "tail": {"type": "integer", "minimum": 10, "maximum": 5000, "description": "so viele Zeilen, Standard 200"},
              "grep": {**E, "description": "nur Zeilen mit diesem Text (ohne Groß/klein)"}})),
        (CODE, "server_befehl", pre + "Führt einen Shell-Befehl (sh -c) IM laufenden Container eines Dienstes aus – wie ein Terminal auf dem "
                                      "Server, ohne SSH. Für Nachsehen, Prüfen, Migrationen u. Ä. Achtung: das ist der echte Server, keine Sandbox.",
         obj({"befehl": E, "service": svc, "timeout_s": {"type": "integer", "minimum": 1, "maximum": 600, "description": "Standard 60"},
              "user": {**E, "description": "optional: als dieser Benutzer im Container (z. B. root)"}}, ("befehl",))),
        (CODE, "server_aktion", pre + "Aktion für den ganzen Server-Stack: update = neue Images holen und neu starten (nach einem Push, wenn das "
                                      "Image gebaut ist), restart, start, stop, up = Compose-Datei übernehmen. Wartet, bis es fertig ist.",
         obj({"aktion": {"type": "string", "enum": list(server_stack.ACTIONS)}}, ("aktion",))),
    ]


async def _context(me) -> tuple[str, str]:
    """(Stack-Name, Rechte-Modus) des Code-Threads."""
    async with session_scope() as db:
        chat = await db.get(Chat, me.chat_id) if me.chat_id else None
        project = await db.get(CodeProject, chat.project_id) if chat and chat.project_id else None
        if not project:
            raise Invalid("server_* gibt es nur in Code-Threads")
        if not project.server_stack:
            raise Invalid(f"Für das Projekt „{project.name}“ ist kein Server-Stack gewählt – der Nutzer stellt das im Projekt-Menü ein (Server-Stack wählen).")
        return project.server_stack, chat.permission_mode


async def _ok(me, mode: str, title: str, info: dict) -> None:
    if mode == "full":
        return
    decision = await approvals.ask(me.chat_id, title, info)
    if decision.get("behavior") != "allow":
        raise Invalid("Der Nutzer hat das abgelehnt.")


async def call(me, name: str, a: dict) -> dict:
    stack, mode = await _context(me)
    if name == "server_status":
        return await server_stack.status(stack)
    if name == "server_logs":
        return await server_stack.logs(stack, a.get("service"), int(a.get("tail") or 200), str(a.get("grep") or ""))
    if name == "server_befehl":
        cmd = str(a.get("befehl") or "")
        await _ok(me, mode, f"Server: Befehl in {stack}", {"service": a.get("service") or "", "befehl": cmd})
        return await server_stack.exec_cmd(stack, a.get("service"), cmd, int(a.get("timeout_s") or 60), str(a.get("user") or ""))
    if name == "server_aktion":
        act = str(a.get("aktion") or "")
        await _ok(me, mode, f"Server: {server_stack.ACTIONS.get(act, act)} – {stack}", {"stack": stack, "aktion": act})
        return await server_stack.action(stack, act)
    raise Invalid(f"Tool {name} gibt es nicht")
