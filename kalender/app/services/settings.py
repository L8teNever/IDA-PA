"""Einstellungen des Kalenders (Tabelle `settings`) mit Standardwerten."""
import hashlib
import hmac
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import Invalid
from ..models import Setting

DEFAULTS = {
    "week_start": 1,                 # 1 = Montag, 0 = Sonntag
    "default_view": "monthday",      # monthday | agenda | days3 | day | week | month | year
    "default_view_desktop": "week",
    "toolbar_views": ["agenda", "days3", "monthday"],
    "show_week_numbers": True,
    "work_hours": "08:00-18:00",
    "state": "",                     # Bundesland für Feiertage; leer = KALENDER_STATE
    "show_holidays": True,
    "hide_weekends": False,
    "default_duration_min": 60,
    "second_timezone": "",
    "theme": "dark",                 # dark | light | system
    "quiet_hours": "",               # „22:00-07:00“: dann keine Benachrichtigung in der App
    "agent_context": True,           # Termine der nächsten 24 h in Idas Prompt
    "highlight_agent": True,
}
_SECRET = {"feed_token_hash", "ida_hook"}


async def get_all(db: AsyncSession) -> dict:
    from sqlalchemy import select
    rows = {s.key: s.value for s in (await db.scalars(select(Setting))).all()}
    return {**DEFAULTS, **{k: v for k, v in rows.items() if k in DEFAULTS}}


async def get(db: AsyncSession, key: str):
    s = await db.get(Setting, key)
    return s.value if s else DEFAULTS.get(key)


async def put(db: AsyncSession, key: str, value) -> None:
    s = await db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


async def update(db: AsyncSession, patch: dict) -> dict:
    for k, v in patch.items():
        if k not in DEFAULTS:
            raise Invalid(f"Unbekannte Einstellung: {k}")
        want = type(DEFAULTS[k])
        if not isinstance(v, want) and not (want is int and isinstance(v, bool)) and not (want is bool and isinstance(v, bool)):
            raise Invalid(f"Einstellung {k}: falscher Typ")
        await put(db, k, v)
    await db.flush()
    return await get_all(db)


# ---------------- Abo-Link ----------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def rotate_feed_token(db: AsyncSession) -> str:
    token = secrets.token_urlsafe(24)
    await put(db, "feed_token_hash", _hash(token))
    await put(db, "feed_token_plain", token)  # nur zum Anzeigen/Kopieren in den Einstellungen (Nutzer hinter Access)
    return token


async def feed_token(db: AsyncSession) -> str:
    t = await get(db, "feed_token_plain")
    return t or await rotate_feed_token(db)


async def feed_token_ok(db: AsyncSession, token: str) -> bool:
    h = await get(db, "feed_token_hash")
    return bool(h) and hmac.compare_digest(str(h), _hash(token))
