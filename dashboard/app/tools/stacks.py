"""Tool „Stacks“: alle Docker-Stacks des Servers sehen, starten/stoppen/neu starten/aktualisieren,
Compose- und .env-Dateien bearbeiten, Logs ansehen."""
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..discovery import discover, is_self, stack_files
from ..docker_api import Docker, DockerError
from .. import access, cloudflare_api, tunnels
from ..jobs import Job, Jobs, run_cmd
from . import Tool

router = APIRouter()

ACTIONS = {
    # id: (Titel, Compose-Befehl, braucht Dateien)
    "start": ("Starten", ["up", "-d"], False),
    "stop": ("Stoppen", ["stop"], False),
    "restart": ("Neu starten", ["restart"], False),
    "update": ("Aktualisieren", None, True),     # pull + up -d
    "up": ("Übernehmen", ["up", "-d", "--remove-orphans"], True),
    "down": ("Herunterfahren", ["down"], True),
}


def docker(req: Request) -> Docker:
    return req.app.state.docker


def jobs(req: Request) -> Jobs:
    return req.app.state.jobs


def fail(e: DockerError):
    raise HTTPException(e.status, str(e))


async def all_stacks(req: Request) -> list[dict]:
    try:
        cs = await docker(req).containers()
    except DockerError as e:
        fail(e)
    return discover(cs, get_settings().stack_dirs)


async def find(req: Request, name: str) -> dict:
    s = next((x for x in await all_stacks(req) if x["name"] == name), None)
    if not s:
        raise HTTPException(404, "Stack nicht gefunden")
    return s


def with_job(s: dict, js: Jobs) -> dict:
    j = js.busy("stack:" + s["name"])
    return {**s, "job": j.view() if j else None}


@router.get("/api/v1/stacks")
async def list_stacks(req: Request):
    return [with_job(s, jobs(req)) for s in await all_stacks(req)]


@router.get("/api/v1/cloudflare")
async def cloudflare_status():
    """Ob die Cloudflare-API (Token wie beim Cloudflare-MCP) geht: Konto, Zahl der Access-Apps und Tunnel-Routen."""
    return await cloudflare_api.status()


@router.get("/api/v1/stacks/{name}")
async def get_stack(name: str, req: Request):
    s = await find(req, name)
    return {**with_job(s, jobs(req)), "files": stack_files(s) if s["files_ok"] else [], "urls": await public_urls(req, s)}


async def public_urls(req: Request, s: dict) -> dict[str, list[dict]]:
    """Container-Id → öffentliche Adressen. Fehlt der Tunnel, bleibt das leer; der Stack selbst lädt trotzdem."""
    try:
        cs = await docker(req).containers()
        found = tunnels.match(await tunnels.routes(docker(req), cs), cs)
    except DockerError:
        return {}
    ids = {c["id"] for c in s["containers"]}
    return await access.annotate({k: v for k, v in found.items() if k in ids})


class ActionIn(BaseModel):
    action: str


def compose_base(s: dict) -> list[str]:
    args = ["docker", "compose", "-p", s["name"], "--project-directory", s["dir"]]
    for f in s["config_files"]:
        args += ["-f", f]
    envs = [f for f in s["env_files"] if os.path.isfile(f)]
    for f in envs:
        args += ["--env-file", f]
    return args


def self_services(s: dict) -> list[str]:
    return [c["service"] for c in s["containers"] if c["self"]]


def other_services(s: dict) -> list[str]:
    """Bei unserem eigenen Stack: alle Dienste außer dem Dashboard (sonst beendet es sich mitten im Vorgang selbst)."""
    mine = set(self_services(s))
    try:
        data = yaml.safe_load(Path(s["config_files"][0]).read_text(encoding="utf-8")) or {}
        names = list((data.get("services") or {}).keys())
    except (OSError, yaml.YAMLError, IndexError, AttributeError):
        names = []
    names = names or [c["service"] for c in s["containers"]]
    return [n for n in dict.fromkeys(names) if n not in mine]


@router.post("/api/v1/stacks/{name}/action")
async def stack_action(name: str, body: ActionIn, req: Request):
    if body.action not in ACTIONS:
        raise HTTPException(400, "Unbekannte Aktion")
    s = await find(req, name)
    js = jobs(req)
    if js.busy("stack:" + name) or js.busy("updates"):
        raise HTTPException(409, "Für diesen Stack läuft schon ein Vorgang")
    title, cmd, needs_files = ACTIONS[body.action]
    if s["self"] and body.action == "down":
        raise HTTPException(400, "Das Dashboard läuft in diesem Stack – herunterfahren geht nur auf dem Server selbst")
    if needs_files and not s["files_ok"]:
        raise HTTPException(400, "Die Compose-Dateien dieses Stacks sind im Dashboard nicht erreichbar (Ordner nicht eingebunden)")
    d = docker(req)
    env = docker_env()

    if s["kind"] == "compose" and s["files_ok"]:
        base = compose_base(s)
        only = other_services(s) if s["self"] else []

        async def work(job: Job) -> bool:
            if s["self"]:
                job.write("Hinweis: Das Dashboard selbst wird dabei ausgelassen (es aktualisiert sich im IDA Server unter Updates).")
            if body.action == "update":
                if not await run_cmd(job, base + ["pull"] + only, cwd=s["dir"], env=env):
                    return False
                return await run_cmd(job, base + ["up", "-d", "--remove-orphans"] + only, cwd=s["dir"], env=env)
            return await run_cmd(job, base + cmd + only, cwd=s["dir"], env=env)
    else:
        if body.action not in ("start", "stop", "restart"):
            raise HTTPException(400, "Für diesen Stack geht nur Starten, Stoppen und Neu starten")

        async def work(job: Job) -> bool:
            ok = True
            for c in s["containers"]:
                if c["self"]:
                    job.write(f"{c['name']}: übersprungen (das ist das Dashboard)")
                    continue
                try:
                    await d.action(c["id"], body.action)
                    job.write(f"{c['name']}: {title.lower()} ✓")
                except DockerError as e:
                    ok = False
                    job.write(f"{c['name']}: {e}")
            return ok

    job = js.start(f"{title}: {name}", "stack:" + name, work)
    return {"job": job.view()}


@router.post("/api/v1/containers/{cid}/action")
async def container_action(cid: str, body: ActionIn, req: Request):
    if body.action not in ("start", "stop", "restart"):
        raise HTTPException(400, "Unbekannte Aktion")
    d = docker(req)
    try:
        cs = await d.containers()
    except DockerError as e:
        fail(e)
    c = next((x for x in cs if x["Id"].startswith(cid)), None)
    if not c:
        raise HTTPException(404, "Container nicht gefunden")
    if is_self(c) and body.action != "start":
        raise HTTPException(400, "Das ist das Dashboard selbst – das geht nur auf dem Server")
    try:
        await d.action(c["Id"], body.action)
    except DockerError as e:
        fail(e)
    return {"ok": True}


@router.get("/api/v1/containers/{cid}/logs")
async def container_logs(cid: str, req: Request, tail: int = 300):
    d = docker(req)
    try:
        info = await d.inspect(cid)
        text = await d.logs(info["Id"], max(10, min(tail, 5000)), tty=bool((info.get("Config") or {}).get("Tty")))
    except DockerError as e:
        fail(e)
    return {"name": info.get("Name", "").lstrip("/"), "text": text}


class ExecIn(BaseModel):
    cmd: str = Field(min_length=1, max_length=20_000)
    timeout: int = Field(60, ge=1, le=600)
    user: str = Field("", max_length=64)


EXEC_MAX = 200_000  # so viele Zeichen Ausgabe kommen höchstens zurück (der Anfang wird abgeschnitten)


def docker_env() -> dict:
    # Nur das Nötigste: Compose nimmt Variablen aus der Umgebung vor denen aus der .env des Stacks. Unser CLOUDFLARE_API_TOKEN
    # oder DATA_DIR landeten sonst in fremden Stacks, die zufällig gleich benannte Variablen haben.
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    if not get_settings().docker_host.startswith("unix://"):
        env["DOCKER_HOST"] = get_settings().docker_host
    return env


@router.post("/api/v1/containers/{cid}/exec")
async def container_exec(cid: str, body: ExecIn, req: Request):
    """Ein Shell-Befehl im laufenden Container (`docker exec … sh -c`), Ausgabe kommt am Ende zurück (für IDA Code)."""
    d = docker(req)
    try:
        info = await d.inspect(cid)
    except DockerError as e:
        fail(e)
    if (info.get("Config") or {}).get("Labels", {}).get("ida.dashboard") == "self":
        raise HTTPException(400, "Das ist das Dashboard selbst – darin keine Befehle")
    if not (info.get("State") or {}).get("Running"):
        raise HTTPException(409, "Der Container läuft gerade nicht")
    args = ["docker", "exec"] + (["-u", body.user] if body.user else []) + [info["Id"], "sh", "-c", body.cmd]
    code, text, timed_out = await run_exec(args, body.timeout)
    cut = len(text) > EXEC_MAX
    return {"name": info.get("Name", "").lstrip("/"), "exit_code": code, "timed_out": timed_out, "truncated": cut, "output": text[-EXEC_MAX:]}


async def run_exec(args: list[str], timeout: int) -> tuple[int | None, str, bool]:
    """(Exit-Code oder None bei Zeitüberschreitung, Ausgabe, abgebrochen?)"""
    proc = await asyncio.create_subprocess_exec(*args, env=docker_env(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        out, _ = await proc.communicate()
        return None, (out or b"").decode("utf-8", "replace"), True
    return proc.returncode, (out or b"").decode("utf-8", "replace"), False


# ---------- Dateien (Compose, .env) ----------
class FileIn(BaseModel):
    path: str
    content: str = Field(max_length=2_000_000)


def allowed_file(s: dict, path: str) -> dict:
    if not s["files_ok"]:
        raise HTTPException(400, "Die Dateien dieses Stacks sind im Dashboard nicht erreichbar")
    p = os.path.normpath(path)
    f = next((x for x in stack_files(s) if x["path"] == p), None)
    if not f:
        raise HTTPException(403, "Diese Datei gehört nicht zum Stack")
    return f


@router.get("/api/v1/stacks/{name}/file")
async def read_file(name: str, path: str, req: Request):
    f = allowed_file(await find(req, name), path)
    if not f["exists"]:
        return {**f, "content": ""}
    try:
        return {**f, "content": Path(f["path"]).read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(400, "Die Datei ist keine Textdatei")


@router.put("/api/v1/stacks/{name}/file")
async def write_file(name: str, body: FileIn, req: Request):
    f = allowed_file(await find(req, name), body.path)
    if f["kind"] == "compose":
        try:
            data = yaml.safe_load(body.content)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"YAML-Fehler: {e}")
        if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
            raise HTTPException(400, "Eine Compose-Datei braucht einen Abschnitt „services:“")
    p = Path(f["path"])
    # alten Stand sichern (data/backups/<stack>/<datei>.<zeit>)
    if p.exists():
        bdir = get_settings().data_dir / "backups" / name
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bdir / f"{p.name}.{time.strftime('%Y%m%d-%H%M%S')}")
        olds = sorted(bdir.glob(p.name + ".*"))
        for old in olds[:-20]:
            old.unlink(missing_ok=True)
    # atomar schreiben, Rechte und Besitzer der alten Datei behalten
    st = p.stat() if p.exists() else None
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(body.content)
        if st:
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
        os.replace(tmp, p)
    except OSError as e:
        Path(tmp).unlink(missing_ok=True)
        raise HTTPException(500, f"Speichern ging nicht: {e}")
    return {"ok": True, "size": p.stat().st_size}


# ---------- Vorgänge ----------
@router.get("/api/v1/jobs")
async def list_jobs(req: Request):
    return sorted((j.view() for j in jobs(req).items.values()), key=lambda j: -j["started"])[:20]


@router.get("/api/v1/jobs/{jid}")
async def get_job(jid: str, req: Request, tail: int = 200):
    """Stand eines Vorgangs samt den letzten Zeilen der Ausgabe (zum Abfragen statt Live-Folgen)."""
    j = jobs(req).items.get(jid)
    if not j:
        raise HTTPException(404, "Vorgang nicht gefunden")
    return {**j.view(), "output": j.lines[-max(1, min(tail, 2000)):]}


@router.get("/api/v1/jobs/{jid}/live")
async def follow_job(jid: str, req: Request):
    j = jobs(req).items.get(jid)
    if not j:
        raise HTTPException(404, "Vorgang nicht gefunden")
    return StreamingResponse(jobs(req).follow(j), media_type="application/x-ndjson", headers={"cache-control": "no-store", "x-accel-buffering": "no"})


tool = Tool(id="stacks", name="Stacks", icon="deployed_code", router=router)
