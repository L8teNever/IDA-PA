"""Wer ruft an?

* ohne Token (auf Web- & /api/v1-Routen) → der Nutzer (davor steht Cloudflare Zero Trust bzw. das LAN)
* Bearer WIKI_IDA_TOKEN                  → IDA intern:
    - `X-Wiki-By: user` oder leer        → der Nutzer (z. B. über IDA)
    - `X-Wiki-By: system:sync`           → automatischer Spiegel für `ki/`
    - `X-Wiki-By: <agent-id>`            → IDA-Agent (darf `memory/` direkt pflegen; `privat/` nur mit bestätigtem Proposal)
* Bearer WIKI_MCP_TOKEN oder benanntes Token aus `mcp_tokens` (auf `/mcp`):
    - Externer KI-Agent (`kind="mcp"`): darf `memory/` lesen & strukturiert schreiben, `privat/` und `ki/` STRIKT NUR LESEN.
* Auf `/mcp` und `/api/v1/internal/*` ist ein gültiger Bearer-Token ZWINGEND erforderlich (401 ohne Token).
"""
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request
from sqlalchemy import select

from .config import get_settings
from .db import session_scope, utcnow
from .models import McpToken

_AGENT = re.compile(r"^[a-z0-9_-]{1,40}$")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass
class Caller:
    by: str                                 # "user" | "<agent-id>" | "system:sync" | "mcp" | "mcp:<name>"
    kind: str                               # "user" | "ida" | "ida_sync" | "mcp"
    allow_read_private: bool = True
    allow_read_chats: bool = True
    via_override: str | None = None

    @property
    def via(self) -> str:
        if self.via_override:
            return self.via_override
        return {"user": "ui", "ida": "ida", "ida_sync": "sync", "mcp": "mcp"}[self.kind]


async def caller(request: Request) -> Caller:
    s = get_settings()
    path = request.url.path
    is_mcp_path = path == "/mcp" or path.startswith("/mcp/")
    is_internal = path.startswith("/api/v1/internal")
    h = request.headers.get("authorization", "")

    if not h:
        if is_mcp_path or is_internal:
            raise HTTPException(401, "Bearer-Token erforderlich")
        return Caller("user", "user")

    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Ungültige Anmeldung")

    tok = h[7:].strip()
    if not tok:
        raise HTTPException(401, "Ungültiger Token")

    # 1. Internes IDA-Token
    if _eq(tok, s.wiki_ida_token):
        who = request.headers.get("x-wiki-by", "").strip().lower()
        if not who or who == "user":
            return Caller("user", "user", via_override="ida")
        if who == "system:sync":
            return Caller("system:sync", "ida_sync")
        if not _AGENT.match(who):
            raise HTTPException(400, "Ungültiger Agent-Header")
        return Caller(who, "ida")

    # 2. Externes MCP-Token (nur auf /mcp zugelassen!)
    if is_mcp_path:
        if _eq(tok, s.wiki_mcp_token):
            return Caller("mcp", "mcp", allow_read_private=True, allow_read_chats=True)
        th = hash_token(tok)
        async with session_scope(write=False) as db:
            row = await db.scalar(select(McpToken).where(McpToken.token_hash == th))
        if row:
            # last_used_at im Hintergrund oder kurz merken (nicht blockierend bei Leseanfragen)
            slug = re.sub(r"[^a-z0-9_-]+", "-", row.name.lower()).strip("-") or row.id[:6]
            return Caller(
                f"mcp:{slug}",
                "mcp",
                allow_read_private=bool(row.allow_read_private),
                allow_read_chats=bool(row.allow_read_chats),
            )

    raise HTTPException(401, "Ungültiger Token")


async def require_ida_internal(request: Request) -> Caller:
    """Schützt /api/v1/internal/* – erlaubt ausschließlich WIKI_IDA_TOKEN."""
    s = get_settings()
    h = request.headers.get("authorization", "")
    tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
    if not _eq(tok, s.wiki_ida_token):
        raise HTTPException(401, "Nur für IDA intern erlaubt")
    return await caller(request)


async def create_named_mcp_token(db, name: str, *, allow_read_private: bool = True, allow_read_chats: bool = True) -> dict:
    clean = (name or "").strip()[:60]
    if not clean:
        from .errors import Invalid
        raise Invalid("Name für den MCP-Zugang fehlt")
    raw = "wk_" + secrets.token_hex(24)
    row = McpToken(
        name=clean,
        token_hash=hash_token(raw),
        prefix=raw[:9],
        allow_read_private=allow_read_private,
        allow_read_chats=allow_read_chats,
        created_at=utcnow(),
    )
    db.add(row)
    await db.flush()
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "token": raw,  # wird nur einmalig beim Erstellen zurückgegeben!
        "allow_read_private": row.allow_read_private,
        "allow_read_chats": row.allow_read_chats,
        "created_at": row.created_at.isoformat(),
    }
