"""Volltextsuche (SQLite FTS5, komplett lokal).

Die Normalisierung (klein, ä→ae, ö→oe, ü→ue, ß→ss, Akzente weg) geschieht hier, nicht per Trigger.
"""
import re
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Calendar, Event

_TOKEN = re.compile(r"[a-z0-9]+")
_REPL = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


def normalize(s: str) -> str:
    s = (s or "").lower()
    for k, v in _REPL.items():
        s = s.replace(k, v)
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(normalize(s))


async def index(db: AsyncSession, ev: Event) -> None:
    await db.execute(text("DELETE FROM events_fts WHERE event_id = :i"), {"i": ev.id})
    if ev.deleted_at is not None:
        return
    cal = await db.get(Calendar, ev.calendar_id)
    body = " ".join(tokens(" ".join([ev.title, ev.location, ev.description, cal.name if cal else ""])))
    await db.execute(text("INSERT INTO events_fts(event_id, text) VALUES (:i, :t)"), {"i": ev.id, "t": body})


async def unindex(db: AsyncSession, event_id: str) -> None:
    await db.execute(text("DELETE FROM events_fts WHERE event_id = :i"), {"i": event_id})


def parse_query(q: str) -> tuple[list[str], list[str]]:
    """„zahnarzt -privat "dr müller"“ → (Suchausdrücke, auszuschließende Wörter). Ausdruck = Wörter in Anführungszeichen."""
    must, mustnot = [], []
    for m in re.finditer(r'(-?)"([^"]+)"|(-?)(\S+)', q or ""):
        neg = (m.group(1) or m.group(3)) == "-"
        raw = m.group(2) or m.group(4)
        quoted = m.group(2) is not None
        toks = tokens(raw)
        if not toks:
            continue
        if neg:
            mustnot.extend(toks)
        elif quoted and len(toks) > 1:
            must.append('"' + " ".join(toks) + '"')
        else:
            must.extend(f'"{t}"*' for t in toks)
    return must, mustnot


def match_expr(q: str) -> str | None:
    must, mustnot = parse_query(q)
    if not must:
        return None
    expr = "(" + " AND ".join(must) + ")"  # NOT bindet in FTS5 stärker als AND – deshalb die Klammer
    for t in mustnot:
        expr += f' NOT "{t}"*'
    return expr


async def matching_ids(db: AsyncSession, q: str, limit: int = 300) -> list[str]:
    expr = match_expr(q)
    if not expr:
        return []
    rows = await db.execute(text("SELECT event_id FROM events_fts WHERE events_fts MATCH :e ORDER BY bm25(events_fts) LIMIT :n"),
                            {"e": expr, "n": limit})
    return [r[0] for r in rows]


async def suggest_titles(db: AsyncSession, prefix: str, limit: int = 5) -> list[dict]:
    """Beim Tippen des Titels: frühere Termine mit Dauer, Kalender, Ort und Erinnerungen als Vorlage."""
    p = (prefix or "").strip()
    if len(p) < 2:
        return []
    rows = (await db.scalars(select(Event).where(Event.deleted_at.is_(None), Event.recurrence_parent_id.is_(None),
                                                  Event.title.ilike(p.replace("%", "") + "%")).order_by(Event.start_at.desc()).limit(60))).all()
    seen, out = set(), []
    for e in rows:
        k = e.title.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append({"title": e.title, "duration_min": int((e.end_at - e.start_at).total_seconds() // 60), "all_day": e.all_day,
                    "calendar_id": e.calendar_id, "location": e.location, "alarms": e.alarms, "color": e.color})
        if len(out) >= limit:
            break
    return out
