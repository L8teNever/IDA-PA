"""Tool „Speicher“: Belegung der Platte, Anteil von Docker (Images, Container, Volumes, Build-Cache)
und wie viel jeder Stack braucht (inkl. seiner Ordner mit Daten, z. B. Minecraft-Welten). Aufräumen von Ungenutztem."""
import asyncio
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import hostinfo
from ..config import get_settings
from ..discovery import P, compose_files, ctr_name, stack_of
from ..docker_api import DockerError
from . import Tool

log = logging.getLogger("dashboard.storage")
router = APIRouter()
DF_EVERY = 15 * 60       # Docker-Belegung neu berechnen
DIRS_EVERY = 6 * 3600    # Ordnergrößen neu berechnen (geht über viele Dateien)


def dir_size(path: str) -> int:
    """Belegter Platz eines Ordners (wie `du -s`, Hardlinks einmal gezählt, keine Symlinks, nicht über andere Platten)."""
    total, seen = 0, set()
    try:
        dev = os.stat(path).st_dev
    except OSError:
        return 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_dev != dev:
                    continue
                if st.st_nlink > 1:
                    if (st.st_dev, st.st_ino) in seen:
                        continue
                    seen.add((st.st_dev, st.st_ino))
                total += getattr(st, "st_blocks", 0) * 512 or st.st_size
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
    return total


async def dir_sizes(paths: list[str]) -> dict[str, int]:
    """Größen vieler Ordner in einem Rutsch mit `du` (niedrigste Priorität, eigener Prozess – der Speicher ist danach
    sofort wieder frei). Ohne `du` rechnet Python selbst."""
    if not paths:
        return {}
    try:
        proc = await asyncio.create_subprocess_exec("nice", "-n", "19", "du", "-sx", "-B1", "--", *paths,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
    except OSError:
        return {p: await asyncio.to_thread(dir_size, p) for p in paths}
    sizes = {}
    for line in out.decode("utf-8", "replace").splitlines():
        n, _, path = line.partition("\t")
        if n.isdigit():
            sizes[path] = int(n)
    return sizes


class State:
    def __init__(self):
        self.df: dict | None = None
        self.df_at = 0.0
        self.dirs: dict[str, int] = {}
        self.dirs_at = 0.0
        self.busy = False
        self.error = ""
        self.lock = asyncio.Lock()


def st(req_or_app) -> State:
    app = getattr(req_or_app, "app", req_or_app)
    return app.state.storage


async def refresh(app, dirs: bool = True) -> None:
    s = st(app)
    async with s.lock:
        s.busy = True
        try:
            s.df = await app.state.docker.df()
            s.df_at = time.time()
            s.error = ""
            if dirs:
                found = {}
                for c in s.df.get("Containers") or []:
                    wd = (c.get("Labels") or {}).get(P + "project.working_dir")
                    if wd:
                        found[stack_of(c)] = wd
                # dazu Stack-Ordner, die gerade aus sind (nur Ordner mit Compose-Datei)
                for root in get_settings().stack_dirs:
                    try:
                        for d in os.scandir(root):
                            key = d.name.lower()
                            if (d.is_dir(follow_symlinks=False) and d.path not in found.values() and not os.path.isdir(found.get(key, ""))
                                    and compose_files(Path(d.path))):
                                found[key] = d.path
                    except OSError:
                        pass
                found = {k: os.path.normpath(v) for k, v in found.items() if os.path.isdir(v)}
                by_path = await dir_sizes(sorted(set(found.values())))
                s.dirs = {k: by_path.get(v, 0) for k, v in found.items()}
                s.dirs_at = time.time()
        except DockerError as e:
            s.error = str(e)
            log.warning("Speicher: %s", e)
        finally:
            s.busy = False


async def loop(app):
    while True:
        try:
            s = st(app)
            await refresh(app, dirs=time.time() - s.dirs_at > DIRS_EVERY)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Speicher-Berechnung fehlgeschlagen")
        await asyncio.sleep(DF_EVERY)


async def start(app):
    app.state.storage = State()
    app.state.storage_task = asyncio.create_task(loop(app))


async def stop(app):
    t = getattr(app.state, "storage_task", None)
    if t:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def summarize(df: dict, dirs: dict[str, int]) -> dict:
    images = {i["Id"]: i for i in df.get("Images") or []}
    vols = {v["Name"]: v for v in df.get("Volumes") or []}
    vsize = lambda v: max(0, ((v or {}).get("UsageData") or {}).get("Size", 0))  # noqa: E731
    stacks: dict[str, dict] = {}

    def get(name):
        return stacks.setdefault(name, {"name": name, "images": 0, "volumes": 0, "rw": 0, "files": 0, "_img": set(), "_vol": set(), "containers": 0})

    for c in df.get("Containers") or []:
        s = get(stack_of(c))
        s["containers"] += 1
        s["rw"] += c.get("SizeRw") or 0
        if c.get("ImageID") in images:
            s["_img"].add(c["ImageID"])
        for m in c.get("Mounts") or []:
            if m.get("Type") == "volume" and m.get("Name") in vols:
                s["_vol"].add(m["Name"])
    vol_users: dict[str, list[str]] = {}
    for c in df.get("Containers") or []:
        for m in c.get("Mounts") or []:
            if m.get("Type") == "volume" and m.get("Name"):
                vol_users.setdefault(m["Name"], []).append(ctr_name(c))
    for v in vols.values():
        proj = (v.get("Labels") or {}).get(P + "project")
        if proj:
            get(proj)["_vol"].add(v["Name"])
    for name, size in dirs.items():
        if name in stacks or size:
            get(name)["files"] = size
    out = []
    for s in stacks.values():
        s["images"] = sum(images[i].get("Size", 0) for i in s.pop("_img"))
        s["volumes"] = sum(vsize(vols[v]) for v in s.pop("_vol"))
        s["total"] = s["images"] + s["volumes"] + s["rw"] + s["files"]
        out.append(s)
    out.sort(key=lambda s: -s["total"])

    img_list = sorted(({"id": i["Id"].split(":")[-1][:12], "tags": i.get("RepoTags") or [], "size": i.get("Size", 0),
                        "shared": max(0, i.get("SharedSize") or 0), "containers": max(0, i.get("Containers", 0)), "created": i.get("Created")}
                       for i in images.values()), key=lambda x: -x["size"])
    vol_list = sorted(({"name": v["Name"], "size": vsize(v), "stack": (v.get("Labels") or {}).get(P + "project") or "",
                        "used_by": vol_users.get(v["Name"], [])} for v in vols.values()), key=lambda x: -x["size"])
    bc = df.get("BuildCache") or []
    unused_imgs = sum(i["size"] for i in img_list if not i["containers"])
    return {
        "docker": {
            "images": sum(i["size"] for i in img_list), "images_count": len(img_list), "images_unused": unused_imgs,
            "containers": sum(c.get("SizeRw") or 0 for c in df.get("Containers") or []), "containers_count": len(df.get("Containers") or []),
            "volumes": sum(v["size"] for v in vol_list), "volumes_count": len(vol_list),
            "volumes_unused": sum(v["size"] for v in vol_list if not v["used_by"]),
            "build_cache": sum(b.get("Size", 0) for b in bc if not b.get("Shared")),
        },
        "stacks": out, "images": img_list, "volumes": vol_list,
    }


@router.get("/api/v1/storage")
async def storage(req: Request):
    s = st(req)
    if s.df is None and not s.busy:
        await refresh(req.app, dirs=False)
    disk = await asyncio.to_thread(hostinfo.read_disk, get_settings().host_root)
    body = summarize(s.df or {}, s.dirs) if s.df else {"docker": None, "stacks": [], "images": [], "volumes": []}
    return {**body, "disk": disk, "updated": s.df_at or None, "dirs_updated": s.dirs_at or None, "busy": s.busy, "error": s.error}


@router.post("/api/v1/storage/refresh")
async def storage_refresh(req: Request):
    s = st(req)
    if not s.busy:
        t = asyncio.create_task(refresh(req.app, dirs=True))
        req.app.state.jobs.tasks.add(t)
        t.add_done_callback(req.app.state.jobs.tasks.discard)
    return {"ok": True}


class PruneIn(BaseModel):
    what: str


@router.post("/api/v1/storage/prune")
async def prune(body: PruneIn, req: Request):
    d = req.app.state.docker
    try:
        if body.what == "images":
            r = await d.prune_images()
        elif body.what == "build":
            r = await d.prune_build_cache()
        else:
            raise HTTPException(400, "Unbekannt")
    except DockerError as e:
        raise HTTPException(e.status, str(e))
    t = asyncio.create_task(refresh(req.app, dirs=False))
    req.app.state.jobs.tasks.add(t)
    t.add_done_callback(req.app.state.jobs.tasks.discard)
    return {"reclaimed": r.get("SpaceReclaimed", 0), "count": len(r.get("ImagesDeleted") or r.get("CachesDeleted") or [])}


tool = Tool(id="storage", name="Speicher", icon="hard_drive", router=router, start=start, stop=stop)
