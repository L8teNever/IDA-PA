"""IDA Health: Geräte mit ida-connect – Messwerte (CPU, RAM, Platte, Temperaturen, Lüfter, Netz) im Verlauf, Shell, Dateien,
Docker-Stacks und Updates. Geräte verbinden sich selbst (WebSocket, ausgehend); Health speichert die Messwerte kompakt in SQLite.
Siehe docs/HEALTH.md und docs/HEALTH-PROTOKOLL.md.
"""
import asyncio
import base64
import contextlib
import html
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, mesh, selfmesh, settings_ui, tasks
from .config import get_settings
from .hub import AgentError, Conn, DeviceOffline, Hub
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("health")

STATE: dict = {}


def store() -> Store:
    return STATE["store"]


def hub() -> Hub:
    return STATE["hub"]


async def _loop():
    n = 0
    while True:
        await asyncio.sleep(5)
        try:
            await asyncio.to_thread(store().flush)
            n += 1
            if n % 12 == 0:
                await asyncio.to_thread(store().compact)
        except Exception:  # noqa: BLE001
            log.exception("Speichern der Messwerte")


@contextlib.asynccontextmanager
async def lifespan(_app):
    s = get_settings()
    STATE["store"] = Store(s.data_dir / "health.db", s.store_seconds, s.raw_keep_hours, s.keep_days)
    STATE["hub"] = Hub(s.live_interval, s.idle_interval)
    STATE["csrf"] = {}
    settings_ui.apply(STATE["store"])
    settings_ui.tune_hub(STATE["hub"])
    settings_ui.new_setup_code(s.data_dir)
    task = asyncio.create_task(_loop())
    def _save(values: dict) -> None:
        store().save_overrides(values, [])
        settings_ui.apply(store())
    watch = asyncio.create_task(selfmesh.watch(STATE["hub"], _save))
    yield
    task.cancel()
    watch.cancel()
    store().close()


class MeshGuard:
    """Über die Mesh-IP von Health (Cloudflare Mesh, 100.64.0.0/10) nur den Agent-Pfad erlauben. Dort steht kein Cloudflare Access
    davor – Oberfläche und API wären sonst für jedes Gerät im Mesh offen. Geprüft wird die lokale Adresse der Verbindung
    (scope["server"]); die setzt der Kernel, Proxy-Header ändern sie nicht."""
    ALLOWED = ("/api/v1/agent/", "/api/v1/health")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            srv = scope.get("server") or ("", 0)
            if selfmesh.in_mesh(str(srv[0])) and not scope.get("path", "").startswith(self.ALLOWED):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 4403})
                else:
                    body = b'{"detail":"\xc3\xbcber das Mesh nur f\xc3\xbcr Ger\xc3\xa4te"}'
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                    await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


app = FastAPI(title="IDA Health", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(MeshGuard)
app.include_router(settings_ui.router)
User = Depends(auth.require)


@app.exception_handler(DeviceOffline)
async def _offline(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=409)


@app.exception_handler(AgentError)
async def _agent_err(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.exception_handler(mesh.MeshError)
async def _mesh_err(_r, e):
    return JSONResponse({"detail": str(e)}, status_code=502)


@app.exception_handler(httpx.HTTPError)
async def _http_err(_r, e):
    return JSONResponse({"detail": f"Cloudflare nicht erreichbar: {e}"}, status_code=502)


@app.get("/api/v1/health")
async def health():
    return {"ok": True}


# ======================= Geräte (Agent) =======================

@app.websocket("/api/v1/agent/ws")
async def agent_ws(ws: WebSocket):
    authz = ws.headers.get("authorization", "")
    await ws.accept()
    paired = None
    dev_id = ""
    if authz.startswith("Pair "):
        paired = await asyncio.to_thread(store().redeem, authz[5:].strip())
        if not paired:
            await ws.close(code=4401)
            return
        dev_id = paired["id"]
        await ws.send_text(json.dumps({"t": "paired", "id": dev_id, "secret": paired["secret"], "name": paired["name"]}))
        store().audit(dev_id, paired["who"], "gekoppelt", paired["name"])
    elif authz.startswith("Bearer ") and ":" in authz:
        dev_id, _, secret = authz[7:].strip().partition(":")
        if not await asyncio.to_thread(store().auth, dev_id, secret):
            await ws.close(code=4401)
            return
    else:
        await ws.close(code=4401)
        return

    c = Conn(dev=dev_id, ws=ws)
    await hub().attach(c)
    log.info("Gerät %s verbunden", dev_id)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 8_000_000:
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            t = m.get("t")
            if t == "m":
                store().add(dev_id, m)
                hub().publish(dev_id, {"t": "m", "dev": dev_id, "m": m})
            elif t in ("hello", "info"):
                info = m.get("info") or {}
                if isinstance(info, dict):
                    store().update_device(dev_id, info=info, last_seen=int(time.time()))
                    hub().publish(dev_id, {"t": "info", "dev": dev_id, "info": info, "online": True})
                if t == "hello" and paired and paired.get("mesh"):
                    asyncio.create_task(_mesh_join_bg(dev_id, paired["who"]))
                    paired = None
            else:
                hub().on_message(c, m)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub().detach(c)
        hub().publish(dev_id, {"t": "online", "dev": dev_id, "online": hub().online(dev_id)})
        log.info("Gerät %s getrennt", dev_id)


async def _mesh_join_bg(dev_id: str, who: str) -> None:
    """Nach dem Koppeln im Hintergrund: Fehler landen im Protokoll des Geräts."""
    try:
        await _mesh_join(dev_id, who)
    except (mesh.MeshError, AgentError, DeviceOffline, httpx.HTTPError) as e:
        store().audit(dev_id, who, "mesh", f"fehlgeschlagen: {e}")
        log.warning("Mesh für %s: %s", dev_id, e)


async def _mesh_join(dev_id: str, who: str, token: str = "") -> dict:
    d = store().device(dev_id) or {}
    node = ""
    if not token:
        node, token = await mesh.create_node(f"ida-{d.get('name') or dev_id}")
        store().update_device(dev_id, mesh_node=node)
    store().audit(dev_id, who, "mesh", f"Knoten {node or '(eigener Token)'} – Einrichtung gestartet")
    try:
        r = await hub().request(dev_id, "mesh_setup", wait=960, token=token)
        store().audit(dev_id, who, "mesh", "eingerichtet: " + str((r.get("mesh") or {}).get("ip") or ""))
        return r
    except (AgentError, DeviceOffline) as e:
        store().audit(dev_id, who, "mesh", f"fehlgeschlagen: {e}")
        raise


# ======================= Koppeln (Browser, hinter Access) =======================

def _cb_ok(cb: str) -> bool:
    u = urlsplit(cb)
    return u.scheme in ("http", "https") and bool(u.netloc) and u.path == "/pair/done" and not u.query and not u.fragment


def _local_host(netloc: str) -> bool:
    """Liegt die Seite des Geräts im eigenen Netz (privat, Loopback, CGNAT/Mesh, .local/.lan)?"""
    import ipaddress
    host = netloc.rsplit("@", 1)[-1]
    host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if host.endswith((".local", ".lan", ".home", ".internal")) or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang=de><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<meta name=color-scheme content=dark><title>{html.escape(title)}</title><link rel=stylesheet href="/static/shared/css/tokens.css">
<link rel=stylesheet href="/static/css/health.css"></head><body class=hp-page><main class=hp-pair>{body}</main></body></html>""",
                        headers={"cache-control": "no-store", "x-frame-options": "DENY"})


@app.get("/pair")
async def pair_page(request: Request, cb: str = "", state: str = "", name: str = "", host: str = "", who: str = User):
    if not _cb_ok(cb) or not state.isalnum() or len(state) > 64:
        raise HTTPException(400, "Ungültiger Aufruf – bitte auf der Seite des Geräts neu starten.")
    csrf = secrets.token_urlsafe(24)
    STATE["csrf"] = {k: v for k, v in STATE["csrf"].items() if v[0] > time.time()}
    STATE["csrf"][csrf] = (time.time() + 600, cb, state)
    e = html.escape
    local = _local_host(urlsplit(cb).netloc)
    warn = "" if local else (f'<p class=hp-warn><span class=ms>warning</span> Die Seite des Geräts ({e(urlsplit(cb).netloc)}) liegt nicht in einem '
                             'privaten Netz. Nur verbinden, wenn du das gerade selbst gestartet hast.</p>')
    mesh_box = (f'<label class=hp-check><input type=checkbox name=mesh value=1{" checked" if local else ""}> Ins Cloudflare Mesh aufnehmen '
                f'<small>(eigene IP im Mesh, Zugriff auf alles, was darauf läuft)</small></label>') if mesh.enabled() else ""
    return _page("Gerät verbinden", f"""
<h1><span class=ms>devices</span> Gerät verbinden?</h1>
<p>Ein Gerät möchte sich mit IDA Health verbinden. Danach siehst du seine Werte und kannst es über Health bedienen
(Shell, Dateien, Docker, Updates) – angemeldet als <b>{e(who)}</b>.</p>
<dl><dt>Gerät</dt><dd>{e(host or '?')}</dd><dt>Seite</dt><dd>{e(urlsplit(cb).netloc)}</dd></dl>{warn}
<form method=post action=/pair/confirm>
<input type=hidden name=csrf value="{e(csrf)}">
<label>Name in Health<input name=name value="{e(name or host)}" maxlength=60 required></label>
{mesh_box}
<div class=hp-row><a class=hp-btn href="{e(cb)}?state={e(state)}&error=abgebrochen">Abbrechen</a><button class="hp-btn primary">Verbinden</button></div>
</form>""")


@app.post("/pair/confirm")
async def pair_confirm(request: Request, who: str = User):
    if not auth.same_origin(request):
        raise HTTPException(403, "Fremde Seite")
    form = dict((k, v) for k, v in (await request.form()).items())
    entry = STATE["csrf"].pop(str(form.get("csrf") or ""), None)
    if not entry or entry[0] < time.time():
        raise HTTPException(400, "Abgelaufen – bitte auf der Seite des Geräts neu starten.")
    _, cb, state = entry
    name = str(form.get("name") or "Gerät").strip()[:60] or "Gerät"
    code = store().create_pair_code(name, bool(form.get("mesh")) and mesh.enabled(), who)
    return RedirectResponse(f"{cb}?state={quote(state)}&code={quote(code)}", status_code=303)


class PairIn(BaseModel):
    name: str = Field("Gerät", max_length=60)
    mesh: bool = True


@app.post("/api/v1/pair-codes")
async def new_pair_code(body: PairIn, who: str = User):
    """Code für ein Gerät ohne Browser: der Install-Befehl enthält ihn."""
    s = get_settings()
    code = store().create_pair_code(body.name.strip() or "Gerät", body.mesh and mesh.enabled(), who)
    server = s.public_url.rstrip("/") or "https://health.kulbarts.com"
    cmd = f"curl -fsSL {s.install_url} | sudo sh -s -- --server {server} --code {code}"
    return {"code": code, "expires_in": 600, "command": cmd}


# ======================= API =======================

def _view(d: dict) -> dict:
    return {**d, "online": hub().online(d["id"])}


def _dev(dev: str) -> dict:
    d = store().find(dev)
    if not d:
        raise HTTPException(404, "Gerät nicht gefunden")
    return d


@app.get("/api/v1/me")
async def me(request: Request):
    """Wer bin ich? Ohne Anmeldung nur, ob die Einrichtung nötig ist (für die Anmeldeseite)."""
    s = get_settings()
    w = auth.who(request.headers, request.cookies)
    if not w:
        return {"who": None, "setup": not auth.secured()}
    return {"who": w, "setup": not auth.secured(), "admin": w == "einrichtung", "mesh": mesh.enabled(), "install_url": s.install_url,
            "server": s.public_url, "access": bool(s.access_team and s.access_aud)}


@app.get("/api/v1/devices")
async def devices(_: str = User):
    return {"devices": [_view(d) for d in store().devices()]}


@app.get("/api/v1/devices/{dev}")
async def device(dev: str, _: str = User):
    return _view(_dev(dev))


class DevPatch(BaseModel):
    name: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=4000)


@app.patch("/api/v1/devices/{dev}")
async def patch_device(dev: str, body: DevPatch, who: str = User):
    d = _dev(dev)
    kw = {k: v.strip() for k, v in body.model_dump(exclude_none=True).items()}
    if kw.get("name") == "":
        raise HTTPException(400, "Name fehlt")
    store().update_device(d["id"], **kw)
    store().audit(d["id"], who, "geändert", json.dumps(kw, ensure_ascii=False)[:500])
    return _view(store().device(d["id"]))


@app.delete("/api/v1/devices/{dev}")
async def delete_device(dev: str, who: str = User):
    d = _dev(dev)
    with contextlib.suppress(mesh.MeshError, OSError, Exception):
        await mesh.delete_node(d.get("mesh_node") or "")
    c = hub().conns.get(d["id"])
    store().delete_device(d["id"])
    store().audit(d["id"], who, "gelöscht", d["name"])
    if c:
        with contextlib.suppress(Exception):
            await c.ws.close(code=4401)
    return {"ok": True}


@app.get("/api/v1/devices/{dev}/history")
async def history(dev: str, range: int = 3600, start: int | None = None, end: int | None = None, points: int = 300, _: str = User):
    d = _dev(dev)
    now = int(time.time())
    end = min(end or now + 1, now + 1)  # ts < end: der Wert von eben gehört dazu
    start = start or end - max(60, min(range, 400 * 86400))
    await asyncio.to_thread(store().flush)
    return await asyncio.to_thread(store().series, d["id"], start, end, max(10, min(points, 2000)))


@app.get("/api/v1/devices/{dev}/audit")
async def audit(dev: str, _: str = User):
    return {"items": store().audit_list(_dev(dev)["id"])}


class ExecIn(BaseModel):
    cmd: str = Field(..., min_length=1, max_length=100_000)
    timeout: int = 60
    user: str = ""


@app.post("/api/v1/devices/{dev}/exec")
async def exec_cmd(dev: str, body: ExecIn, who: str = User):
    d = _dev(dev)
    store().audit(d["id"], who, "befehl", body.cmd)
    return await hub().exec(d["id"], body.cmd, body.timeout, body.user)


# ---- Dateien ----
TEXT_MAX = 2 * 1024 * 1024
CHUNK = 2 * 1024 * 1024


@app.get("/api/v1/devices/{dev}/fs/list")
async def fs_list(dev: str, path: str = "/", _: str = User):
    return await hub().request(_dev(dev)["id"], "fs_list", wait=30, path=path or "/")


@app.get("/api/v1/devices/{dev}/fs/read")
async def fs_read(dev: str, path: str, _: str = User):
    """Textdatei (bis 2 MB) zum Ansehen/Bearbeiten."""
    r = await hub().request(_dev(dev)["id"], "fs_read", wait=60, path=path, offset=0, len=TEXT_MAX)
    data = base64.b64decode(r.get("d") or "")
    binary = b"\0" in data[:8000]
    return {"path": path, "size": r.get("size"), "truncated": not r.get("eof"), "binary": binary,
            "content": "" if binary else data.decode("utf-8", errors="replace")}


@app.get("/api/v1/devices/{dev}/fs/download")
async def fs_download(dev: str, path: str, _: str = User):
    d = _dev(dev)
    first = await hub().request(d["id"], "fs_read", wait=60, path=path, offset=0, len=CHUNK)

    async def gen():
        r, off = first, 0
        while True:
            b = base64.b64decode(r.get("d") or "")
            yield b
            off += len(b)
            if r.get("eof") or not b:
                break
            r = await hub().request(d["id"], "fs_read", wait=60, path=path, offset=off, len=CHUNK)

    name = path.rstrip("/").rsplit("/", 1)[-1] or "datei"
    return StreamingResponse(gen(), media_type="application/octet-stream",
                             headers={"content-disposition": f"attachment; filename*=UTF-8''{quote(name)}", "content-length": str(first.get("size") or 0)})


class WriteIn(BaseModel):
    path: str
    content: str | None = None
    b64: str | None = None


@app.post("/api/v1/devices/{dev}/fs/write")
async def fs_write(dev: str, body: WriteIn, who: str = User):
    d = _dev(dev)
    data = base64.b64decode(body.b64) if body.b64 else (body.content or "").encode()
    if len(data) > 8 * 1024 * 1024:  # eine WebSocket-Nachricht ans Gerät (Base64) – Größeres geht in Stücken über /fs/upload
        raise HTTPException(413, "Zu groß – über Hochladen schicken")
    store().audit(d["id"], who, "datei schreiben", body.path)
    return await hub().request(d["id"], "fs_write", wait=120, path=body.path, d=base64.b64encode(data).decode(), append=False, mkdirs=True)


@app.post("/api/v1/devices/{dev}/fs/upload")
async def fs_upload(dev: str, path: str, request: Request, who: str = User):
    """Body = die Datei selbst. Geht in Stücken ans Gerät (erst neu anlegen, dann anhängen)."""
    if not auth.same_origin(request):
        raise HTTPException(403, "Fremde Seite")
    d = _dev(dev)
    tmp = path + ".ida-upload"
    buf, total, first = b"", 0, True
    async for chunk in request.stream():
        buf += chunk
        while len(buf) >= CHUNK:
            part, buf = buf[:CHUNK], buf[CHUNK:]
            await hub().request(d["id"], "fs_write", wait=120, path=tmp, d=base64.b64encode(part).decode(), append=not first, mkdirs=True)
            first, total = False, total + len(part)
    if buf or first:
        await hub().request(d["id"], "fs_write", wait=120, path=tmp, d=base64.b64encode(buf).decode(), append=not first, mkdirs=True)
        total += len(buf)
    await hub().request(d["id"], "fs_rename", wait=30, **{"from": tmp, "to": path})
    store().audit(d["id"], who, "hochgeladen", f"{path} ({total} B)")
    return {"ok": True, "size": total}


class PathIn(BaseModel):
    path: str
    recursive: bool = False
    to: str = ""


@app.post("/api/v1/devices/{dev}/fs/{op}")
async def fs_op(dev: str, op: str, body: PathIn, who: str = User):
    d = _dev(dev)
    if op == "delete":
        store().audit(d["id"], who, "löschen", body.path)
        return await hub().request(d["id"], "fs_delete", wait=120, path=body.path, recursive=body.recursive)
    if op == "mkdir":
        return await hub().request(d["id"], "fs_mkdir", wait=30, path=body.path)
    if op == "rename":
        store().audit(d["id"], who, "umbenennen", f"{body.path} -> {body.to}")
        return await hub().request(d["id"], "fs_rename", wait=30, **{"from": body.path, "to": body.to})
    raise HTTPException(404)


# ---- Docker ----
@app.get("/api/v1/devices/{dev}/docker")
async def docker(dev: str, _: str = User):
    return await tasks.docker_overview(hub(), _dev(dev)["id"])


class ActionIn(BaseModel):
    name: str
    action: str


@app.post("/api/v1/devices/{dev}/docker/stack")
async def docker_stack(dev: str, body: ActionIn, who: str = User):
    d = _dev(dev)
    store().audit(d["id"], who, f"stack {body.action}", body.name)
    return await tasks.stack_action(hub(), d["id"], body.name, body.action)


@app.get("/api/v1/devices/{dev}/docker/stack/files")
async def docker_stack_files(dev: str, name: str, _: str = User):
    return {"files": await tasks.stack_files(hub(), _dev(dev)["id"], name)}


@app.post("/api/v1/devices/{dev}/docker/container")
async def docker_container(dev: str, body: ActionIn, who: str = User):
    d = _dev(dev)
    store().audit(d["id"], who, f"container {body.action}", body.name)
    return await tasks.container_action(hub(), d["id"], body.name, body.action)


@app.get("/api/v1/devices/{dev}/docker/logs")
async def docker_logs(dev: str, name: str, tail: int = 200, _: str = User):
    return {"logs": await tasks.container_logs(hub(), _dev(dev)["id"], name, tail)}


# ---- Updates, Mesh ----
@app.get("/api/v1/devices/{dev}/updates")
async def updates(dev: str, _: str = User):
    return await tasks.updates_check(hub(), _dev(dev)["id"])


class UpdateIn(BaseModel):
    target: str = "system"  # system | agent


@app.post("/api/v1/devices/{dev}/update")
async def update(dev: str, body: UpdateIn, who: str = User):
    d = _dev(dev)
    store().audit(d["id"], who, f"update {body.target}")
    if body.target == "agent":
        return await hub().request(d["id"], "update", wait=60)
    if body.target == "system":
        return await tasks.system_update(hub(), d["id"])
    raise HTTPException(400, "target: system oder agent")


@app.get("/api/v1/devices/{dev}/agent-update")
async def agent_update_check(dev: str, _: str = User):
    """Gibt es eine neue Version von ida-connect? (Das Gerät fragt GitHub.)"""
    return await hub().request(_dev(dev)["id"], "update_check", wait=45)


class AutoUpdateIn(BaseModel):
    on: bool


@app.post("/api/v1/devices/{dev}/auto-update")
async def agent_auto_update(dev: str, body: AutoUpdateIn, who: str = User):
    d = _dev(dev)
    store().audit(d["id"], who, "auto-update", "an" if body.on else "aus")
    return await hub().request(d["id"], "set_auto_update", wait=15, on=body.on)


class MeshIn(BaseModel):
    token: str = ""


@app.post("/api/v1/devices/{dev}/mesh")
async def mesh_setup(dev: str, body: MeshIn, who: str = User):
    d = _dev(dev)
    if not hub().online(d["id"]):
        raise DeviceOffline("Gerät ist gerade nicht verbunden")
    return await _mesh_join(d["id"], who, body.token.strip())


# ======================= Live (Browser) =======================

@app.websocket("/api/v1/live")
async def live(ws: WebSocket):
    if not auth.require_ws(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    focus = ws.query_params.get("dev") or None
    vid, q = hub().watch(focus)
    await hub().tune()

    async def reader():
        while True:
            m = json.loads(await ws.receive_text())
            if m.get("t") == "focus":
                hub().refocus(vid, m.get("dev") or None)
                await hub().tune()

    rt = asyncio.create_task(reader())
    try:
        while not rt.done():
            try:
                msg = await asyncio.wait_for(q.get(), 25)
            except asyncio.TimeoutError:
                msg = {"t": "ping"}
            await ws.send_text(json.dumps(msg, separators=(",", ":")))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.cancel()
        hub().unwatch(vid)
        await hub().tune()


@app.websocket("/api/v1/devices/{dev}/shell")
async def shell(ws: WebSocket, dev: str):
    who = auth.require_ws(ws)
    if not who:
        await ws.close(code=4401)
        return
    d = store().find(dev)
    await ws.accept()
    if not d or not hub().online(d["id"]):
        await ws.send_text(json.dumps({"t": "exit", "code": -1, "error": "Gerät ist gerade nicht verbunden"}))
        await ws.close()
        return
    cols = int(ws.query_params.get("cols") or 120)
    rows = int(ws.query_params.get("rows") or 32)
    sid, q = await hub().pty_open(d["id"], cols, rows)
    store().audit(d["id"], who, "shell", "geöffnet")

    async def reader():
        while True:
            m = json.loads(await ws.receive_text())
            if m.get("t") == "in":
                await hub().pty_input(d["id"], sid, str(m.get("d") or "").encode())
            elif m.get("t") == "resize":
                await hub().pty_send(d["id"], {"t": "pty_resize", "sid": sid, "cols": int(m.get("cols") or 120), "rows": int(m.get("rows") or 32)})

    rt = asyncio.create_task(reader())
    try:
        while not rt.done():
            try:
                msg = await asyncio.wait_for(q.get(), 25)
            except asyncio.TimeoutError:
                continue
            await ws.send_text(json.dumps(msg))
            if msg.get("t") == "exit":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        rt.cancel()
        await hub().pty_close(d["id"], sid)
        with contextlib.suppress(Exception):
            await ws.close()


# ======================= Oberfläche =======================

class Static(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["cache-control"] = "no-cache"
        return resp


def _fe() -> Path:
    return get_settings().frontend_dir


@app.get("/manifest.webmanifest")
async def manifest():
    return JSONResponse({"name": "IDA Health", "short_name": "Health", "start_url": "/", "display": "standalone",
                         "background_color": "#000000", "theme_color": "#000000",
                         "icons": [{"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                                   {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}]},
                        media_type="application/manifest+json")


@app.get("/")
async def index():
    """Die Seite selbst ist frei (nur HTML/JS); alle Daten kommen über die API mit Anmeldung."""
    root = _fe() / "health"
    files = [root / "js" / "health.js", root / "js" / "settings.js", root / "css" / "health.css"]
    v = max((int(f.stat().st_mtime) for f in files if f.exists()), default=0)
    page = (root / "index.html").read_text(encoding="utf-8").replace("__V__", str(v))
    return HTMLResponse(page, headers={"cache-control": "no-cache"})


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


app.mount("/static/shared/css", Static(directory=_fe() / "css", check_dir=False), name="shared-css")
app.mount("/static/shared/fonts", Static(directory=_fe() / "fonts", check_dir=False), name="shared-fonts")
app.mount("/static/shared/dashcss", Static(directory=_fe() / "dashboard" / "css", check_dir=False), name="shared-dashcss")
app.mount("/static/shared/dashboard", Static(directory=_fe() / "dashboard" / "js", check_dir=False), name="shared-dash")
app.mount("/static/vendor", Static(directory=_fe() / "vendor", check_dir=False), name="vendor")
app.mount("/icons", Static(directory=_fe() / "launcher" / "icons", check_dir=False), name="icons")
app.mount("/static", Static(directory=_fe() / "health", check_dir=False), name="static")
