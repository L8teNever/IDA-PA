"""Claude accounts. Logging in through a browser does not work inside the container, so an account gets
its login either as an uploaded .credentials.json (from a machine where `claude` is logged in) or as a
token from `claude setup-token`."""
import json

from fastapi import APIRouter, UploadFile
from sqlalchemy import func, select

from ..models import Account
from ..schemas import AccountCreate, AccountUpdate
from ..services.claude import usage as usage_svc
from ..services.claude.runner import account_dir
from ..services.errors import Invalid, NotFound
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1", tags=["accounts"])
MAX_ACCOUNTS = 5


@router.get("/accounts")
async def list_accounts(db: DB):
    return [ser.account(a, usage_svc.logged_in(a)) for a in (await db.scalars(select(Account).order_by(Account.position, Account.id))).all()]


@router.post("/accounts", status_code=201)
async def create(body: AccountCreate, db: DB):
    ids = set((await db.scalars(select(Account.id))).all())
    aid = next((f"a{n}" for n in range(1, MAX_ACCOUNTS + 1) if f"a{n}" not in ids), None)
    if not aid:
        raise Invalid(f"Maximal {MAX_ACCOUNTS} Accounts")
    pos = (await db.scalar(select(func.max(Account.position)))) or 0
    a = Account(id=aid, name=body.name.strip()[:40] or f"Account {aid[1:]}", position=pos + 1)
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
    return ser.account(a, usage_svc.logged_in(a))


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


@router.delete("/accounts/{account_id}/login")
async def logout(account_id: str, db: DB):
    a = await db.get(Account, account_id)
    if not a:
        raise NotFound("Account nicht gefunden")
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
        try:
            u = await usage_svc.usage(a, force)
        except Exception as e:  # noqa: BLE001
            u = {"error": f"Abfrage fehlgeschlagen: {e}"}
        out.append({"id": a.id, "name": a.name, **u})
    return out
