"""Wer ruft an?

* ohne Token            → der Nutzer (die Oberfläche; davor steht Cloudflare Access bzw. das LAN – wie bei IDA)
* Bearer KALENDER_IDA_TOKEN → IDA; `X-Kalender-By` sagt, wer: „user“ (Oberfläche über den Proxy) oder die ID eines Agents
* Bearer KALENDER_MCP_TOKEN → externes MCP (nur unter /mcp), gilt als Agent „mcp“
Alles andere mit Bearer ist ungültig (401). Agents unterliegen den Rechten pro Kalender (agent_access).
"""
import hmac
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import get_settings
from .db import pending

_AGENT = re.compile(r"^[a-z0-9_-]{1,40}$")


@dataclass
class Caller:
    by: str            # "user" oder Agent-ID
    kind: str          # user | ida | mcp

    @property
    def via(self) -> str:
        return {"user": "ui", "ida": "ida" if self.by != "user" else "ui", "mcp": "mcp"}[self.kind]


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def caller(request: Request) -> Caller:
    s = get_settings()
    h = request.headers.get("authorization", "")
    if not h:
        return Caller("user", "user")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Ungültige Anmeldung")
    tok = h[7:].strip()
    if _eq(tok, s.kalender_ida_token):
        who = request.headers.get("x-kalender-by", "").strip().lower()
        if not who or who == "user":
            return Caller("user", "ida")
        if not _AGENT.match(who):
            raise HTTPException(400, "Ungültiger Agent")
        return Caller(who, "ida")
    if _eq(tok, s.kalender_mcp_token) and request.url.path.startswith("/mcp"):
        return Caller("mcp", "mcp")
    raise HTTPException(401, "Ungültiger Token")


def last_change_id(db) -> int | None:
    evs = [e for e in db.info.get("events", []) if e.get("change_id")]
    return evs[-1]["change_id"] if evs else None
