"""Server-Stack eines Code-Projekts: IDA spricht intern mit dem Dashboard (DASHBOARD_URL, siehe docs/DASHBOARD.md).

Pro Code-Projekt kann der Nutzer einen Docker-Stack seines Servers wählen (CodeProject.server_stack). Die KI im Code-Thread
sieht dann Status und Logs, führt Befehle in den Containern aus und aktualisiert den Stack – alles über die API des
Dashboards, ohne SSH. Leer = aus.
"""
import asyncio

import httpx

from ..config import get_settings
from .errors import Invalid, NotFound

ACTIONS = {"update": "Aktualisieren (pull + up -d)", "restart": "Neu starten", "start": "Starten", "stop": "Stoppen", "up": "Übernehmen (up -d)"}
JOB_WAIT = 900  # so lange (Sekunden) wartet ein Tool höchstens auf einen Vorgang wie „Aktualisieren“
LOG_MAX = 60_000


def enabled() -> bool:
    return bool(get_settings().dashboard_url.strip())


def _base() -> str:
    if not enabled():
        raise Invalid("Das Dashboard ist nicht eingerichtet (DASHBOARD_URL fehlt)")
    return get_settings().dashboard_url.rstrip("/")


async def _req(method: str, path: str, *, timeout: float = 20, **kw) -> dict | list:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, _base() + path, **kw)
    except httpx.HTTPError as e:
        raise Invalid(f"Dashboard nicht erreichbar: {e}") from e
    if r.status_code == 404:
        raise NotFound(_detail(r) or "Im Dashboard nicht gefunden")
    if r.status_code >= 400:
        raise Invalid(_detail(r) or f"Dashboard antwortet {r.status_code}")
    return r.json()


def _detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail") or "")
    except ValueError:
        return r.text[:300]


async def stacks() -> list[dict]:
    """Alle Stacks des Servers, kurz (zum Auswählen im Projekt)."""
    out = await _req("GET", "/api/v1/stacks")
    return [{"name": s["name"], "state": s.get("state"), "running": s.get("running"), "total": s.get("total"),
             "services": sorted({c["service"] for c in s.get("containers") or []}), "self": bool(s.get("self"))} for s in out]


async def stack(name: str) -> dict:
    return await _req("GET", f"/api/v1/stacks/{name}")


def _container(s: dict, service: str | None) -> dict:
    cs = s.get("containers") or []
    if not cs:
        raise Invalid(f"Im Stack {s['name']} läuft gerade kein Container")
    if not service:
        if len(cs) == 1:
            return cs[0]
        raise Invalid("Der Stack hat mehrere Dienste – gib service an: " + ", ".join(c["service"] for c in cs))
    c = next((c for c in cs if service in (c["service"], c["name"])), None)
    if not c:
        raise NotFound(f"Dienst {service} gibt es im Stack {s['name']} nicht – vorhanden: " + ", ".join(c["service"] for c in cs))
    return c


async def status(name: str) -> dict:
    s = await stack(name)
    urls = s.get("urls") or {}
    return {"stack": s["name"], "zustand": s.get("state"), "laeuft": f"{s.get('running')}/{s.get('total')}",
            "dateien_erreichbar": s.get("files_ok"),
            "dienste": [{"service": c["service"], "container": c["name"], "image": c.get("image"), "zustand": c.get("state"),
                         "status": c.get("status"), "health": c.get("health"),
                         "urls": [{"url": u["url"], "schutz": u.get("access")} for u in urls.get(c["id"], [])]} for c in s.get("containers") or []]}


async def logs(name: str, service: str | None, tail: int = 200, grep: str = "") -> dict:
    c = _container(await stack(name), service)
    out = await _req("GET", f"/api/v1/containers/{c['id']}/logs", params={"tail": max(10, min(int(tail or 200), 5000))})
    text = out.get("text") or ""
    if grep:
        g = grep.lower()
        text = "\n".join(line for line in text.splitlines() if g in line.lower())
    return {"service": c["service"], "container": c["name"], "gekuerzt": len(text) > LOG_MAX, "logs": text[-LOG_MAX:]}


async def exec_cmd(name: str, service: str | None, cmd: str, timeout: int = 60, user: str = "") -> dict:
    if not (cmd or "").strip():
        raise Invalid("befehl fehlt")
    c = _container(await stack(name), service)
    t = max(1, min(int(timeout or 60), 600))
    out = await _req("POST", f"/api/v1/containers/{c['id']}/exec", json={"cmd": cmd, "timeout": t, "user": user or ""}, timeout=t + 15)
    return {"service": c["service"], "container": c["name"], **out}


async def action(name: str, act: str) -> dict:
    """Startet den Vorgang im Dashboard und wartet, bis er fertig ist (Ausgabe am Ende)."""
    if act not in ACTIONS:
        raise Invalid("aktion: " + ", ".join(ACTIONS))
    job = (await _req("POST", f"/api/v1/stacks/{name}/action", json={"action": act}))["job"]
    waited = 0.0
    while True:
        j = await _req("GET", f"/api/v1/jobs/{job['id']}", params={"tail": 150})
        if j.get("done") or waited >= JOB_WAIT:
            break
        await asyncio.sleep(2)
        waited += 2
    return {"stack": name, "aktion": ACTIONS[act], "fertig": bool(j.get("done")), "ok": bool(j.get("ok")),
            "ausgabe": "\n".join(j.get("output") or [])[-LOG_MAX:],
            **({} if j.get("done") else {"hinweis": "Läuft noch im Dashboard weiter – Stand später mit server_status prüfen."})}


# ---- Dateien des Stacks (Compose, .env) – liest und schreibt das Dashboard (mit Sicherung des alten Stands) ----

async def files(name: str) -> dict:
    s = await stack(name)
    if not s.get("files_ok"):
        raise Invalid(f"Die Dateien von {name} sind im Dashboard nicht erreichbar (Ordner nicht eingebunden)")
    return {"stack": name, "ordner": s.get("dir"),
            "dateien": [{"name": f["name"], "pfad": f["path"], "art": f["kind"], "vorhanden": f["exists"], "bytes": f.get("size", 0)} for f in s.get("files") or []]}


async def _file(name: str, datei: str) -> dict:
    fs = (await files(name))["dateien"]
    want = (datei or "").strip()
    f = next((f for f in fs if want in (f["pfad"], f["name"])), None) if want else (fs[0] if fs else None)
    if not f:
        raise NotFound(f"Datei {want or '?'} gehört nicht zum Stack {name} – vorhanden: " + ", ".join(x["name"] for x in fs))
    return f


async def read_file(name: str, datei: str) -> dict:
    f = await _file(name, datei)
    out = await _req("GET", f"/api/v1/stacks/{name}/file", params={"path": f["pfad"]})
    return {"stack": name, "datei": f["name"], "pfad": f["pfad"], "vorhanden": f["vorhanden"], "inhalt": out.get("content", "")}


async def write_file(name: str, datei: str, content: str) -> dict:
    f = await _file(name, datei)
    await _req("PUT", f"/api/v1/stacks/{name}/file", json={"path": f["pfad"], "content": content})
    return {"stack": name, "datei": f["name"], "pfad": f["pfad"], "gespeichert": True,
            "hinweis": "Gespeichert (alter Stand im Dashboard gesichert). Übernehmen mit server_aktion up."}
