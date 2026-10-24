import re
from urllib.parse import urlsplit

from fastapi import APIRouter
from sqlalchemy import select

from ..db import utcnow
from ..models import AgentConnector, Connector
from ..schemas import ConnectorCreate, ConnectorUpdate
from ..services.errors import Invalid, NotFound
from ..services.mcp.client import list_tools
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])
RESERVED = {"ida"}  # name of the internal MCP server


async def _agents(db, cid: str) -> list[str]:
    return list((await db.scalars(select(AgentConnector.agent_id).where(AgentConnector.connector_id == cid))).all())


async def _refresh(c: Connector) -> None:
    """Loads the tool list; new tools of a known server start switched off."""
    try:
        names = await list_tools(c)
    except Exception as e:  # noqa: BLE001 – everything network related
        c.status, c.status_text, c.checked_at = "error", str(e)[:300] or e.__class__.__name__, utcnow()
        return
    if c.tools:
        c.disabled_tools = [t for t in c.disabled_tools if t in names] + [t for t in names if t not in c.tools]
    c.tools = names
    c.status, c.status_text, c.checked_at = "ok", f"{len(names)} Tools", utcnow()


@router.get("")
async def list_connectors(db: DB):
    return [ser.connector(c, await _agents(db, c.id)) for c in (await db.scalars(select(Connector).order_by(Connector.name))).all()]


@router.get("/{connector_id}")
async def get_connector(connector_id: str, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    return ser.connector(c, await _agents(db, c.id))


@router.post("", status_code=201)
async def create(body: ConnectorCreate, db: DB):
    url = body.url.strip()
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise Invalid("Die URL muss mit http:// oder https:// anfangen")
    name = body.name.strip() or u.hostname.removeprefix("www.")
    cid = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")[:40] or "mcp"
    if cid in RESERVED:
        cid += "_1"
    base, n = cid, 2
    while await db.get(Connector, cid):
        cid, n = f"{base[:36]}_{n}", n + 1
    c = Connector(id=cid, name=name[:60], url=url, headers={"Authorization": f"Bearer {body.token}"} if body.token else {})
    db.add(c)
    await _refresh(c)
    await db.flush()
    for aid in body.agents:
        db.add(AgentConnector(agent_id=aid, connector_id=cid, tools=None))
    await db.flush()
    return ser.connector(c, await _agents(db, cid))


@router.patch("/{connector_id}")
async def update(connector_id: str, body: ConnectorUpdate, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    data = body.model_dump(exclude_unset=True)
    if data.get("name"):
        c.name = data["name"].strip()[:60]
    if data.get("enabled") is not None:
        c.enabled = data["enabled"]
    if data.get("disabled_tools") is not None:
        c.disabled_tools = [t for t in data["disabled_tools"] if t in c.tools]
    return ser.connector(c, await _agents(db, c.id))


@router.post("/{connector_id}/refresh")
async def refresh(connector_id: str, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    await _refresh(c)
    return ser.connector(c, await _agents(db, c.id))


@router.delete("/{connector_id}")
async def delete(connector_id: str, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    await db.delete(c)
    return {"ok": True}
