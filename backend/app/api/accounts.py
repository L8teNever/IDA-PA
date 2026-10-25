"""Claude accounts. Login like in T3 Code: a terminal in the browser runs `claude auth login` for the account
(WebSocket below); the CLI stores the login itself in the account's config dir on the /data volume.
Alternatives: upload a .credentials.json or paste a token from `claude setup-token`."""
import json

import asyncio
import contextlib

from fastapi import APIRouter, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from ..models import Account
from ..schemas import AccountCreate, AccountUpdate
from ..db import session_scope
from ..security import websocket_allowed
from ..services import antigravity, providers
from ..services.claude import terminal as term
from ..services.claude import usage as usage_svc
from ..services.claude.runner import account_dir
from ..services.errors import Invalid, NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1", tags=["accounts"])
MAX_ACCOUNTS = 10


@router.get("/accounts")
async def list_accounts(db: DB):
    accs = (await db.scalars(select(Account).order_by(Account.position, Account.id))).all()
    return [ser.account(a, providers.logged_in(a)) for a in accs]


@router.post("/accounts/{account_id}/refresh")
async def refresh(account_id: str, db: DB):
    """Reloads the models of the account (Antigravity: from the account, Claude: Anthropic's model list)."""
    a = await _account(db, account_id)
    if a.provider == "antigravity":
        await antigravity.load_models(a)
    else:
        from ..services.claude import models as claude_models
        await claude_models.refresh([a])
    return ser.account(a, providers.logged_in(a))


# ---- Antigravity: managed runtime + Google sign-in (like T3 Code) ----
@router.get("/antigravity/runtime")
async def agy_runtime():
    return antigravity.install_state()


@router.post("/antigravity/runtime")
async def agy_install():
    return antigravity.start_install()


@router.post("/accounts/{account_id}/antigravity/login")
async def agy_login(account_id: str, db: DB):
    """Starts the sign-in: returns the Google link (or "installing" while the runtime downloads)."""
    a = await _account(db, account_id)
    if a.provider != "antigravity":
        raise Invalid("Kein Antigravity-Account")
    return await antigravity.start_login(a)


@router.get("/accounts/{account_id}/antigravity/login")
async def agy_login_state(account_id: str, db: DB):
    return antigravity.login_state(await _account(db, account_id))


@router.post("/accounts/{account_id}/antigravity/callback")
async def agy_callback(account_id: str, body: dict, db: DB):
    """The address Google returned to (http://127.0.0.1:…/?code=…) – delivered to the agent on the server."""
    return await antigravity.finish_login(await _account(db, account_id), str(body.get("url") or ""))


@router.post("/accounts", status_code=201)
async def create(body: AccountCreate, db: DB):
    ids = set((await db.scalars(select(Account.id))).all())
    aid = next((f"a{n}" for n in range(1, MAX_ACCOUNTS + 1) if f"a{n}" not in ids), None)
    if not aid:
        raise Invalid(f"Maximal {MAX_ACCOUNTS} Accounts")
    pos = (await db.scalar(select(func.max(Account.position)))) or 0
    if body.provider not in providers.PROVIDERS:
        raise Invalid("Anbieter: claude oder antigravity")
    label = providers.PROVIDERS[body.provider]
    a = Account(id=aid, name=body.name.strip()[:40] or f"{label} {aid[1:]}", position=pos + 1, provider=body.provider)
    db.add(a)
    return ser.account(a, False)


@router.patch("/accounts/{account_id}")
async def update(account_id: str, body: AccountUpdate, db: DB):
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
    data = body.model_dump(exclude_unset=True)
    if data.get("name"):
        a.name = data["name"].strip()[:40]
    if "oauth_token" in data:
        a.oauth_token = (data["oauth_token"] or "").strip() or None
    return ser.account(a, providers.logged_in(a))


@router.post("/accounts/{account_id}/credentials")
async def upload_credentials(account_id: str, file: UploadFile, db: DB):
    """Stores a .credentials.json of a logged-in Claude Code (contains the refreshable OAuth login)."""
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
    raw = await file.read()
    try:
        data = json.loads(raw)
        assert (data.get("claudeAiOauth") or {}).get("accessToken")
    except (ValueError, AssertionError, AttributeError) as e:
        raise Invalid("Das ist keine gültige .credentials.json von Claude Code") from e
    path = account_dir(a.id) / ".credentials.json"
    path.write_bytes(raw)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return ser.account(a, True)


async def _account(db, account_id: str) -> Account:
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
    return a


@router.delete("/accounts/{account_id}/login")
async def logout(account_id: str, db: DB):
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
    if a.provider == "antigravity":
        await antigravity.logout(a)
        return ser.account(a, False)
    (account_dir(a.id) / ".credentials.json").unlink(missing_ok=True)
    a.oauth_token = None
    return ser.account(a, False)


@router.delete("/accounts/{account_id}")
async def delete(account_id: str, db: DB):
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
    if (await db.scalar(select(func.count()).select_from(Account))) <= 1:
        raise Invalid("Mindestens ein Account muss bleiben")
    await db.delete(a)
    return {"ok": True}


@router.get("/usage")
async def usage(db: DB, force: bool = False):
    out = []
    for a in (await db.scalars(select(Account).order_by(Account.position, Account.id))).all():
        if a.provider == "antigravity":
            out.append({"id": a.id, "name": a.name, "provider": a.provider, **await antigravity.usage(a, force)})
            continue
        try:
            u = await usage_svc.usage(a, force)
        except Exception as e:  # noqa: BLE001
            u = {"error": f"Abfrage fehlgeschlagen: {e}"}
        out.append({"id": a.id, "name": a.name, **u})
    return out


@router.get("/terminal")
async def terminal_info():
    return {"available": term.available(), "commands": list(term.COMMANDS)}


@router.websocket("/accounts/{account_id}/terminal")
async def terminal(ws: WebSocket, account_id: str, cmd: str = "login", cols: int = 100, rows: int = 30):
    """Live terminal with the Claude CLI of this account (login like in T3 Code: `claude auth login`).
    Messages: browser -> {"t":"in","d":text} | {"t":"resize","cols","rows"};  server -> {"t":"out","d":text} | {"t":"exit",...}"""
    if not await websocket_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    async with session_scope() as db:
        a = await db.get(Account, account_id)
    if not a:
        await ws.send_json({"t": "error", "d": "Account nicht gefunden"})
        await ws.close()
        return
    try:
        s = await term.open_session(a, cmd, cols, rows)
    except Invalid as e:
        await ws.send_json({"t": "error", "d": str(e)})
        await ws.close()
        return
    queue: asyncio.Queue = asyncio.Queue()
    listener = queue.put_nowait
    s.listeners.add(listener)
    if s.buffer:  # reconnect: show what happened meanwhile
        await ws.send_json({"t": "out", "d": bytes(s.buffer).decode(errors="replace")})
    if not s.running:
        queue.put_nowait(None)

    async def pump_out():
        while True:
            data = await queue.get()
            if data is None:
                await ws.send_json({"t": "exit", "code": s.exit_code, "logged_in": providers.logged_in(a)})
                return
            await ws.send_json({"t": "out", "d": data.decode(errors="replace")})

    out_task = asyncio.create_task(pump_out())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("t") == "in":
                s.write(str(msg.get("d", "")).encode())
            elif msg.get("t") == "resize":
                s.resize(int(msg.get("cols", 100)), int(msg.get("rows", 30)))
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass  # the process keeps running – opening the terminal again reconnects
    finally:
        s.listeners.discard(listener)
        out_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await out_task
