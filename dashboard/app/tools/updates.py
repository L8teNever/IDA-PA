"""Tool „Updates“ (nur IDA Server): sucht neue Versionen der laufenden Docker-Images und spielt sie ein, wenn man das will.

Gesucht wird regelmäßig oder täglich zu einer Uhrzeit (app/registry.py, lädt dabei nichts). Was dann passiert, stellt man ein: nur
anzeigen, sofort aktualisieren oder zu einer festen Uhrzeit, für alle Dienste, alle außer … oder nur diese. Die Einstellungen,
die letzte Suche und der Verlauf liegen in /data/updates.json. Ohne Einstellung wird nur gesucht und angezeigt, nie aktualisiert.

Aktualisiert wird nur, was läuft und zu einem Compose-Stack gehört: `pull` + `up --no-deps` nur für die gewählten Dienste, damit
ausgenommene Dienste unberührt bleiben. Dabei gilt:
  * Lokal gebaute Images (`build:`) fasst das Tool nie an, ihr Name könnte in der Registry ein fremdes Image sein.
  * Das Watchtower-Label pre-update wird beachtet: Exit 75 heißt „gerade nicht“ (z. B. Stella telefoniert), der Dienst kommt später dran.
  * Hängt ein Dienst per network_mode am Aktualisierten, wird er mit neu erstellt, sonst verliert er sein Netz.
  * Der Server selbst kommt zuletzt und über einen kurzen Hilfscontainer, weil er sich sonst mitten im Vorgang beenden würde.
"""
import asyncio
import json
import logging
import os
import re
import shlex
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import registry
from ..config import get_settings
from ..discovery import P, discover
from ..docker_api import DockerError
from ..jobs import Job, run_cmd
from . import Tool
from .stacks import compose_base, docker_env, fail

log = logging.getLogger("dashboard.updates")
router = APIRouter()

JOB = "updates"
TICK = 30            # so oft schaut der Hintergrund-Dienst auf die Uhr
GRACE = 3600         # war der Server zur eingestellten Uhrzeit aus: so lange danach gilt sie noch, später wartet er auf den nächsten Tag
HEALTH_MIN = 12      # so lange muss ein neuer Container mindestens laufen …
HEALTH_MAX = 90      # … und so lange bekommt er, um gesund zu werden
HEALTH_STEP = 3
RETRY = 600          # „gerade nicht“ (Telefonat): nach 10 Minuten noch einmal
RETRIES = 6
SELF_WAIT = 300      # so lange darf der Neustart des Servers dauern
HISTORY = 50
HOOK = "com.centurylinklabs.watchtower.lifecycle.pre-update"
HOOK_TIMEOUT = HOOK + "-timeout"
BUSY_CODE = 75       # EX_TEMPFAIL, wie bei Watchtower
IMAGE_ID = re.compile(r"^(sha256:)?[0-9a-f]{12,64}$")

DEFAULTS = {"check": "interval", "every_hours": 6, "check_at": "03:00", "action": "notify", "apply_at": "03:00",
            "scope": "all", "picks": [], "cleanup": True, "saved_at": 0}


# ---------- Einstellungen und Zustand ----------
class Data:
    def __init__(self, path: Path):
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.settings = {**DEFAULTS, **(raw.get("settings") or {})}
        self.state = {"last_check": 0, "last_apply": 0, "check_error": "", "results": [], "history": [], "failed": {}, "retry_at": 0, "tries": 0,
                      "self_pending": None, **(raw.get("state") or {})}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"settings": self.settings, "state": self.state}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


def data(app: FastAPI) -> Data:
    if not hasattr(app.state, "updates"):
        app.state.updates = Data(get_settings().data_dir / "updates.json")
        app.state.updates_lock = asyncio.Lock()
    return app.state.updates


def wanted(s: dict, stack: str, service: str) -> bool:
    """Gilt die Automatik für diesen Dienst? Eine Auswahl ist ein ganzer Stack („shop“) oder ein Dienst („shop/web“)."""
    hit = stack in s["picks"] or f"{stack}/{service}" in s["picks"]
    return {"all": True, "except": not hit, "only": hit}[s["scope"]]


# ---------- Zeitplan ----------
def last_slot(at: str, now: float, tz: ZoneInfo) -> float:
    """Letzter Zeitpunkt, an dem es `at` Uhr war."""
    h, m = map(int, at.split(":"))
    t = datetime.fromtimestamp(now, tz)
    slot = t.replace(hour=h, minute=m, second=0, microsecond=0)
    return (slot if slot <= t else slot - timedelta(days=1)).timestamp()


def next_slot(at: str, now: float, tz: ZoneInfo) -> float:
    h, m = map(int, at.split(":"))
    t = datetime.fromtimestamp(last_slot(at, now, tz), tz) + timedelta(days=1)
    return t.replace(hour=h, minute=m).timestamp()


def slot_due(at: str, done: float, now: float, tz: ZoneInfo) -> bool:
    slot = last_slot(at, now, tz)
    return done < slot and now - slot <= GRACE


def check_due(s: dict, last: float, now: float, tz: ZoneInfo) -> bool:
    if s["check"] == "interval":
        return now - last >= s["every_hours"] * 3600
    # saved_at: Wer um 15:00 „täglich 14:50“ einstellt, meint morgen und nicht sofort
    return s["check"] == "daily" and slot_due(s["check_at"], max(last, s["saved_at"]), now, tz)


def apply_due(s: dict, last: float, now: float, tz: ZoneInfo) -> bool:
    return s["check"] != "off" and s["action"] == "at" and slot_due(s["apply_at"], max(last, s["saved_at"]), now, tz)


def next_times(s: dict, st: dict, now: float, tz: ZoneInfo) -> dict:
    out = {"check": None, "apply": None}
    if s["check"] == "interval":
        out["check"] = max(now, st["last_check"] + s["every_hours"] * 3600)
    elif s["check"] == "daily":
        out["check"] = next_slot(s["check_at"], now, tz)
    if s["check"] != "off" and s["action"] == "at":
        out["apply"] = next_slot(s["apply_at"], now, tz)
    return out


# ---------- Suchen ----------
def own_container(cid: str) -> bool:
    host = os.environ.get("HOSTNAME", "")
    return len(host) >= 12 and cid.startswith(host)


def compose_services(s: dict) -> dict:
    out: dict = {}
    for f in s.get("config_files") or []:
        try:
            conf = yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}
            out.update(conf.get("services") or {})
        except (OSError, yaml.YAMLError, AttributeError):
            continue
    return out


def attached(services: dict, chosen: list[str]) -> list[str]:
    """Dienste, die per network_mode: service:… (auch über Umwege) an einem der gewählten hängen."""
    out: list[str] = []
    grew = True
    while grew:
        grew = False
        for name, conf in services.items():
            m = re.fullmatch(r"service:(.+)", str((conf or {}).get("network_mode") or ""))
            if m and m[1] in (*chosen, *out) and name not in chosen and name not in out:
                out.append(name)
                grew = True
    return out


async def scan(app: FastAPI) -> list[dict]:
    """Alles, was läuft, mit dem Stand des Images. Gestoppte Container bleiben außen vor: Ein Update würde sie starten."""
    d = app.state.docker
    raw = await d.containers()
    by_id = {c["Id"]: c for c in raw}
    rows = []
    for s in discover(raw, get_settings().stack_dirs):
        services = compose_services(s) if s["files_ok"] else {}
        for c in s["containers"]:
            if c["state"] != "running":
                continue
            compose = s["kind"] == "compose"
            built = bool((services.get(c["service"]) or {}).get("build"))
            why = "" if s["files_ok"] else "Compose-Dateien nicht erreichbar" if compose else "Läuft ohne Compose, nur von Hand"
            rows.append({"key": f"{s['name']}/{c['service']}", "stack": s["name"], "service": c["service"], "container": c["name"], "id": c["id"],
                         "image": c["image"], "image_id": by_id[c["id"]].get("ImageID") or "", "built": built, "own": own_container(c["id"]),
                         "can": s["files_ok"] and not built, "why": why, "status": "", "message": "", "local": "", "remote": ""})
    await d.gather([image_of(d, r) for r in rows])
    images: dict[str, dict] = {}
    await d.gather([local_state(d, r, images) for r in rows])
    return rows


async def image_of(d, row: dict) -> None:
    """Ist der Tag weitergerückt, nennt Docker nur noch die Image-ID. Der Name steht dann noch in der Konfiguration des Containers."""
    if IMAGE_ID.match(row["image"]):
        try:
            row["image"] = ((await d.inspect(row["id"])).get("Config") or {}).get("Image") or row["image"]
        except DockerError:
            pass


async def local_state(d, row: dict, images: dict) -> None:
    if row["built"]:
        row["status"], row["why"] = "local", "Wird lokal gebaut"
        return
    if IMAGE_ID.match(row["image"]):
        row["status"], row["why"] = "local", "Kein Image-Name bekannt"
        return
    if registry.parse(row["image"]).pinned:
        row["status"] = "pinned"
        return

    async def get(ref: str) -> dict:
        if ref not in images:
            try:
                images[ref] = await d.image(ref)
            except DockerError:
                images[ref] = {}
        return images[ref]

    mine = await get(row["image_id"])
    row["digests"] = [x.split("@", 1)[1] for x in mine.get("RepoDigests") or [] if "@" in x]
    if not row["digests"]:
        # Ohne Digest läuft der Container meist auf einem Stand, den der Tag längst verlassen hat
        tagged = await get(row["image"])
        if tagged.get("Id") and tagged["Id"] != row["image_id"]:
            row["stale"] = True
        else:
            row["status"], row["why"] = "local", "Image kommt aus keiner Registry"


async def check(app: FastAPI) -> list[dict]:
    db = data(app)
    async with app.state.updates_lock:
        rows = await scan(app)
        auths = registry.load_auths()
        todo = sorted({r["image"] for r in rows if not r["status"]})
        found: dict[str, str | registry.RegistryError] = {}
        sem = asyncio.Semaphore(6)
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            async def one(image: str):
                ref = registry.parse(image)
                async with sem:
                    try:
                        found[image] = await registry.digest(client, ref, auths.get(ref.registry))
                    except registry.RegistryError as e:
                        found[image] = e
            await asyncio.gather(*(one(i) for i in todo))
        for r in rows:
            if not r["status"]:
                judge(r, found[r["image"]])
            r.pop("digests", None)
            r.pop("stale", None)
        db.state.update(results=rows, last_check=time.time(), check_error="")
        db.save()
    return rows


def judge(r: dict, remote) -> None:
    digests = r.get("digests") or []
    r["local"] = digests[0][7:19] if digests else ""
    if isinstance(remote, registry.RegistryError):
        ref = registry.parse(r["image"])
        if r.get("stale"):
            r["status"], r["message"] = "update", "Neuere Version liegt schon auf dem Server"
        elif isinstance(remote, registry.NotThere) and ref.registry == registry.HUB and ref.repo.startswith("library/"):
            r["status"], r["why"] = "local", "Image kommt aus keiner Registry"   # kurzer Name ohne Registry: lokal gebaut, kein offizielles Image
        else:
            r["status"], r["message"] = "error", str(remote)
        return
    r["remote"] = remote
    r["status"] = "current" if remote in digests else "update"
    if r.get("stale"):
        r["status"], r["message"] = "update", "Läuft noch auf einem alten Stand"


# ---------- Aktualisieren ----------
def pending(db: Data, keys: list[str] | None = None, auto: bool = False) -> list[dict]:
    rows = [r for r in db.state["results"] if r["status"] == "update" and r["can"]]
    if keys:
        rows = [r for r in rows if r["key"] in keys]
    if auto:  # was schon gescheitert ist, probiert die Automatik erst bei der nächsten neuen Version wieder
        rows = [r for r in rows if wanted(db.settings, r["stack"], r["service"]) and db.state["failed"].get(r["key"]) != (r["remote"] or "stale")]
    return rows


async def ready(job: Job, d, row: dict) -> bool:
    """Darf dieser Container gerade neu erstellt werden? Fragt sein Label pre-update."""
    try:
        labels = ((await d.inspect(row["id"])).get("Config") or {}).get("Labels") or {}
    except DockerError:
        return True
    cmd = labels.get(HOOK)
    if not cmd:
        return True
    try:
        minutes = float(labels.get(HOOK_TIMEOUT) or 1)
    except ValueError:
        minutes = 1
    proc = await asyncio.create_subprocess_exec("docker", "exec", row["id"], "sh", "-c", cmd, env=docker_env(),
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), minutes * 60)
    except TimeoutError:
        proc.kill()
        job.write(f"{row['key']}: Prüfung vor dem Update antwortet nicht, später")
        return False
    if proc.returncode == 0:
        return True
    job.write(f"{row['key']}: {out.decode('utf-8', 'replace').strip() or 'jetzt nicht'}, später")
    return False


async def settled(app: FastAPI, stack: str, services: list[str]) -> list[str]:
    """Wartet, bis die neuen Container stabil laufen. Gibt die zurück, die es nicht tun."""
    d = app.state.docker
    start = time.monotonic()
    while True:
        await asyncio.sleep(HEALTH_STEP)
        seen = {}
        for c in await d.containers():
            lab = c.get("Labels") or {}
            if lab.get(P + "project") == stack and lab.get(P + "service") in services:
                seen[lab[P + "service"]] = c
        bad = [s for s in services if s not in seen or seen[s].get("State") != "running" or "unhealthy" in (seen[s].get("Status") or "")]
        waiting = [s for s in services if s in seen and "health: starting" in (seen[s].get("Status") or "")]
        took = time.monotonic() - start
        if took >= HEALTH_MAX or (took >= HEALTH_MIN and not waiting and not bad):
            return bad + waiting


async def update_stack(app: FastAPI, job: Job, s: dict, rows: list[dict], out: dict, cleanup: bool) -> None:
    d = app.state.docker
    env = docker_env()
    base = compose_base(s)
    keep = []
    for r in rows:
        if await ready(job, d, r):
            keep.append(r)
        else:
            out["later"].append(r["key"])
    if not keep:
        return
    names = list(dict.fromkeys(r["service"] for r in keep))
    mine = [r for r in keep if r["own"]]
    now = [n for n in names if n not in {r["service"] for r in mine}]
    job.write(f"\n=== {s['name']}: {', '.join(names)} ===")
    if not await run_cmd(job, base + ["pull", *names], cwd=s["dir"], env=env):
        out["failed"] += [r["key"] for r in keep]
        return
    if mine:
        out["self"] = (s, mine[0])
    if not now:
        return
    rest = [r for r in keep if not r["own"]]
    if not await run_cmd(job, base + ["up", "-d", "--no-deps", "--pull", "never", *now], cwd=s["dir"], env=env):
        out["failed"] += [r["key"] for r in rest]
        return
    running = {c["service"] for c in s["containers"] if c["state"] == "running"}
    hang = [n for n in attached(compose_services(s), now) if n in running]
    if hang:
        job.write(f"Hängt am selben Netz und wird mit neu erstellt: {', '.join(hang)}")
        if not await run_cmd(job, base + ["up", "-d", "--no-deps", "--pull", "never", "--force-recreate", *hang], cwd=s["dir"], env=env):
            out["problem"] += [f"{s['name']}/{n}" for n in hang]
    bad = await settled(app, s["name"], now + hang)
    out["problem"] += [f"{s['name']}/{n}" for n in bad]
    good = [r for r in rest if r["service"] not in bad]
    out["updated"] += [r["key"] for r in good]
    if bad:
        job.write(f"Läuft nicht sauber: {', '.join(bad)}. Das alte Image bleibt deshalb liegen.")
    elif cleanup:
        await drop_images(job, d, rest)


async def drop_images(job: Job, d, rows: list[dict]) -> None:
    """Nur die abgelösten Images; was noch ein anderer Container nutzt, lehnt Docker von selbst ab."""
    used = {c.get("ImageID") for c in await d.containers()}
    for image_id in {r["image_id"] for r in rows} - used:
        try:
            await d.remove_image(image_id)
            job.write(f"Altes Image gelöscht: {image_id[7:19]}")
        except DockerError:
            pass


async def restart_self(job: Job, s: dict, row: dict) -> bool:
    """Ein Hilfscontainer aus dem neuen Image erstellt den Server neu, der sich sonst dabei selbst beenden würde."""
    dirs = {s["dir"], *(str(Path(f).parent) for f in [*s["config_files"], *s["env_files"]])}
    inner = shlex.join([*compose_base(s), "up", "-d", "--no-deps", "--pull", "never", row["service"]])
    args = ["docker", "run", "-d", "--rm", "--name", f"ida-self-update-{int(time.time())}", "-v", "/var/run/docker.sock:/var/run/docker.sock"]
    for x in sorted(dirs):
        args += ["-v", f"{x}:{x}"]
    args += ["-w", s["dir"], "--entrypoint", "sh", row["image"], "-c", f"sleep 5; {inner}"]
    job.write("\nDer Server startet jetzt neu, die Verbindung bricht dabei kurz ab.")
    return await run_cmd(job, args, cwd=s["dir"], env=docker_env())


def remember(db: Data, entry: dict) -> None:
    db.state["history"] = [entry, *db.state["history"]][:HISTORY]


def start_run(app: FastAPI, rows: list[dict], auto: bool) -> Job:
    db = data(app)
    d = app.state.docker
    js = app.state.jobs

    async def work(job: Job) -> bool:
        out: dict = {"updated": [], "failed": [], "problem": [], "later": [], "self": None}
        stacks = {s["name"]: s for s in discover(await d.containers(), get_settings().stack_dirs)}
        for name in dict.fromkeys(r["stack"] for r in sorted(rows, key=lambda r: any(x["own"] and x["stack"] == r["stack"] for x in rows))):
            todo = [r for r in rows if r["stack"] == name]
            s = stacks.get(name)
            if not s or js.busy("stack:" + name):
                job.write(f"\n{name}: gerade belegt, später")
                out["later"] += [r["key"] for r in todo]
                continue
            await update_stack(app, job, s, todo, out, db.settings["cleanup"])
        by_key = {r["key"]: r for r in rows}
        for key in out["failed"] + out["problem"]:
            if key in by_key:
                db.state["failed"][key] = by_key[key]["remote"] or "stale"
        for key in out["updated"]:
            db.state["failed"].pop(key, None)
        retry = out["later"] and auto and db.state["tries"] < RETRIES
        db.state.update(retry_at=time.time() + RETRY if retry else 0, tries=db.state["tries"] + 1 if retry else 0)
        ok = not out["failed"] and not out["problem"]
        entry = {"id": uuid.uuid4().hex[:12], "at": time.time(), "auto": auto, "ok": ok, "updated": out["updated"], "failed": out["failed"], "problem": out["problem"], "later": out["later"],
                 "running": False, "log": job.lines[-200:]}
        own = out.pop("self")
        if own:
            s, row = own
            entry["running"] = True
            db.state["self_pending"] = {"key": row["key"], "image_id": row["image_id"], "at": time.time()}
        remember(db, entry)
        db.save()
        if not own:
            await refresh(app)
            return ok
        await refresh(app)
        started = await restart_self(job, s, row)
        if not started:
            finish_self(db, False)
        return ok and started

    return js.start("Automatische Updates" if auto else "Updates", JOB, work)


async def refresh(app: FastAPI) -> None:
    try:
        await check(app)
    except DockerError as e:
        log.warning("Stand nach den Updates nicht lesbar: %s", e)


def finish_self(db: Data, ok: bool) -> None:
    p = db.state["self_pending"]
    db.state["self_pending"] = None
    for e in db.state["history"]:
        if e.get("running"):
            e["running"] = False
            e["updated" if ok else "failed"].append(p["key"])
            e["ok"] = e["ok"] and ok
            break
    db.save()


async def settle_self(app: FastAPI) -> None:
    """Nach dem Neustart: Läuft der Server wirklich auf dem neuen Image?"""
    db = data(app)
    p = db.state["self_pending"]
    if not p:
        return
    me = next((c for c in await app.state.docker.containers() if own_container(c["Id"])), None)
    if me and me.get("ImageID") != p["image_id"]:
        finish_self(db, True)
        if db.settings["cleanup"]:
            try:
                await app.state.docker.remove_image(p["image_id"])
            except DockerError:
                pass  # nutzt noch ein anderer Container
    elif time.time() - p["at"] > SELF_WAIT:
        finish_self(db, False)


# ---------- Hintergrund ----------
async def tick(app: FastAPI, now: float | None = None) -> None:
    db = data(app)
    now = now or time.time()
    tz = ZoneInfo(get_settings().timezone)
    s, st = db.settings, db.state
    await settle_self(app)
    if app.state.jobs.busy(JOB) or s["check"] == "off":
        return
    run = False
    if check_due(s, st["last_check"], now, tz):
        await check(app)
        run = s["action"] == "now"
    if apply_due(s, st["last_apply"], now, tz):
        st["last_apply"] = now
        db.save()
        await check(app)
        run = True
    if st["retry_at"] and now >= st["retry_at"]:
        st["retry_at"] = 0
        run = s["action"] != "notify"
    if run and s["action"] != "notify" and (rows := pending(db, auto=True)):
        start_run(app, rows, auto=True)


async def loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(TICK)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 – der Dienst soll beim nächsten Mal weitermachen
            log.warning("Update-Suche fehlgeschlagen: %s", e)
            db = data(app)
            db.state.update(check_error=str(e), last_check=time.time())
            db.save()


async def start(app: FastAPI) -> None:
    data(app)
    app.state.updates_task = asyncio.create_task(loop(app))


async def stop(app: FastAPI) -> None:
    app.state.updates_task.cancel()


# ---------- API ----------
class SettingsIn(BaseModel):
    check: Literal["off", "interval", "daily"]
    every_hours: int = Field(6, ge=1, le=168)
    check_at: str = Field("03:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    action: Literal["notify", "now", "at"]
    apply_at: str = Field("03:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    scope: Literal["all", "except", "only"]
    picks: list[str] = Field(default_factory=list, max_length=500)
    cleanup: bool = True


class ApplyIn(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=500)


@router.get("/api/v1/updates")
async def overview(req: Request):
    db = data(req.app)
    s, st = db.settings, db.state
    try:
        raw = await req.app.state.docker.containers()
    except DockerError as e:
        fail(e)
    groups = [{"name": x["name"], "services": sorted({c["service"] for c in x["containers"]})} for x in discover(raw, get_settings().stack_dirs)
              if x["kind"] == "compose" and x["containers"]]
    job = req.app.state.jobs.busy(JOB)
    return {"settings": s, "results": [{**r, "auto": wanted(s, r["stack"], r["service"])} for r in st["results"]], "stacks": groups,
            "last_check": st["last_check"], "check_error": st["check_error"], "history": [{k: v for k, v in e.items() if k != "log"} for e in st["history"]], "self_pending": st["self_pending"],
            "next": next_times(s, st, time.time(), ZoneInfo(get_settings().timezone)), "job": job.view() if job else None,
            "watchtower": any("watchtower" in (c.get("Image") or "") and c.get("State") == "running" for c in raw)}


@router.get("/api/v1/updates/log/{entry}")
async def run_log(entry: str, req: Request):
    e = next((x for x in data(req.app).state["history"] if x.get("id") == entry), None)
    if not e:
        raise HTTPException(404, "Diesen Eintrag gibt es nicht mehr")
    return {"lines": e.get("log") or []}


@router.put("/api/v1/updates/settings")
async def save_settings(body: SettingsIn, req: Request):
    if body.scope == "only" and not body.picks and body.action != "notify":
        raise HTTPException(400, "Wähle mindestens einen Dienst aus")
    db = data(req.app)
    db.settings = {**db.settings, **body.model_dump(), "saved_at": time.time()}
    db.state.update(retry_at=0, tries=0)
    db.save()
    return db.settings


@router.post("/api/v1/updates/check")
async def check_now(req: Request):
    data(req.app)
    if req.app.state.jobs.busy(JOB):
        raise HTTPException(409, "Es läuft gerade ein Update")
    try:
        rows = await check(req.app)
    except DockerError as e:
        fail(e)
    return {"found": sum(r["status"] == "update" for r in rows)}


@router.post("/api/v1/updates/apply")
async def apply_now(body: ApplyIn, req: Request):
    db = data(req.app)
    if req.app.state.jobs.busy(JOB):
        raise HTTPException(409, "Es läuft schon ein Update")
    rows = pending(db, body.keys)
    if not rows:
        raise HTTPException(400, "Dafür gibt es gerade kein Update")
    return {"job": start_run(req.app, rows, auto=False).view()}


tool = Tool(id="updates", name="Updates", icon="system_update_alt", router=router, start=start, stop=stop)
