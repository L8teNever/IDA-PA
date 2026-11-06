import logging
import re
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..db import utcnow
from ..models import AgentConnector, Connector
from ..config import get_settings
from ..schemas import ConnectorCreate, ConnectorOAuth, ConnectorUpdate
from ..services.errors import Invalid, NotFound
from ..services.mcp import context as mcp_context
from ..services.mcp import oauth
from ..services.mcp.client import list_tools
from . import serializers as ser
from .deps import DB

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])
RESERVED = {"ida"}  # name of the internal MCP server
log = logging.getLogger(__name__)


async def _agents(db, cid: str) -> list[str]:
    return list((await db.scalars(select(AgentConnector.agent_id).where(AgentConnector.connector_id == cid))).all())


async def _refresh(c: Connector) -> None:
    """Loads the tool list; new tools of a known server start switched off."""
    if oauth.active(c) and c.oauth.get("grant") == "code" and not oauth.logged_in(c):
        c.status, c.status_text, c.checked_at = "login", "Anmeldung nötig", utcnow()
        return
    try:
        names = await list_tools(c)
    except Exception as e:  # noqa: BLE001 – everything network related
        c.status, c.status_text, c.checked_at = "error", str(e)[:300] or e.__class__.__name__, utcnow()
        return
    if c.tools:
        c.disabled_tools = [t for t in c.disabled_tools if t in names] + [t for t in names if t not in c.tools]
    c.tools = names
    c.status, c.status_text, c.checked_at = "ok", f"{len(names)} Tools", utcnow()


async def _describe(db, c: Connector) -> None:
    """Drafts the context text once, as long as there is none (the user's own text is never overwritten)."""
    if (c.context or "").strip() or c.status != "ok" or not c.tools:
        return
    try:
        c.context = await mcp_context.describe(db, c)
    except Exception as e:  # noqa: BLE001 – the connector works without it
        log.warning("connector %s: no context drafted: %s", c.id, e)


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
    c = Connector(id=cid, name=name[:60], url=url, headers={"Authorization": f"Bearer {body.token}"} if body.token else {}, oauth={})
    db.add(c)
    if body.oauth:
        c.oauth = _oauth_settings({}, body.oauth)
    elif not body.token:
        await _auto_oauth(c)
    await db.flush()
    if c.oauth.get("grant") != "client_credentials" or await _client_credentials(c):
        await _refresh(c)
    await _describe(db, c)
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
    if data.get("context") is not None:
        c.context = data["context"].strip()[:2000]
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
    await _describe(db, c)
    return ser.connector(c, await _agents(db, c.id))


@router.post("/{connector_id}/describe")
async def describe(connector_id: str, db: DB):
    """Drafts the context text again from the current tools (replaces the old one)."""
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    try:
        text = await mcp_context.describe(db, c)
    except Exception as e:  # noqa: BLE001
        raise Invalid(f"Beschreibung fehlgeschlagen: {str(e)[:200] or e.__class__.__name__}")
    if not text:
        raise Invalid("Der Server hat keine Tools")
    c.context = text
    return ser.connector(c, await _agents(db, c.id))


@router.delete("/{connector_id}")
async def delete(connector_id: str, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    await db.delete(c)
    return {"ok": True}


# ---------------- OAuth (Client-ID/Secret, Login im Browser) ----------------
def _oauth_settings(old: dict, o: ConnectorOAuth) -> dict:
    """Neue Einstellungen übernehmen. Ändert sich Client-ID, Art oder Scope, ist die alte Anmeldung ungültig."""
    new = {**old, "grant": o.grant}
    if o.client_id is not None:
        cid = o.client_id.strip()
        if cid != old.get("client_id"):
            new.update(client_id=cid, registered=False)
    if o.client_secret is not None:
        new["client_secret"] = o.client_secret.strip()
    if o.scope is not None:
        new["scope"] = " ".join(o.scope.split())
    if any(new.get(k) != old.get(k) for k in ("grant", "client_id", "client_secret", "scope")):
        for k in ("access_token", "refresh_token", "expires_at", "pending", "granted_scope"):
            new.pop(k, None)
    if new["grant"] == "client_credentials" and not (new.get("client_id") and new.get("client_secret")):
        raise Invalid("„Ohne Login“ braucht Client-ID und Client-Secret")
    return new


async def _auto_oauth(c: Connector) -> None:
    """Ohne Token: verlangt der Server OAuth, wird der Connector zum Login vorbereitet (Client-ID kommt dann per Registrierung)."""
    try:
        meta = await oauth.discover(c.url)
    except oauth.OAuthError:
        return
    c.oauth = {"grant": "code", "meta": meta}


async def _client_credentials(c: Connector) -> bool:
    try:
        await oauth.client_credentials(c)
        return True
    except oauth.OAuthError as e:
        c.status, c.status_text, c.checked_at = "error", str(e)[:300], utcnow()
        return False


def _base_url(request: Request) -> str:
    s = get_settings()
    return (s.ida_public_url or str(request.base_url)).rstrip("/")


@router.put("/{connector_id}/oauth")
async def set_oauth(connector_id: str, body: ConnectorOAuth, db: DB):
    """Anmeldung einstellen oder ändern (Client-ID, Secret, Scope, Art). Danach ggf. neu anmelden."""
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    c.oauth = _oauth_settings(dict(c.oauth or {}), body)
    await db.flush()
    if c.oauth["grant"] == "code" or await _client_credentials(c):
        await _refresh(c)
    return ser.connector(c, await _agents(db, c.id))


@router.delete("/{connector_id}/oauth")
async def drop_oauth(connector_id: str, db: DB):
    """OAuth ganz entfernen (zurück zu „ohne Anmeldung“ bzw. festem Token)."""
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    c.oauth = {}
    await _refresh(c)
    return ser.connector(c, await _agents(db, c.id))


@router.post("/{connector_id}/oauth/start")
async def oauth_start(connector_id: str, request: Request, db: DB):
    """→ {url}: dorthin schickt die Oberfläche den Browser zum Anmelden. Zurück kommt er über /oauth/callback."""
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    if not oauth.active(c):
        c.oauth = {"grant": "code"}
    try:
        url = await oauth.start(c, _base_url(request))
    except oauth.OAuthError as e:
        raise Invalid(str(e)) from e
    return {"url": url}


@router.post("/{connector_id}/oauth/logout")
async def oauth_logout(connector_id: str, db: DB):
    c = await db.get(Connector, connector_id)
    if not c:
        raise NotFound("Connector nicht gefunden")
    await oauth.forget(c)
    c.status, c.status_text = ("login", "Anmeldung nötig") if c.oauth.get("grant") == "code" else (c.status, c.status_text)
    return ser.connector(c, await _agents(db, c.id))


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(db: DB, state: str = "", code: str = "", error: str = "", error_description: str = ""):
    """Rücksprung vom Anmelde-Server: Code gegen Token tauschen, Tools laden, zurück zur Seite des Connectors."""
    rows = (await db.scalars(select(Connector))).all()
    c = next((x for x in rows if oauth.matches_state(x, state)), None)
    if not c:
        return RedirectResponse("/einstellungen/mcp?oauth=abgelaufen", status_code=303)
    page = f"/einstellungen/mcp/{c.id}"
    if error or not code:
        await oauth.forget(c)
        msg = (error_description or error or "abgebrochen")[:120]
        return RedirectResponse(f"{page}?oauth=fehler&grund={quote(msg)}", status_code=303)
    try:
        await oauth.finish(c, code)
    except oauth.OAuthError as e:
        return RedirectResponse(f"{page}?oauth=fehler&grund={quote(str(e)[:120])}", status_code=303)
    await _refresh(c)
    await _describe(db, c)
    return RedirectResponse(f"{page}?oauth=ok", status_code=303)
