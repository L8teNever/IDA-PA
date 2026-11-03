"""stella_*-Tools des internen MCP: Ida (und Stella selbst) lösen über den Voice-Dienst Anrufe aus (siehe docs/STELLA.md).

Nur der Haupt-Agent und der Voice-Agent sehen sie. Der Voice-Dienst wählt; der Verlauf liegt in IDA (voice_calls).
"""
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_settings
from ...models import VoiceCall
from ..errors import Invalid, NotFound

E = {"type": "string"}


def enabled() -> bool:
    return get_settings().voice_on


def spec() -> list[tuple[str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    return [
        ("stella_call", "Stella ruft an (Gemini Live am Telefon). Ohne „to“ ruft sie den Nutzer an – dann hat sie im Gespräch seine Tools, "
                        "sein Gedächtnis und kann Aufgaben annehmen. Andere Nummern: Stella hat dort KEINEN Zugriff und weiß nur, was im "
                        "Auftrag steht. Den Nutzer nur anrufen, wenn es wirklich dringend ist oder er es (mit Uhrzeit) angesagt hat.",
         obj({"auftrag": {**E, "description": "Worum es geht, vollständig – das ist alles, was Stella über den Anlass weiß"},
              "to": {**E, "description": "Nummer im Format +49…; leer = der Nutzer"},
              "vorlesen": {"type": "boolean", "description": "true = Stella liest den Auftrag nur vor (z. B. Briefing), fragt ob angekommen, legt auf"}},
             ("auftrag",))),
        ("stella_call_status", "Stand eines Anrufs (id aus stella_call): Status, Ergebnis, Chat mit der Mitschrift.", obj({"call_id": E}, ("call_id",))),
        ("stella_busy", "Telefoniert Stella gerade? (nur lesen, ruft nicht an)", obj({})),
        ("stella_recent_calls", "Letzte Gespräche von Stella (Anrufe und Live-Gespräche in der App).",
         obj({"limit": {"type": "integer", "minimum": 1, "maximum": 50}})),
    ]


async def _voice(method: str, path: str, body: dict | None = None) -> dict:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, s.voice_url.rstrip("/") + path, json=body, headers={"x-voice-secret": s.voice_secret})
    except httpx.HTTPError as e:
        raise Invalid(f"Voice-Dienst nicht erreichbar: {e.__class__.__name__}") from e
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 400:
        raise Invalid(data.get("detail") or f"Voice-Dienst: HTTP {r.status_code}")
    return data


async def call(db: AsyncSession, me, name: str, a: dict) -> dict:
    from .. import voice_bridge  # late: voice_bridge imports the hub

    if name == "stella_call":
        text = str(a.get("auftrag") or a.get("brief") or "").strip()
        if not text:
            raise Invalid("auftrag fehlt")
        return await _voice("POST", "/internal/calls", {"brief": text, "to": str(a.get("to") or "").strip(),
                                                        "read_only": bool(a.get("vorlesen")), "by": me.agent_id})
    if name == "stella_call_status":
        row = await db.get(VoiceCall, str(a.get("call_id") or ""))
        if not row:
            raise NotFound("Anruf nicht gefunden")
        return voice_bridge.call_view(row)
    if name == "stella_busy":
        return await _voice("GET", "/internal/busy")
    if name == "stella_recent_calls":
        return {"gespraeche": await voice_bridge.recent(int(a.get("limit") or 10))}
    return {"error": f"Unbekanntes Tool {name}"}
