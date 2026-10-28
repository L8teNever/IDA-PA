"""GitHub account for the code area: sign-in via Device Flow (or a personal access token), repos, pull requests.

The token stays on the server (settings table) – the browser only ever sees login, name, avatar and scopes.
Git and the agent get it through the environment of their processes (git.auth_env), never in URLs or prompts.
"""
import asyncio
import logging
import time

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...db import session_scope
from .. import settings_store
from ..errors import Invalid

log = logging.getLogger("ida.github")
KEY = "github"  # {"token", "login", "name", "avatar", "email", "scopes", "via"}
CLIENT_KEY = "github_client_id"
SCOPES = "repo workflow read:user user:email"

# the sign-in that is running right now (Device Flow): the user enters user_code on github.com/login/device
_pending: dict = {}
_task: asyncio.Task | None = None


async def client_id(db: AsyncSession) -> str:
    return (await settings_store.get(db, CLIENT_KEY, "") or get_settings().github_client_id or "").strip()


async def account(db: AsyncSession) -> dict | None:
    a = await settings_store.get(db, KEY, None)
    return a if isinstance(a, dict) and a.get("token") else None


async def token(db: AsyncSession) -> str | None:
    a = await account(db)
    return a["token"] if a else None


async def need_token(db: AsyncSession) -> str:
    t = await token(db)
    if not t:
        raise Invalid("Nicht mit GitHub verbunden (Einstellungen → GitHub)")
    return t


async def status(db: AsyncSession) -> dict:
    a = await account(db) or {}
    pend = {k: _pending.get(k) for k in ("user_code", "verification_uri", "expires_at", "error")} if _pending else None
    return {"connected": bool(a), "login": a.get("login"), "name": a.get("name"), "avatar": a.get("avatar"),
            "scopes": a.get("scopes") or [], "via": a.get("via"),
            "client_id": await client_id(db), "client_id_env": bool(get_settings().github_client_id), "pending": pend}


async def set_client_id(db: AsyncSession, cid: str) -> None:
    cid = cid.strip()
    if cid and not cid.replace("_", "").replace(".", "").replace("-", "").isalnum():
        raise Invalid("Das sieht nicht wie eine Client ID aus")
    await settings_store.put(db, CLIENT_KEY, cid)


def _api(tok: str | None = None) -> httpx.AsyncClient:
    h = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28", "user-agent": "IDA"}
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return httpx.AsyncClient(base_url=get_settings().github_api_url, headers=h, timeout=30)


def _err(r: httpx.Response, what: str) -> Invalid:
    if r.status_code == 401:
        return Invalid(f"{what}: GitHub hat den Zugang abgelehnt – bitte neu anmelden (Einstellungen → GitHub)")
    try:
        j = r.json()
        msg = j.get("message") or ""
        if j.get("errors"):
            msg += ": " + "; ".join(str(e.get("message") or e.get("code") or e) for e in j["errors"] if isinstance(e, dict))
    except ValueError:
        msg = r.text[:200]
    return Invalid(f"{what}: {msg or r.status_code}")


async def _profile(tok: str) -> dict:
    async with _api(tok) as c:
        r = await c.get("/user")
    if r.status_code != 200:
        raise _err(r, "GitHub-Anmeldung fehlgeschlagen")
    u = r.json()
    scopes = [s.strip() for s in (r.headers.get("x-oauth-scopes") or "").split(",") if s.strip()]
    # commits are attributed to the account through its private noreply address
    email = f"{u['id']}+{u['login']}@users.noreply.github.com"
    return {"login": u["login"], "name": u.get("name") or u["login"], "avatar": u.get("avatar_url"), "email": email, "scopes": scopes}


async def connect_token(db: AsyncSession, tok: str, via: str = "token") -> dict:
    tok = tok.strip()
    if not tok:
        raise Invalid("Token fehlt")
    p = await _profile(tok)
    await settings_store.put(db, KEY, {"token": tok, "via": via, **p})
    return p


async def logout(db: AsyncSession) -> None:
    cancel_login()
    await settings_store.put(db, KEY, None)


# ---- Device Flow ----

def cancel_login() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
    _pending.clear()


async def start_login(db: AsyncSession) -> dict:
    """Asks GitHub for a user code; a background task waits until the user confirmed it on github.com."""
    global _task
    cid = await client_id(db)
    if not cid:
        raise Invalid("Erst die Client ID deiner GitHub OAuth App eintragen (oder einen Token einfügen)")
    cancel_login()
    async with httpx.AsyncClient(timeout=30, headers={"accept": "application/json", "user-agent": "IDA"}) as c:
        r = await c.post(f"{get_settings().github_url}/login/device/code", data={"client_id": cid, "scope": SCOPES})
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code != 200 or "device_code" not in j:
        msg = j.get("error_description") or j.get("error") or r.text[:200]
        if j.get("error") == "device_flow_disabled":
            msg = "In der OAuth App ist „Device Flow“ nicht aktiviert"
        raise Invalid(f"GitHub-Anmeldung konnte nicht starten: {msg}")
    _pending.update(user_code=j["user_code"], verification_uri=j.get("verification_uri") or f"{get_settings().github_url}/login/device",
                    expires_at=time.time() + int(j.get("expires_in") or 900), interval=max(1, int(j.get("interval") or 5)),
                    device_code=j["device_code"], client_id=cid, error=None)
    _task = asyncio.create_task(_poll(dict(_pending)))
    return {k: _pending[k] for k in ("user_code", "verification_uri", "expires_at")}


async def _poll(p: dict) -> None:
    interval = p["interval"]
    try:
        async with httpx.AsyncClient(timeout=30, headers={"accept": "application/json", "user-agent": "IDA"}) as c:
            while time.time() < p["expires_at"]:
                await asyncio.sleep(interval)
                r = await c.post(f"{get_settings().github_url}/login/oauth/access_token",
                                 data={"client_id": p["client_id"], "device_code": p["device_code"],
                                       "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
                try:
                    j = r.json()
                except ValueError:
                    continue
                err = j.get("error")
                if err == "authorization_pending":
                    continue
                if err == "slow_down":  # GitHub wants a longer interval
                    interval = int(j.get("interval") or interval + 5)
                    continue
                if j.get("access_token"):
                    async with session_scope() as db:
                        await connect_token(db, j["access_token"], via="device")
                    _pending.clear()
                    return
                _pending["error"] = {"expired_token": "Der Code ist abgelaufen – bitte neu starten",
                                     "access_denied": "Auf GitHub abgelehnt"}.get(err, j.get("error_description") or err or "Unbekannter Fehler")
                return
        _pending["error"] = "Der Code ist abgelaufen – bitte neu starten"
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 – shown in the settings
        log.exception("GitHub sign-in failed")
        _pending["error"] = str(e)[:300] or e.__class__.__name__


# ---- API ----

async def repos(db: AsyncSession, q: str = "") -> list[dict]:
    tok = await need_token(db)
    async with _api(tok) as c:
        r = await c.get("/user/repos", params={"per_page": 100, "sort": "updated", "affiliation": "owner,collaborator,organization_member"})
    if r.status_code != 200:
        raise _err(r, "Repos laden fehlgeschlagen")
    q = q.strip().lower()
    out = [{"full_name": x["full_name"], "name": x["name"], "private": x["private"], "description": x.get("description") or "",
            "clone_url": x["clone_url"], "html_url": x["html_url"], "default_branch": x.get("default_branch") or "main",
            "updated_at": x.get("updated_at")} for x in r.json()]
    return [x for x in out if not q or q in x["full_name"].lower() or q in x["description"].lower()]


async def create_repo(db: AsyncSession, name: str, private: bool = True, description: str = "", auto_init: bool = True) -> dict:
    tok = await need_token(db)
    if not name.strip():
        raise Invalid("Name fehlt")
    async with _api(tok) as c:
        r = await c.post("/user/repos", json={"name": name.strip(), "private": private, "description": description, "auto_init": auto_init})
    if r.status_code != 201:
        raise _err(r, "Repo erstellen fehlgeschlagen")
    x = r.json()
    return {"full_name": x["full_name"], "name": x["name"], "private": x["private"], "clone_url": x["clone_url"],
            "html_url": x["html_url"], "default_branch": x.get("default_branch") or "main"}


async def create_pr(db: AsyncSession, full_name: str, head: str, base: str, title: str, body: str = "") -> dict:
    tok = await need_token(db)
    async with _api(tok) as c:
        r = await c.post(f"/repos/{full_name}/pulls", json={"title": title, "head": head, "base": base, "body": body})
    if r.status_code != 201:
        raise _err(r, "Pull Request fehlgeschlagen")
    x = r.json()
    return {"number": x["number"], "url": x["html_url"], "title": x["title"]}
