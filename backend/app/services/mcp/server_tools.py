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
    stk = {**E, "description": "Stack-Name; nur nötig, wenn das Projekt mehrere Server-Stacks hat"}
    datei = {**E, "description": "Dateiname (z. B. compose.yaml, .env) oder voller Pfad aus server_dateien; leer = die Compose-Datei"}
    pre = "Nur wenn das Projekt einen Server-Stack hat (steht im Prompt). "
    tools = [
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
        (CODE, "server_dateien", pre + "Listet die Dateien des Stacks auf dem Server, die man bearbeiten darf (Compose-Dateien und .env), mit Pfad.",
         obj({})),
        (CODE, "server_datei_lesen", pre + "Liest eine Datei des Stacks auf dem Server (Compose-Datei oder .env).", obj({"datei": datei})),
        (CODE, "server_datei_schreiben", pre + "Schreibt eine Datei des Stacks auf dem Server (ganzer neuer Inhalt). Kaputtes YAML wird abgelehnt, "
                                               "der alte Stand wird gesichert. Wirkt erst nach server_aktion up.",
         obj({"datei": datei, "inhalt": E}, ("inhalt",))),
    ]
    for _, _, _, schema in tools:  # bei mehreren Stacks: welcher
        schema.setdefault("properties", {})["stack"] = stk
    return tools


async def _context(me, wanted: str = "") -> tuple[str, str]:
    """(Stack-Name, Rechte-Modus) des Code-Threads; bei mehreren Stacks der genannte."""
    async with session_scope() as db:
        chat = await db.get(Chat, me.chat_id) if me.chat_id else None
        project = await db.get(CodeProject, chat.project_id) if chat and chat.project_id else None
        if not project:
            raise Invalid("server_* gibt es nur in Code-Threads")
        from ..code.projects import stacks_of  # spät: projects hängt am Chat-Dienst
        stacks = stacks_of(project)
        if not stacks:
            raise Invalid(f"Für das Projekt „{project.name}“ ist kein Server-Stack gewählt – der Nutzer stellt das im Projekt-Menü ein (Server-Stacks wählen).")
        wanted = (wanted or "").strip()
        if wanted:
            if wanted not in stacks:
                raise Invalid(f"Stack „{wanted}“ gehört nicht zum Projekt – erlaubt: " + ", ".join(stacks))
            return wanted, chat.permission_mode
        if len(stacks) > 1:
            raise Invalid("Das Projekt hat mehrere Server-Stacks – gib stack an: " + ", ".join(stacks))
        return stacks[0], chat.permission_mode


async def _ok(me, mode: str, title: str, info: dict) -> None:
    if mode == "full":
        return
    decision = await approvals.ask(me.chat_id, title, info)
    if decision.get("behavior") != "allow":
        raise Invalid("Der Nutzer hat das abgelehnt.")


async def call(me, name: str, a: dict) -> dict:
    stack, mode = await _context(me, str(a.get("stack") or ""))
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
    if name == "server_dateien":
        return await server_stack.files(stack)
    if name == "server_datei_lesen":
        return await server_stack.read_file(stack, str(a.get("datei") or ""))
    if name == "server_datei_schreiben":
        content = str(a.get("inhalt") or "")
        f = str(a.get("datei") or "") or "Compose-Datei"
        await _ok(me, mode, f"Server: {f} in {stack} schreiben", {"stack": stack, "datei": f, "inhalt": content[:4000]})
        return await server_stack.write_file(stack, str(a.get("datei") or ""), content)
    raise Invalid(f"Tool {name} gibt es nicht")
