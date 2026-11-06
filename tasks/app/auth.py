"""Wer ruft an?

* ohne Token                → der Nutzer (die Oberfläche; davor steht Cloudflare Access bzw. das LAN – wie beim Kalender)
* Bearer TASKS_IDA_TOKEN    → IDA; `X-Tasks-By` sagt, wer: „user“ oder die ID eines Agents (Ida, Stella …)
Alles andere mit Bearer ist ungültig (401).
"""
import hmac
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import get_settings

_AGENT = re.compile(r"^[a-z0-9_-]{1,40}$")


@dataclass
class Caller:
    by: str     # "user" oder Agent-ID
    kind: str   # user | ida


def _eq(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def caller(request: Request) -> Caller:
    h = request.headers.get("authorization", "")
    if not h:
        return Caller("user", "user")
    if not h.lower().startswith("bearer "):
        raise HTTPException(401, "Ungültige Anmeldung")
    if _eq(h[7:].strip(), get_settings().tasks_ida_token):
        who = request.headers.get("x-tasks-by", "").strip().lower()
        if not who or who == "user":
            return Caller("user", "ida")
        if not _AGENT.match(who):
            raise HTTPException(400, "Ungültiger Agent")
        return Caller(who, "ida")
    raise HTTPException(401, "Ungültiger Token")
