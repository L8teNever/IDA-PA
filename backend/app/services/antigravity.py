"""Google Antigravity as a second model provider – the same way T3 Code does it.

Google's official Antigravity ACP agent (`agy_acp_server`, Agent Client Protocol = JSON-RPC over stdin/stdout)
runs on the server:
  - Runtime: downloaded once from Google (official ACP registry, checksum verified) into data/agy-runtime/<version>
    ("managed runtime", about 1 GB unpacked).
  - One agent process per account with its own profile (GEMINI_HOME = data/agy/<id>); the Google login is stored
    there as a file (antigravity-acp/acp_token.json) and survives restarts.
  - Sign-in: the agent prints a Google link (its browser launch is caught), Google returns to http://127.0.0.1:<port>/
    – the user copies that return address into IDA, the server delivers it to the agent.
  - Models come from the account (session/new -> config option "model"), offered as "agy:<model>".
  - Limits (5-hour / weekly per model family) come from Google's quota endpoint with the stored login.
  - Runs: a fresh session per run with the run's MCP servers; tool permission requests are only granted for
    IDA's MCP tools (no shell, no file edits).
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from ..config import get_settings
from ..models import Account
from ..models._types import new_id
from .errors import Invalid

log = logging.getLogger("ida.agy")
PREFIX = "agy:"
VERSION = "1.4.3"
ARCHIVE = f"https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-{VERSION}-linux-x86_64.zip"
SHA256 = "0634ce5306ba92c932945455e7a83fda78e2d99c33fa8aa6ee0566206a382714"
EXE, HARNESS = "agy_acp_server.par", "localharness_external"
AUTH_PREFIX = "Open the following link to authenticate the ACP server: "
URL_MARK = "__IDA_AUTH_URL__"
IDLE_LIMIT = 15 * 60
QUOTA_ENDPOINTS = ("https://daily-cloudcode-pa.googleapis.com", "https://cloudcode-pa.googleapis.com")


class NotLoggedIn(Invalid):
    pass


def is_model(m: str | None) -> bool:
    return bool(m) and m.startswith(PREFIX) and len(m) > len(PREFIX)


# ---------------- managed runtime ----------------
def runtime_dir() -> Path:
    override = get_settings().agy_runtime_dir
    return Path(override) if override else get_settings().data_dir / "agy-runtime" / VERSION


def installed() -> bool:
    d = runtime_dir()
    return (d / EXE).exists() and (d / HARNESS).exists() and (d / ".ok").exists()


_install: dict = {"phase": "idle", "done": 0, "total": 0, "error": None}
_install_task: asyncio.Task | None = None


def install_state() -> dict:
    return {**_install, "installed": installed(), "version": VERSION}


async def _do_install() -> None:
    d = runtime_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d.parent / f"download-{VERSION}.zip"
    try:
        _install.update(phase="downloading", done=0, total=0, error=None)
        h = hashlib.sha256()
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, read=120), follow_redirects=True) as c:
            async with c.stream("GET", ARCHIVE) as r:
                r.raise_for_status()
                _install["total"] = int(r.headers.get("content-length") or 0)
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes(1 << 20):
                        f.write(chunk)
                        h.update(chunk)
                        _install["done"] += len(chunk)
        if h.hexdigest() != SHA256:
            raise RuntimeError("Prüfsumme stimmt nicht – Download beschädigt")
        _install["phase"] = "extracting"

        def extract():
            with zipfile.ZipFile(tmp) as z:
                for name in (EXE, HARNESS):
                    with z.open(name) as src, (d / name).open("wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    (d / name).chmod(0o755)
            (d / ".ok").write_text(VERSION)
        await asyncio.to_thread(extract)
        _install.update(phase="done")
        log.info("antigravity runtime %s installed", VERSION)
    except Exception as e:  # noqa: BLE001
        _install.update(phase="error", error=str(e)[:300])
        log.warning("antigravity runtime install failed: %s", e)
    finally:
        tmp.unlink(missing_ok=True)


def start_install() -> dict:
    global _install_task
    if not installed() and not (_install_task and not _install_task.done()):
        _install_task = asyncio.get_running_loop().create_task(_do_install())
    return install_state()


# ---------------- profile per account ----------------
def home(account_id: str) -> Path:
    d = get_settings().data_dir / "agy" / account_id
    (d / "antigravity-acp" / "tmp").mkdir(parents=True, exist_ok=True)
    (d / "work").mkdir(exist_ok=True)
    settings = d / "antigravity-acp" / "settings.json"
    if not settings.exists():
        settings.write_text(json.dumps({"auth": {"type": "oauth-personal"}}) + "\n")
    return d


def _browser_helper() -> Path:
    """The agent "opens the browser" with this script – it only reports the link (on stderr)."""
    p = get_settings().data_dir / "agy" / "browser.sh"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(f'#!/bin/sh\necho "{URL_MARK}$1" >&2\n')
        p.chmod(0o755)
    return p


def env(account: Account) -> dict[str, str]:
    """No server secrets: only what the agent needs."""
    h = home(account.id)
    e = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(h), "LANG": "C.UTF-8",
         "GEMINI_HOME": str(h), "AGY_ACP_FORCE_FILE_STORAGE": "1", "BROWSER": f"{_browser_helper()} %s",
         "PYTHONUNBUFFERED": "1", "TMPDIR": str(h / "antigravity-acp" / "tmp"),
         "ANTIGRAVITY_HARNESS_PATH": str(runtime_dir() / HARNESS), "TZ": get_settings().timezone}
    for ca in ("/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/certs/ca-bundle.crt", "/etc/ssl/cert.pem"):
        if os.path.exists(ca):
            e["SSL_CERT_FILE"] = ca
            break
    if os.name == "nt":
        e["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return e


def token_file(account_id: str) -> Path:
    return get_settings().data_dir / "agy" / account_id / "antigravity-acp" / "acp_token.json"


# ---------------- status (login + models), cached in status.json ----------------
def _status_file(account_id: str) -> Path:
    return get_settings().data_dir / "agy" / account_id / "status.json"


def status(account: Account) -> dict:
    try:
        st = json.loads(_status_file(account.id).read_text(encoding="utf8"))
    except (OSError, ValueError):
        st = {"models": []}
    st["models"] = [m if isinstance(m, dict) else {"id": str(m), "name": str(m)} for m in st.get("models") or [] if m]
    st["logged_in"] = token_file(account.id).exists()
    return st


def _save_status(account_id: str, **kw) -> None:
    f = _status_file(account_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(f.read_text(encoding="utf8"))
    except (OSError, ValueError):
        cur = {}
    cur.update(kw)
    f.write_text(json.dumps(cur, indent=1), encoding="utf8")


def logged_in(account: Account) -> bool:
    return token_file(account.id).exists()


def models(accounts: list[Account]) -> list[str]:
    out: list[str] = []
    for a in accounts:
        if a.provider == "antigravity" and logged_in(a):
            out += [PREFIX + m["id"] for m in status(a).get("models", [])]
    return list(dict.fromkeys(out))


def model_names(accounts: list[Account]) -> dict[str, str]:
    out = {}
    for a in accounts:
        if a.provider == "antigravity":
            for m in status(a).get("models", []):
                out[PREFIX + m["id"]] = m.get("name") or m["id"]
    return out


def cached_models() -> list[str]:
    out: list[str] = []
    for f in sorted((get_settings().data_dir / "agy").glob("*/status.json")):
        if not (f.parent / "antigravity-acp" / "acp_token.json").exists():
            continue
        try:
            out += [PREFIX + (m["id"] if isinstance(m, dict) else str(m)) for m in json.loads(f.read_text(encoding="utf8")).get("models", [])]
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(dict.fromkeys(out))


# ---------------- ACP client (one agent process per account) ----------------
class Client:
    def __init__(self, account: Account):
        self.account_id = account.id
        self.account = account
        self.proc: asyncio.subprocess.Process | None = None
        self.next_id = 0
        self.pending: dict[int, asyncio.Future] = {}
        self.sessions: dict[str, asyncio.Queue] = {}
        self.allowed_servers: dict[str, set[str]] = {}
        self.auth_url: str | None = None
        self.auth_seen = asyncio.Event()
        self.auth_task: asyncio.Task | None = None
        self.initialized = False
        self.authenticated = False
        self.last_used = time.time()
        self.tasks: list[asyncio.Task] = []
        self.lock = asyncio.Lock()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        if not installed():
            raise Invalid("Die Antigravity-Laufzeit ist noch nicht installiert (Einstellungen → Accounts)")
        h = home(self.account_id)
        exe = str(runtime_dir() / EXE)
        argv = [exe, "--uid="] if os.name != "nt" else [sys.executable, exe]  # Windows: only the tests (fake agent script)
        self.proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(h / "work"), env=env(self.account),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024, **({"start_new_session": True} if os.name != "nt" else {}))
        self.tasks = [asyncio.create_task(self._read_out()), asyncio.create_task(self._read_err())]

    def _auth_line(self, line: str) -> bool:
        url = line[len(AUTH_PREFIX):] if line.startswith(AUTH_PREFIX) else line[len(URL_MARK):] if line.startswith(URL_MARK) else None
        if url and url.startswith("https://accounts.google.com/"):
            self.auth_url = url.strip()
            self.auth_seen.set()
            return True
        return False

    async def _read_err(self) -> None:
        assert self.proc and self.proc.stderr
        async for raw in self.proc.stderr:
            line = raw.decode(errors="replace").rstrip("\r\n")
            if not self._auth_line(line) and line:
                log.debug("agy %s: %s", self.account_id, line[:300])

    async def _read_out(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            async for raw in self.proc.stdout:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if not line.startswith("{"):
                    self._auth_line(line)
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                await self._dispatch(msg)
        finally:
            err = Invalid("Der Antigravity-Agent wurde beendet")
            for f in self.pending.values():
                if not f.done():
                    f.set_exception(err)
            for q in self.sessions.values():
                q.put_nowait(None)

    async def _dispatch(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            f = self.pending.pop(msg["id"], None)
            if f and not f.done():
                if "error" in msg:
                    e = msg["error"] or {}
                    f.set_exception(Invalid(f"Antigravity: {e.get('message') or e}"[:600]))
                else:
                    f.set_result(msg.get("result"))
            return
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "session/update":
            q = self.sessions.get(params.get("sessionId"))
            if q:
                q.put_nowait(params.get("update") or {})
            return
        if "id" in msg:  # a request from the agent to us
            if method == "session/request_permission":
                await self._send({"jsonrpc": "2.0", "id": msg["id"], "result": self._permission(params)})
            else:
                await self._send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"{method} wird nicht unterstützt"}})

    def _permission(self, params: dict) -> dict:
        """Only IDA's MCP tools may run – shell, file edits and everything else are rejected."""
        call = params.get("toolCall") or {}
        servers = self.allowed_servers.get(params.get("sessionId"), set())
        text = json.dumps(call, ensure_ascii=False).lower()
        is_mcp = any(s.lower() in text for s in servers) or "mcp" in str(call.get("title", "")).lower()
        kind = "allow_once" if is_mcp and call.get("kind") not in ("execute", "edit", "delete", "move") else "reject_once"
        opt = next((o for o in params.get("options") or [] if o.get("kind") == kind), None)
        with contextlib.suppress(OSError):
            with (home(self.account_id) / "permissions.jsonl").open("a", encoding="utf8") as f:
                f.write(json.dumps({"at": time.time(), "decision": kind, "call": call}, ensure_ascii=False)[:2000] + "\n")
        return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}} if opt else {"outcome": {"outcome": "cancelled"}}

    async def _send(self, msg: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: dict, timeout: float = 120) -> dict:
        if not self.alive:
            raise Invalid("Der Antigravity-Agent läuft nicht")
        self.last_used = time.time()
        self.next_id += 1
        rid = self.next_id
        fut = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    async def initialize(self) -> None:
        if self.initialized:
            return
        await self.request("initialize", {"protocolVersion": 1, "clientInfo": {"name": "ida", "version": "2.0"},
                                          "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}}, 90)
        self.initialized = True

    async def authenticate(self, interactive: bool) -> None:
        """Without a stored login the agent shows a Google link: for runs that means "not signed in"."""
        if self.authenticated:
            return
        task = asyncio.create_task(self.request("authenticate", {"methodId": "oauth-personal"}, 330))
        url_wait = asyncio.create_task(self.auth_seen.wait())
        done, _ = await asyncio.wait({task, url_wait}, return_when=asyncio.FIRST_COMPLETED, timeout=None if interactive else 60)
        if task in done:
            url_wait.cancel()
            task.result()
            self.authenticated = True
            return
        if not interactive:
            task.cancel()
            url_wait.cancel()
            raise NotLoggedIn("Antigravity-Account ist nicht angemeldet (Einstellungen → Accounts → Anmelden)")
        url_wait.cancel()
        self.auth_task = task

    def stop(self) -> None:
        if self.alive:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                if os.name != "nt":
                    os.killpg(self.proc.pid, 9)
                else:
                    self.proc.kill()
        for t in self.tasks:
            t.cancel()
        tmp = home(self.account_id) / "antigravity-acp" / "tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)


_clients: dict[str, Client] = {}
_logins: dict[str, dict] = {}


async def client_for(account: Account) -> Client:
    c = _clients.get(account.id)
    if c and c.alive and c.authenticated:
        c.account = account
        return c
    if c:
        c.stop()
    c = Client(account)
    _clients[account.id] = c
    async with c.lock:
        await c.start()
        await c.initialize()
        await c.authenticate(interactive=False)
    return c


def reap() -> None:
    """Ends agents that were idle for a while (each one keeps about 1 GB unpacked)."""
    now = time.time()
    for aid, c in list(_clients.items()):
        if aid in _logins:
            continue
        if (not c.sessions and now - c.last_used > IDLE_LIMIT) or not c.alive:
            c.stop()
            _clients.pop(aid, None)


# ---------------- sign-in (like T3 Code) ----------------
async def start_login(account: Account) -> dict:
    if not installed():
        start_install()
        return {"phase": "installing", "install": install_state()}
    old = _clients.pop(account.id, None)
    if old:
        old.stop()
    c = Client(account)
    _clients[account.id] = c
    await c.start()
    await c.initialize()
    await c.authenticate(interactive=True)
    if c.authenticated:  # still signed in
        await load_models(account, c)
        _logins.pop(account.id, None)
        return {"phase": "done"}
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(c.auth_seen.wait(), 60)
    if not c.auth_url:
        c.stop()
        raise Invalid("Antigravity hat keinen Google-Link geliefert (Netzwerk des Servers prüfen) – bitte nochmal versuchen")
    q = parse_qs(urlparse(c.auth_url).query)
    _logins[account.id] = {"phase": "waiting", "url": c.auth_url, "redirect": (q.get("redirect_uri") or [""])[0],
                           "state": (q.get("state") or [""])[0], "started": time.time()}
    return {"phase": "waiting", "url": c.auth_url}


def login_state(account: Account) -> dict:
    st = _logins.get(account.id)
    if not st:
        return {"phase": "done" if logged_in(account) else "idle", "install": install_state()}
    return {**{k: v for k, v in st.items() if k in ("phase", "url", "message")}, "install": install_state()}


async def finish_login(account: Account, callback_url: str) -> dict:
    st = _logins.get(account.id)
    c = _clients.get(account.id)
    if not st or not c or not c.auth_task:
        raise Invalid("Keine laufende Anmeldung – bitte „Anmelden“ nochmal drücken")
    cb = urlparse(callback_url.strip())
    exp = urlparse(st["redirect"])
    q = parse_qs(cb.query)
    if cb.scheme != "http" or cb.hostname != "127.0.0.1" or cb.port != exp.port or (q.get("state") or [""])[0] != st["state"]:
        raise Invalid("Das ist nicht die Rückkehr-Adresse dieser Anmeldung – die komplette Adresse aus der Adresszeile einfügen (beginnt mit http://127.0.0.1:)")
    if not q.get("code") and not q.get("error"):
        raise Invalid("In der Adresse fehlt der Anmelde-Code – bitte die komplette Adresse kopieren")
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as h:
            await h.get(f"http://127.0.0.1:{cb.port}{cb.path or '/'}?{cb.query}")
    except httpx.HTTPError as e:
        raise Invalid(f"Antwort konnte nicht an Antigravity übergeben werden ({e.__class__.__name__}) – Anmeldung neu starten") from e
    try:
        await asyncio.wait_for(asyncio.shield(c.auth_task), 90)
    except Exception as e:  # noqa: BLE001
        msg = str(e) or e.__class__.__name__
        if "SUBSCRIPTION_REQUIRED" in msg:
            msg = "Google verlangt für dieses Konto ein passendes Antigravity-Abo."
        _logins[account.id] = {**st, "phase": "error", "message": msg[:300]}
        raise Invalid(f"Anmeldung fehlgeschlagen: {msg}") from e
    c.authenticated = True
    _logins.pop(account.id, None)
    found = await load_models(account, c)
    return {"phase": "done", "models": found}


async def logout(account: Account) -> None:
    c = _clients.pop(account.id, None)
    if c:
        with contextlib.suppress(Exception):
            if c.alive and c.initialized:
                await c.request("logout", {}, 15)
        c.stop()
    token_file(account.id).unlink(missing_ok=True)
    _logins.pop(account.id, None)
    _save_status(account.id, models=[])


# ---------------- models ----------------
def _parse_models(result: dict) -> list[dict]:
    out: list[dict] = []
    for opt in (result or {}).get("configOptions") or []:
        if opt.get("id") == "model" and opt.get("type") == "select":
            for entry in opt.get("options") or []:
                for o in (entry.get("options") if "options" in entry else [entry]):
                    if o.get("value"):
                        out.append({"id": o["value"], "name": o.get("name") or o["value"], "desc": o.get("description") or ""})
    if not out:
        for m in ((result or {}).get("models") or {}).get("availableModels") or []:
            if m.get("modelId"):
                out.append({"id": m["modelId"], "name": m.get("name") or m["modelId"], "desc": m.get("description") or ""})
    return out


async def load_models(account: Account, c: Client | None = None) -> list[dict]:
    c = c or await client_for(account)
    res = await c.request("session/new", {"cwd": str(home(account.id) / "work"), "mcpServers": []}, 120)
    found = _parse_models(res)
    if found:
        _save_status(account.id, models=found, checked=time.time())
    return found


# ---------------- limits (like T3 Code: Google's quota endpoint with the stored login) ----------------
_access: dict[str, tuple[str, float]] = {}
_usage_cache: dict[str, tuple[float, dict]] = {}
USAGE_TTL = 180


async def usage(account: Account, force: bool = False) -> dict:
    at, data = _usage_cache.get(account.id, (0, None))
    if data and not force and time.time() - at < USAGE_TTL:
        return {**data, "fetched_at": at}
    out = await _fetch_usage(account)
    if "windows" in out:
        _usage_cache[account.id] = (time.time(), out)
        return {**out, "fetched_at": time.time()}
    return {**data, "fetched_at": at, "stale": True, "note": out.get("error")} if data else out


async def _fetch_usage(account: Account) -> dict:
    tf = token_file(account.id)
    if not tf.exists():
        return {"error": "Nicht angemeldet – Einstellungen → Accounts → Anmelden"}
    try:
        tok = json.loads(tf.read_text(encoding="utf8"))
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as h:
            access, until = _access.get(account.id, ("", 0))
            if until < time.time() + 60:
                r = await h.post("https://oauth2.googleapis.com/token", data={
                    "client_id": tok["client_id"], "client_secret": tok["client_secret"],
                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
                r.raise_for_status()
                j = r.json()
                access = j["access_token"]
                _access[account.id] = (access, time.time() + float(j.get("expires_in", 3000)))
            headers = {"Authorization": f"Bearer {access}",
                       "User-Agent": "antigravity/hub/1.0.0 (aidev_client; os_type=linux; arch=x64; cl=963137146)"}
            body = {"project": tok["project_id"]} if tok.get("project_id") else {}

            async def call(op: str) -> list[dict]:
                outs = []
                for ep in QUOTA_ENDPOINTS:
                    with contextlib.suppress(httpx.HTTPError, ValueError):
                        rr = await h.post(f"{ep}/v1internal:{op}", headers=headers, json=body)
                        if rr.status_code == 200:
                            outs.append(rr.json())
                return outs
            windows = _summary_windows(await call("retrieveUserQuotaSummary"))
            if not windows:
                windows = _model_windows(await call("fetchAvailableModels"))
    except (OSError, ValueError, KeyError, httpx.HTTPError) as e:
        return {"error": f"Limits konnten nicht gelesen werden: {e.__class__.__name__}"}
    if not windows:
        return {"error": "Antigravity hat keine Limit-Angaben geliefert"}
    return {"windows": windows}


def _window(wid: str, label: str, q: dict) -> dict | None:
    frac = q.get("remainingFraction")
    if not isinstance(frac, (int, float)):
        return None
    return {"id": wid, "label": label, "percent": round((1 - max(0.0, min(1.0, float(frac)))) * 100, 1), "resets_at": q.get("resetTime")}


def _merge(windows: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for w in windows:
        if w["id"] not in best or w["percent"] > best[w["id"]]["percent"]:
            best[w["id"]] = w
    return list(best.values())


def _summary_windows(responses: list[dict]) -> list[dict]:
    out = []
    for s in responses:
        groups = s.get("groups") if any(g.get("buckets") for g in s.get("groups") or []) else [{"buckets": s.get("buckets")}]
        for g in groups or []:
            for b in g.get("buckets") or []:
                if b.get("disabled") or not b.get("bucketId"):
                    continue
                bid = b["bucketId"]
                fam = "Gemini" if bid.startswith("gemini-") else "Claude & GPT" if bid.startswith("3p-") else (g.get("displayName") or bid)
                win = b.get("window")
                label = f"{fam} · " + ("Woche" if win == "weekly" else "5 Std" if win == "5h" else (b.get("displayName") or "Kontingent"))
                w = _window(bid, label, b)
                if w:
                    out.append(w)
    return _merge(out)


def _model_windows(responses: list[dict]) -> list[dict]:
    out = []
    for s in responses:
        for mid, m in (s.get("models") or {}).items():
            prov = m.get("modelProvider") or ""
            fam = ("Gemini" if "GOOGLE" in prov or mid.startswith("gemini-") else "Claude" if "ANTHROPIC" in prov or mid.startswith("claude-")
                   else "GPT" if "OPENAI" in prov or mid.startswith("gpt-") else None)
            if not fam:
                continue
            for suffix, q in (("quota", m.get("quotaInfo")), ("weekly", m.get("weeklyQuotaInfo"))):
                w = _window(f"{fam.lower()}-{suffix}", f"{fam} · {'Woche' if suffix == 'weekly' else 'Kontingent'}", q or {})
                if w:
                    out.append(w)
    return _merge(out)


async def exhausted(account: Account) -> bool:
    u = await usage(account)
    return any((w.get("percent") or 0) >= 99.5 for w in u.get("windows") or [])


# ---------------- runs ----------------
def _prompt(spec) -> str:
    return f"<anweisungen>\n{spec.system}\n</anweisungen>\n\n{spec.prompt}" if spec.system else spec.prompt


def _content_text(c) -> str:
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(_content_text(x) for x in c)
    if isinstance(c, dict):
        if c.get("type") == "text":
            return c.get("text") or ""
        if "content" in c:
            return _content_text(c["content"])
        if "text" in c:
            return str(c["text"])
    return ""


def _tool_name(call: dict, servers: list[str]) -> tuple[str, str]:
    title = str(call.get("title") or call.get("toolName") or "")
    raw = json.dumps(call.get("rawInput") or {}, ensure_ascii=False)
    for s in servers:
        m = re.search(rf"{re.escape(s)}[_:./ ]{{1,3}}([\w-]+)", title) or re.search(rf"{re.escape(s)}[_:./]{{1,3}}([\w-]+)", raw)
        if m:
            return s, m.group(1)
    return "", title or "Tool"


async def stream(spec) -> AsyncIterator[dict]:
    """Same events as claude.runner.stream. A fresh session per run (chats send their history along)."""
    s = get_settings()
    acc = spec.account
    try:
        c = await client_for(acc)
    except Invalid as e:
        yield {"type": "done", "ok": False, "result": str(e), "session_id": None}
        return
    run_dir = s.sandbox_dir / "agy-runs" / new_id()
    run_dir.mkdir(parents=True, exist_ok=True)
    servers = [{"type": "http", "name": k, "url": v["url"], "headers": [{"name": hk, "value": hv} for hk, hv in (v.get("headers") or {}).items()]}
               for k, v in spec.mcp_servers.items()]
    raw_log = home(acc.id) / "last_run.jsonl"
    sid = None
    try:
        res = await c.request("session/new", {"cwd": str(run_dir), "mcpServers": servers}, 120)
        sid = res["sessionId"]
        found = _parse_models(res)
        if found:
            _save_status(acc.id, models=found, checked=time.time())
        queue: asyncio.Queue = asyncio.Queue()
        c.sessions[sid] = queue
        c.allowed_servers[sid] = set(spec.mcp_servers)
        model = spec.model[len(PREFIX):] if is_model(spec.model) else ""
        if model:
            try:
                await c.request("session/set_config_option", {"sessionId": sid, "configId": "model", "value": model}, 30)
            except Invalid:
                await c.request("session/set_model", {"sessionId": sid, "modelId": model}, 30)
        prompt = asyncio.create_task(c.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": _prompt(spec)}]},
                                               s.run_timeout_s))
        names: dict[str, tuple[str, str]] = {}
        texts: list[str] = []
        with raw_log.open("w", encoding="utf8") as raw:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({getter, prompt}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    await asyncio.sleep(0.05)  # updates that arrive together with the end
                    while not queue.empty():
                        upd = queue.get_nowait()
                        if upd:
                            raw.write(json.dumps(upd, ensure_ascii=False)[:4000] + "\n")
                            for ev in _events(upd, names, list(spec.mcp_servers), texts):
                                yield ev
                    break
                upd = getter.result()
                if upd is None:
                    break
                raw.write(json.dumps(upd, ensure_ascii=False)[:4000] + "\n")
                for ev in _events(upd, names, list(spec.mcp_servers), texts):
                    yield ev
        try:
            result = await prompt
            stop = (result or {}).get("stopReason", "end_turn")
            ok = stop in ("end_turn", "max_tokens", "max_turn_requests")
            text = "".join(texts).strip()
            yield {"type": "done", "ok": ok, "result": text if ok else f"Antigravity hat abgebrochen ({stop})", "session_id": None}
        except Exception as e:  # noqa: BLE001
            msg = str(e) or e.__class__.__name__
            if re.search(r"RESOURCE_EXHAUSTED|quota|rate.?limit|429", msg, re.I):
                msg = "usage limit reached – " + msg
            yield {"type": "done", "ok": False, "result": msg[:800], "session_id": None}
    except (Invalid, KeyError, TimeoutError) as e:
        yield {"type": "done", "ok": False, "result": f"Antigravity: {e}"[:800], "session_id": None}
    finally:
        if sid:
            c.sessions.pop(sid, None)
            c.allowed_servers.pop(sid, None)
        c.last_used = time.time()
        shutil.rmtree(run_dir, ignore_errors=True)


def _events(upd: dict, names: dict, servers: list[str], texts: list[str]) -> list[dict]:
    kind = upd.get("sessionUpdate")
    if kind == "agent_message_chunk":
        t = _content_text(upd.get("content"))
        if t:
            texts.append(t)
            return [{"type": "text", "text": t}]
    elif kind == "tool_call":
        tid = upd.get("toolCallId") or new_id()
        names[tid] = _tool_name(upd, servers)
        out = [{"type": "tool", "id": tid, "server": names[tid][0], "name": names[tid][1], "input": upd.get("rawInput") or {}}]
        if upd.get("status") in ("completed", "failed"):
            out.append({"type": "tool_result", "id": tid, "error": upd.get("status") == "failed",
                        "content": _content_text(upd.get("content"))[:4000]})
        return out
    elif kind == "tool_call_update" and upd.get("status") in ("completed", "failed"):
        tid = upd.get("toolCallId") or ""
        content = _content_text(upd.get("content")) or json.dumps(upd.get("rawOutput") or "", ensure_ascii=False)
        return [{"type": "tool_result", "id": tid, "error": upd.get("status") == "failed", "content": content[:4000]}]
    return []
