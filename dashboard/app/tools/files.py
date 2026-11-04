"""Tool „Dateien“: Datei-Explorer für den ganzen Server. Ordner ansehen, Dateien öffnen, bearbeiten, hochladen, entpacken,
umbenennen, kopieren, verschieben und löschen.

Der Server ist unter FILES_ROOT eingebunden (/hostfs). Alle Pfade der API sind Pfade auf dem Server: „/etc/hosts“ liegt hier unter
/hostfs/etc/hosts. Symlinks werden deshalb selbst aufgelöst, sonst würde „/etc/x -> /var/y“ im Container statt auf dem Server landen.
Fehlt der Mount, zeigt das Tool nur den Ordner FILES_FALLBACK, der unter demselben Pfad eingebunden ist."""
import asyncio
import mimetypes
import os
import posixpath
import re
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..jobs import Job, Jobs, run_cmd
from . import Tool

router = APIRouter()

MAX_ENTRIES = 5000               # mehr zeigt ein Ordner nicht auf einmal
MAX_TEXT = 5 * 1024 * 1024       # so große Dateien öffnet der Editor
MAX_HOPS = 40                    # Symlinks hintereinander
VIRTUAL = ("/proc", "/sys", "/dev")  # keine echten Dateien: nur ansehen
PART = ".idaupload"              # Upload, der noch läuft
PART_KEEP = 24 * 3600            # abgebrochene Uploads bleiben so lange liegen
ARCHIVES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
INLINE = ("image/", "video/", "audio/")
SANDBOX = "sandbox allow-scripts; default-src 'self' 'unsafe-inline' data: blob:"  # eigene Herkunft: kommt weder an die API noch an Cookies


def base_scope() -> tuple[str, str]:
    """(Ordner, unter dem der Server im Container liegt; Bereich, den das Tool zeigen darf)."""
    s = get_settings()
    root = s.files_root.rstrip("/")
    if root and os.path.isdir(root):
        return root, "/"
    return "", posixpath.normpath(s.files_fallback)


def inside(v: str, scope: str) -> bool:
    return scope == "/" or v == scope or v.startswith(scope + "/")


def clean(p: str) -> str:
    if "\0" in (p or ""):
        raise HTTPException(400, "Ungültiger Pfad")
    return posixpath.normpath("/" + (p or "").lstrip("/"))


def resolve(p: str, follow: bool = True) -> str:
    """Pfad auf dem Server ohne Symlinks. follow=False lässt das letzte Glied stehen (umbenennen und löschen treffen den Link selbst)."""
    base, scope = base_scope()
    parts = [x for x in clean(p).split("/") if x]
    done: list[str] = []
    hops = 0
    while parts:
        part = parts.pop(0)
        if part == ".":
            continue
        if part == "..":
            if done:
                done.pop()
            continue
        disk = f"{base}/{'/'.join([*done, part])}"
        if (follow or parts) and os.path.islink(disk):
            hops += 1
            if hops > MAX_HOPS:
                raise HTTPException(400, "Zu viele Symlinks")
            target = os.readlink(disk)
            if target.startswith("/"):
                done = []
            parts = [x for x in target.split("/") if x] + parts
            continue
        done.append(part)
    v = "/" + "/".join(done)
    if not inside(v, scope):
        raise HTTPException(403, f"Außerhalb des Bereichs {scope}")
    return v


def disk(v: str) -> str:
    return base_scope()[0] + v


def virtual(v: str) -> bool:
    return base_scope()[1] == "/" and any(v == x or v.startswith(x + "/") for x in VIRTUAL)


def writable(v: str) -> str:
    if virtual(v):
        raise HTTPException(403, f"{v.split('/')[1]} ist kein echter Ordner, hier lässt sich nichts ändern")
    return v


def changeable(v: str) -> str:
    """Löschen, verschieben, umbenennen: nie die Wurzel oder einen Ordner direkt darunter (/etc, /var …)."""
    writable(v)
    scope = base_scope()[1]
    if v == scope or (scope == "/" and v.count("/") == 1):
        raise HTTPException(403, f"{v} ist geschützt")
    return v


def valid_name(name: str) -> str:
    n = (name or "").strip()
    if not n or n in (".", "..") or "/" in n or "\0" in n or len(n.encode()) > 255:
        raise HTTPException(400, "Ungültiger Name")
    return n


@contextmanager
def errors() -> Iterator[None]:
    try:
        yield
    except FileNotFoundError as e:
        raise HTTPException(404, "Nicht gefunden") from e
    except FileExistsError as e:
        raise HTTPException(409, "Das gibt es dort schon") from e
    except PermissionError as e:
        raise HTTPException(403, "Keine Berechtigung") from e
    except OSError as e:
        raise HTTPException(400, e.strerror or "Fehler im Dateisystem") from e


# ---------- ansehen ----------
_users: tuple[float, dict[int, str]] | None = None  # (Zeitpunkt, Namen); None = noch nie gelesen


def users() -> dict[int, str]:
    """Namen aus der /etc/passwd des Servers (die des Containers kennt sie nicht)."""
    global _users
    if _users is None or time.monotonic() - _users[0] > 300:  # nicht gegen 0 rechnen: monotonic zählt ab Systemstart
        names: dict[int, str] = {}
        try:
            for line in Path(disk("/etc/passwd")).read_text(encoding="utf-8", errors="replace").splitlines():
                f = line.split(":")
                if len(f) > 2 and f[2].isdigit():
                    names[int(f[2])] = f[0]
        except OSError:
            pass
        _users = (time.monotonic(), names)
    return _users[1]


def entry(folder: str, name: str) -> dict | None:
    v = posixpath.join(folder, name)
    try:
        st = os.lstat(disk(v))
    except OSError:
        return None
    link = None
    if stat.S_ISLNK(st.st_mode):
        try:
            link = os.readlink(disk(v))
            st = os.stat(disk(resolve(v)))
        except (OSError, HTTPException):
            return {"name": name, "path": v, "type": "broken", "size": 0, "mtime": 0, "mode": "", "owner": "", "link": link}
    kind = "dir" if stat.S_ISDIR(st.st_mode) else "file" if stat.S_ISREG(st.st_mode) else "other"
    return {"name": name, "path": v, "type": kind, "size": st.st_size if kind == "file" else None, "mtime": st.st_mtime, "mode": stat.filemode(st.st_mode),
            "owner": users().get(st.st_uid, str(st.st_uid)), "link": link}


@router.get("/api/v1/fs/list")
async def list_dir(path: str = ""):
    _, scope = base_scope()
    v = resolve(path or scope)
    with errors():
        names = sorted(n for n in os.listdir(disk(v)) if not n.endswith(PART))
    shown = names[:MAX_ENTRIES]
    items = [e for e in (entry(v, n) for n in shown) if e]
    items.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    usage = None
    try:
        du = shutil.disk_usage(disk(v))
        usage = {"total": du.total, "free": du.free}
    except OSError:
        pass
    return {"path": v, "root": scope, "parent": None if v == scope else posixpath.dirname(v), "entries": items, "more": len(names) - len(shown),
            "readonly": virtual(v), "usage": usage}


def regular(v: str) -> os.stat_result:
    with errors():
        st = os.stat(disk(v))
    if not stat.S_ISREG(st.st_mode):
        raise HTTPException(400, "Das ist keine normale Datei")
    return st


@router.get("/api/v1/fs/text")
async def read_text(path: str):
    v = resolve(path)
    st = regular(v)
    if st.st_size > MAX_TEXT:
        raise HTTPException(413, f"Zu groß für den Editor (höchstens {MAX_TEXT // 1024 // 1024} MB)")
    with errors(), open(disk(v), "rb") as f:
        data = f.read(MAX_TEXT + 1)
    if b"\0" in data[:8192]:
        raise HTTPException(415, "Das ist keine Textdatei")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(415, "Das ist keine UTF-8-Textdatei") from e
    return {"path": v, "content": text, "version": str(st.st_mtime_ns), "size": st.st_size, "readonly": virtual(v)}


@router.get("/api/v1/fs/raw")
async def raw(path: str, download: bool = False):
    v = resolve(path)
    if virtual(v):
        raise HTTPException(403, "Dieser Bereich lässt sich nur ansehen")
    regular(v)
    name = posixpath.basename(v)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    head = {"x-content-type-options": "nosniff", "cache-control": "private, no-cache"}
    active = mime in ("text/html", "image/svg+xml")
    if not download and (mime.startswith(INLINE) or mime == "application/pdf" or active):
        head["content-disposition"] = f"inline; filename*=UTF-8''{quote(name)}"
        if active:  # kann Skripte enthalten: läuft isoliert
            head["content-security-policy"] = SANDBOX
        return FileResponse(disk(v), media_type=mime, headers=head)
    head["content-disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    return FileResponse(disk(v), media_type="application/octet-stream", headers=head)


# ---------- ändern ----------
class TextIn(BaseModel):
    path: str
    content: str = Field(max_length=MAX_TEXT * 2)
    version: str | None = None


def replace_file(target: str, data: bytes) -> None:
    """Neu schreiben und tauschen: ein Absturz hinterlässt nie eine halbe Datei. Rechte und Besitzer bleiben."""
    st = os.stat(target)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".ida-", suffix=PART)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        os.chown(tmp, st.st_uid, st.st_gid)
        try:
            os.replace(tmp, target)
        except OSError:  # einzeln eingebundene Datei lässt sich nicht tauschen
            with open(target, "wb") as f:
                f.write(data)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@router.put("/api/v1/fs/text")
async def write_text(body: TextIn):
    v = writable(resolve(body.path))
    st = regular(v)
    if body.version and body.version != str(st.st_mtime_ns):
        raise HTTPException(409, "Die Datei wurde inzwischen geändert")
    with errors():
        replace_file(disk(v), body.content.encode("utf-8"))
    return {"path": v, "version": str(os.stat(disk(v)).st_mtime_ns), "size": len(body.content.encode("utf-8"))}


class NewIn(BaseModel):
    dir: str
    name: str
    folder: bool = False


def like_parent(path: str) -> None:
    """Neues gehört dem, dem auch der Ordner gehört, nicht root."""
    st = os.stat(os.path.dirname(path))
    os.chown(path, st.st_uid, st.st_gid, follow_symlinks=False)


@router.post("/api/v1/fs/new")
async def create(body: NewIn):
    v = posixpath.join(writable(resolve(body.dir)), valid_name(body.name))
    with errors():
        if body.folder:
            os.mkdir(disk(v))
        else:
            with open(disk(v), "x"):
                pass
        like_parent(disk(v))
    return entry(posixpath.dirname(v), body.name.strip())


class RenameIn(BaseModel):
    path: str
    name: str


@router.post("/api/v1/fs/rename")
async def rename(body: RenameIn):
    v = changeable(resolve(body.path, follow=False))
    new = posixpath.join(posixpath.dirname(v), valid_name(body.name))
    if os.path.lexists(disk(new)):
        raise HTTPException(409, "Das gibt es dort schon")
    with errors():
        os.rename(disk(v), disk(new))
    return entry(posixpath.dirname(new), posixpath.basename(new))


# ---------- hochladen: in Stücken, Cloudflare nimmt höchstens 100 MB pro Anfrage ----------
def upload_paths(folder: str, name: str, upload_id: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z0-9]{8,32}", upload_id):
        raise HTTPException(400, "Ungültige Upload-Id")
    parts = [valid_name(x) for x in (name or "").split("/")]
    target = writable(resolve(posixpath.join(writable(resolve(folder)), *parts)))  # Unterordner können Links sein
    return target, posixpath.join(posixpath.dirname(target), f".{upload_id}{PART}")


def make_dirs(folder: str) -> None:
    """Fehlende Ordner (Hochladen mit Unterordnern) anlegen; sie gehören wie Neues dem Besitzer des Ordners darüber."""
    if os.path.isdir(folder):
        return
    make_dirs(os.path.dirname(folder))
    os.mkdir(folder)
    like_parent(folder)


def sweep_parts(folder: str) -> None:
    for n in os.listdir(folder):
        p = os.path.join(folder, n)
        if n.endswith(PART) and time.time() - os.path.getmtime(p) > PART_KEEP:
            os.unlink(p)


@router.put("/api/v1/fs/upload")
async def upload(req: Request, dir: str, name: str, upload_id: str, total: int, offset: int = 0, overwrite: bool = False):
    target, part = upload_paths(dir, name, upload_id)
    with errors():
        if offset == 0:
            if os.path.lexists(disk(target)) and not overwrite:
                raise HTTPException(409, "exists")
            if os.path.isdir(disk(target)):
                raise HTTPException(409, "Dort liegt ein Ordner mit diesem Namen")
            make_dirs(os.path.dirname(disk(target)))
            sweep_parts(os.path.dirname(disk(target)))
        elif not os.path.exists(disk(part)) or os.path.getsize(disk(part)) != offset:
            raise HTTPException(409, "Upload unterbrochen, bitte neu starten")
        with open(disk(part), "wb" if offset == 0 else "r+b") as f:
            f.seek(offset)
            async for chunk in req.stream():
                f.write(chunk)
            size = f.tell()
        if size < total:
            return {"size": size, "done": False}
        os.replace(disk(part), disk(target))
        like_parent(disk(target))
    return {"size": size, "done": True}


class CancelIn(BaseModel):
    dir: str
    name: str
    upload_id: str


@router.delete("/api/v1/fs/upload")
async def cancel_upload(body: CancelIn):
    _, part = upload_paths(body.dir, body.name, body.upload_id)
    with errors():
        Path(disk(part)).unlink(missing_ok=True)
    return {"ok": True}


# ---------- längere Vorgänge: im Hintergrund, sie dürfen länger dauern als eine Anfrage ----------
def jobs(req: Request) -> Jobs:
    return req.app.state.jobs


def unique(folder: str, name: str, word: str = " Kopie") -> str:
    """„a.txt“ → „a Kopie.txt“ → „a Kopie 2.txt“; bei einem leeren word „a 2.txt“."""
    stem, ext = name, ""
    for a in ARCHIVES:
        if name.lower().endswith(a):
            stem, ext = name[:-len(a)], name[-len(a):]
            break
    else:
        if "." in name[1:]:
            stem, ext = posixpath.splitext(name)
    v, n = posixpath.join(folder, name if not word else f"{stem}{word}{ext}"), 1
    while os.path.lexists(disk(v)):
        n += 1
        v = posixpath.join(folder, f"{stem}{word} {n}{ext}")
    return v


class PasteIn(BaseModel):
    items: list[str] = Field(min_length=1, max_length=2000)
    dest: str
    move: bool = False
    conflict: str = Field("keep", pattern="^(keep|replace|skip)$")


def paste_plan(body: PasteIn) -> tuple[str, list[tuple[str, str, bool]]]:
    """(Ziel, [(Quelle, Ziel, liegt dort schon)])."""
    dest = writable(resolve(body.dest))
    with errors():
        if not stat.S_ISDIR(os.stat(disk(dest)).st_mode):
            raise HTTPException(400, "Das Ziel ist kein Ordner")
    plan = []
    for item in body.items:
        src = resolve(item, follow=False)
        changeable(src) if body.move else writable(src)
        if not os.path.lexists(disk(src)):
            raise HTTPException(404, f"{src} gibt es nicht mehr")
        if dest == src or dest.startswith(src.rstrip("/") + "/"):
            raise HTTPException(400, f"{posixpath.basename(src)} kann nicht in sich selbst liegen")
        to = posixpath.join(dest, posixpath.basename(src))
        plan.append((src, to, os.path.lexists(disk(to))))
    return dest, plan


@router.post("/api/v1/fs/conflicts")
async def conflicts(body: PasteIn):
    """Was läge im Ziel schon? Zeigt die Oberfläche vor dem Einfügen. Einfügen in denselben Ordner ist kein Konflikt."""
    _, plan = paste_plan(body)
    return {"names": [posixpath.basename(to) for src, to, there in plan if there and src != to]}


@router.post("/api/v1/fs/paste")
async def paste(body: PasteIn, req: Request):
    dest, plan = paste_plan(body)

    async def work(job: Job) -> bool:
        ok = True
        for src, to, there in plan:
            if src == to:
                if body.move:
                    continue
                to = unique(dest, posixpath.basename(src))
            elif there:
                if body.conflict == "skip":
                    job.write(f"Übersprungen: {posixpath.basename(src)}")
                    continue
                if body.conflict == "keep":
                    to = unique(dest, posixpath.basename(src), word="")
                else:
                    ok = await run_cmd(job, ["rm", "-rf", "--", disk(to)]) and ok
            ok = await run_cmd(job, ["mv", "-T", "--", disk(src), disk(to)] if body.move else ["cp", "-aT", "--", disk(src), disk(to)]) and ok
        return ok

    n = len(plan)
    return jobs(req).start(f"{n} {'Element' if n == 1 else 'Elemente'} {'verschieben' if body.move else 'kopieren'}", "fs", work).view()


class ItemsIn(BaseModel):
    items: list[str] = Field(min_length=1, max_length=2000)


@router.post("/api/v1/fs/delete")
async def delete(body: ItemsIn, req: Request):
    paths = [changeable(resolve(p, follow=False)) for p in body.items]

    async def work(job: Job) -> bool:
        return await run_cmd(job, ["rm", "-rf", "--", *[disk(p) for p in paths]])

    n = len(paths)
    return jobs(req).start(f"{n} {'Element' if n == 1 else 'Elemente'} löschen", "fs", work).view()


def is_archive(name: str) -> bool:
    return name.lower().endswith(ARCHIVES)


def unpack(src: str, dest: str) -> None:
    """zipfile und tarfile (filter="data") lassen nichts außerhalb von dest landen, auch keine Links oder setuid-Dateien."""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            z.extractall(dest)
    elif tarfile.is_tarfile(src):
        with tarfile.open(src) as t:
            t.extractall(dest, filter="data")
    else:
        raise ValueError("Das Archiv lässt sich nicht lesen")


class ExtractIn(BaseModel):
    path: str


@router.post("/api/v1/fs/extract")
async def extract(body: ExtractIn, req: Request):
    v = writable(resolve(body.path))
    regular(v)
    name = posixpath.basename(v)
    if not is_archive(name):
        raise HTTPException(400, "Nur ZIP und TAR lassen sich entpacken")
    stem = next(name[:-len(a)] for a in ARCHIVES if name.lower().endswith(a))
    folder = posixpath.dirname(v)
    dest = posixpath.join(folder, stem)
    if os.path.lexists(disk(dest)):
        dest = unique(folder, stem, word="")

    async def work(job: Job) -> bool:
        job.write(f"Entpacke nach {dest}")
        os.mkdir(disk(dest))
        try:
            await asyncio.to_thread(unpack, disk(v), disk(dest))
        except BaseException:
            shutil.rmtree(disk(dest), ignore_errors=True)
            raise
        like_parent(disk(dest))
        return True

    return {**jobs(req).start(f"{name} entpacken", "fs", work).view(), "dest": dest}


def pack(paths: list[str], target: str) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            top = os.path.dirname(p)
            if not os.path.isdir(p) or os.path.islink(p):
                if os.path.isfile(p):
                    z.write(p, os.path.relpath(p, top))
                continue
            for folder, dirs, names in os.walk(p):  # folgt keinen Links
                z.write(folder, os.path.relpath(folder, top))
                for n in names:
                    f = os.path.join(folder, n)
                    if os.path.isfile(f) and not os.path.islink(f):
                        z.write(f, os.path.relpath(f, top))


@router.post("/api/v1/fs/compress")
async def compress(body: ItemsIn, req: Request):
    paths = [writable(resolve(p, follow=False)) for p in body.items]
    folder = posixpath.dirname(paths[0])
    writable(folder)
    name = f"{posixpath.basename(paths[0]) if len(paths) == 1 else posixpath.basename(folder) or 'Archiv'}.zip"
    target = posixpath.join(folder, name)
    if os.path.lexists(disk(target)):
        target = unique(folder, name, word="")

    async def work(job: Job) -> bool:
        tmp = disk(target) + PART  # bis es fertig ist, sieht man die Datei nicht
        try:
            await asyncio.to_thread(pack, [disk(p) for p in paths], tmp)
            os.replace(tmp, disk(target))
        finally:
            Path(tmp).unlink(missing_ok=True)
        like_parent(disk(target))
        return True

    return {**jobs(req).start(f"{posixpath.basename(target)} packen", "fs", work).view(), "dest": target}


tool = Tool("files", "Dateien", "folder_open", router)
