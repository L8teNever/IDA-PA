"""geraet_*-Tools: Geräte mit ida-connect über IDA Health (siehe services/health_api.py, docs/HEALTH.md).

Wer darf: Ida (Haupt-Agent) im Chat – alle Geräte; Code-Threads – nur die Geräte, die beim Projekt gewählt sind.
Lesen geht immer. Befehle, Schreiben, Docker-Aktionen und Updates brauchen im Code-Thread (Rechte „Nachfragen“/„Bearbeiten“)
die Freigabe des Nutzers; bei „Voller Zugriff“ laufen sie direkt. Im Chat gilt die Regel „nur nach klarem Auftrag“ (steht im Tool).
"""
import time

from ...db import session_scope
from ...models import Chat, CodeProject
from .. import health_api
from ..code import approvals
from ..errors import Invalid

E = {"type": "string"}
DEVICES = "devices"
WRITE = {"geraet_befehl", "geraet_datei_schreiben", "geraet_docker_aktion", "geraet_update"}


def enabled() -> bool:
    return health_api.enabled()


async def load() -> None:
    await health_api.load()


def spec() -> list[tuple[str, str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    dev = {**E, "description": "Gerät: id oder Name aus geraete_liste"}
    pre = "Gerät mit ida-connect (IDA Health). "
    act = "Ändert etwas auf dem echten Gerät: nur nach klarem Auftrag des Nutzers. "
    return [
        (DEVICES, "geraete_liste", pre + "Listet alle Geräte in IDA Health mit online/offline, System, CPU, RAM, Temperatur, Platten und Mesh-IP. "
                                         "Im Code-Thread nur die Geräte dieses Projekts.", obj({})),
        (DEVICES, "geraet_status", pre + "Details eines Geräts: Hardware, System, aktuelle Werte und der Verlauf (CPU/RAM/Temperatur) der letzten Zeit.",
         obj({"geraet": dev, "stunden": {"type": "integer", "minimum": 1, "maximum": 720, "description": "Verlauf über so viele Stunden, Standard 1"}}, ("geraet",))),
        (DEVICES, "geraet_befehl", pre + act + "Führt einen Shell-Befehl (sh -c) als root auf dem Gerät aus – wie SSH. Für Nachsehen, Einrichten, Neustarten von Diensten.",
         obj({"geraet": dev, "befehl": E, "timeout_s": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "Standard 60"},
              "user": {**E, "description": "optional: als dieser Benutzer statt root"}}, ("geraet", "befehl"))),
        (DEVICES, "geraet_dateien", pre + "Listet einen Ordner auf dem Gerät (Name, Typ d/f/l, Größe, Änderung, Rechte).",
         obj({"geraet": dev, "pfad": {**E, "description": "absoluter Pfad, Standard /"}}, ("geraet",))),
        (DEVICES, "geraet_datei_lesen", pre + "Liest eine Textdatei auf dem Gerät (bis 2 MB).", obj({"geraet": dev, "pfad": E}, ("geraet", "pfad"))),
        (DEVICES, "geraet_datei_schreiben", pre + act + "Schreibt eine Textdatei auf dem Gerät (ganzer neuer Inhalt, atomar, Rechte bleiben).",
         obj({"geraet": dev, "pfad": E, "inhalt": E}, ("geraet", "pfad", "inhalt"))),
        (DEVICES, "geraet_docker", pre + "Docker auf dem Gerät: Compose-Stacks (mit Compose-Dateien) und alle Container mit Zustand.", obj({"geraet": dev}, ("geraet",))),
        (DEVICES, "geraet_docker_aktion", pre + act + "Aktion für einen Compose-Stack (stack: update = pull + up -d, up, restart, stop, start, down) "
                                                        "oder einen Container (container: start, stop, restart). Wartet, bis es fertig ist.",
         obj({"geraet": dev, "stack": {**E, "description": "Name des Stacks"}, "container": {**E, "description": "oder Name des Containers"},
              "aktion": E}, ("geraet", "aktion"))),
        (DEVICES, "geraet_docker_logs", pre + "Logs eines Containers auf dem Gerät (letzte Zeilen, optional gefiltert).",
         obj({"geraet": dev, "container": E, "tail": {"type": "integer", "minimum": 10, "maximum": 5000}, "grep": {**E, "description": "nur Zeilen mit diesem Text"}},
             ("geraet", "container"))),
        (DEVICES, "geraet_update", pre + act + "Updates: ziel=system installiert alle Paket-Updates (apt/dnf/apk/pacman, dauert), ziel=agent aktualisiert ida-connect selbst.",
         obj({"geraet": dev, "ziel": {"type": "string", "enum": ["system", "agent"]}}, ("geraet", "ziel"))),
    ]


async def _scope(me) -> tuple[list[str] | None, str, str]:
    """(erlaubte Geräte-ids oder None = alle, Rechte-Modus, Projektname) des Aufrufers."""
    if not me.code:
        return None, "full", ""
    async with session_scope() as db:
        chat = await db.get(Chat, me.chat_id) if me.chat_id else None
        project = await db.get(CodeProject, chat.project_id) if chat and chat.project_id else None
        if not project:
            raise Invalid("geraet_* gibt es im Code-Bereich nur in Threads eines Projekts")
        return list(project.devices or []), chat.permission_mode, project.name


async def _resolve(me, key: str) -> tuple[dict, str]:
    allowed, mode, pname = await _scope(me)
    d = await health_api.device(key)
    if allowed is not None and d["id"] not in allowed:
        if not allowed:
            raise Invalid(f"Für das Projekt „{pname}“ ist kein Gerät gewählt – der Nutzer stellt das im Projekt-Menü ein (Geräte wählen).")
        raise Invalid(f"Gerät „{d['name']}“ gehört nicht zum Projekt „{pname}“")
    return d, mode


async def _ok(me, mode: str, title: str, info: dict) -> None:
    if not me.code or mode == "full":
        return
    decision = await approvals.ask(me.chat_id, title, info)
    if decision.get("behavior") != "allow":
        raise Invalid("Der Nutzer hat das abgelehnt.")


async def call(me, name: str, a: dict) -> dict:
    if name == "geraete_liste":
        allowed, _, _ = await _scope(me)
        devs = [health_api.summary(d) for d in await health_api.devices() if allowed is None or d["id"] in allowed]
        return {"geraete": devs} if devs else {"geraete": [], "hinweis": "Keine Geräte" + (" für dieses Projekt gewählt" if allowed is not None else " in IDA Health")}
    d, mode = await _resolve(me, str(a.get("geraet") or ""))
    dev = d["id"]
    if name == "geraet_status":
        hours = max(1, min(int(a.get("stunden") or 1), 720))
        h = await health_api.history(dev, hours * 3600)
        pick = lambda k: [v for v in h.get(k) or [] if v is not None]  # noqa: E731
        stat = lambda k: {"min": min(pick(k)), "mittel": round(sum(pick(k)) / len(pick(k)), 1), "max": max(pick(k))} if pick(k) else None  # noqa: E731
        return {**health_api.summary(d), "info": d.get("info"), "zuletzt_gesehen": d.get("last_seen"),
                "verlauf": {"stunden": hours, "cpu_prozent": stat("cpu"), "cpu_spitze": stat("cpu_max"), "temperatur_c": stat("temp_max"),
                            "load1": stat("load1")}, "jetzt": int(time.time())}
    if name == "geraet_befehl":
        cmd = str(a.get("befehl") or "")
        await _ok(me, mode, f"Gerät: Befehl auf {d['name']}", {"geraet": d["name"], "befehl": cmd})
        return await health_api.exec_cmd(dev, cmd, int(a.get("timeout_s") or 60), str(a.get("user") or ""))
    if name == "geraet_dateien":
        return await health_api.fs_list(dev, str(a.get("pfad") or "/"))
    if name == "geraet_datei_lesen":
        return await health_api.fs_read(dev, str(a.get("pfad") or ""))
    if name == "geraet_datei_schreiben":
        path, content = str(a.get("pfad") or ""), str(a.get("inhalt") or "")
        await _ok(me, mode, f"Gerät: {path} auf {d['name']} schreiben", {"geraet": d["name"], "pfad": path, "inhalt": content[:4000]})
        return await health_api.fs_write(dev, path, content)
    if name == "geraet_docker":
        return await health_api.docker(dev)
    if name == "geraet_docker_aktion":
        act, stack, ctr = str(a.get("aktion") or ""), str(a.get("stack") or ""), str(a.get("container") or "")
        if bool(stack) == bool(ctr):
            raise Invalid("Gib genau eins an: stack oder container")
        await _ok(me, mode, f"Gerät: {act} {stack or ctr} auf {d['name']}", {"geraet": d["name"], "stack": stack, "container": ctr, "aktion": act})
        return await (health_api.stack_action(dev, stack, act) if stack else health_api.container_action(dev, ctr, act))
    if name == "geraet_docker_logs":
        r = await health_api.container_logs(dev, str(a.get("container") or ""), int(a.get("tail") or 200))
        g = str(a.get("grep") or "").lower()
        if g:
            r["logs"] = "\n".join(x for x in r["logs"].splitlines() if g in x.lower())
        return r
    if name == "geraet_update":
        target = str(a.get("ziel") or "system")
        await _ok(me, mode, f"Gerät: Update ({target}) auf {d['name']}", {"geraet": d["name"], "ziel": target})
        return await health_api.update(dev, target)
    raise Invalid(f"Tool {name} gibt es nicht")
