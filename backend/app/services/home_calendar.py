"""Today's calendar events for the start page – the server calls a calendar tool of a connector itself (no AI).

Setting "home_calendar":
  missing          -> automatic: the first enabled connector with a tool like "termine_liste" / "list_events"
  {"off": true}    -> no calendar on the start page
  {"connector", "tool", "args"} -> exactly this tool; in args, {heute_start} {heute_ende} {heute} {monat} are filled in
"""
import json
import re
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Connector
from . import settings_store
from .mcp import client
from .schedule import tz

KEY = "home_calendar"
TTL = 300  # seconds a result is reused (the start page refreshes every minute)
TOOL_RE = re.compile(r"termin|kalender|calendar|event", re.I)
LIST_RE = re.compile(r"list|liste|suche|search|get", re.I)
_cache: dict[str, tuple[float, dict]] = {}

TITLE = ("titel", "title", "summary", "name", "betreff", "subject")
START = ("start", "beginn", "begin", "von", "start_time", "startTime", "start_date", "from")
END = ("ende", "end", "bis", "end_time", "endTime", "end_date", "to")
PLACE = ("ort", "location", "place")
ARG_HINTS = [  # schema property -> placeholder
    (re.compile(r"^(von|from|start|start_?date|start_?time|time_?min|timeMin|after|since)$", re.I), "{heute_start}"),
    (re.compile(r"^(bis|to|end|end_?date|end_?time|time_?max|timeMax|before|until)$", re.I), "{heute_ende}"),
    (re.compile(r"^(date|datum|day|tag)$", re.I), "{heute}"),
    (re.compile(r"^(month|monat)$", re.I), "{monat}"),
]


def _today() -> tuple[datetime, datetime]:
    d = datetime.now(tz()).date()
    start = datetime(d.year, d.month, d.day, tzinfo=tz())
    return start, start + timedelta(days=1)


def fill(args, start: datetime, end: datetime):
    vals = {"heute_start": start.isoformat(), "heute_ende": end.isoformat(), "heute": start.date().isoformat(), "monat": f"{start:%Y-%m}"}
    if isinstance(args, dict):
        return {k: fill(v, start, end) for k, v in args.items()}
    if isinstance(args, list):
        return [fill(v, start, end) for v in args]
    if isinstance(args, str):
        return re.sub(r"\{(heute_start|heute_ende|heute|monat)\}", lambda m: vals[m.group(1)], args)
    return args


def suggest_args(schema: dict | None) -> dict:
    out = {}
    for prop in ((schema or {}).get("properties") or {}):
        for rx, ph in ARG_HINTS:
            if rx.match(prop):
                out[prop] = ph
                break
    return out


def candidates(connectors: list[Connector]) -> list[dict]:
    """Calendar-like list tools, best first (Google before others)."""
    out = []
    for c in connectors:
        if not c.enabled:
            continue
        for t in c.tools or []:
            if t in (c.disabled_tools or []) or not TOOL_RE.search(t) or not LIST_RE.search(t):
                continue
            out.append({"connector": c.id, "connector_name": c.name, "tool": t})
    return sorted(out, key=lambda x: (0 if "google" in x["tool"].lower() else 1, x["tool"]))


def parse_json(text: str):
    """Tool output -> JSON; several objects one after another become a list."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    dec, pos, items = json.JSONDecoder(), 0, []
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        try:
            obj, pos = dec.raw_decode(text, pos)
        except ValueError:
            break
        items.append(obj)
    return items


def _first(d: dict, keys) -> object:
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if low.get(k.lower()) not in (None, ""):
            return low[k.lower()]
    return None


def _when(v) -> tuple[datetime | date | None, bool]:
    """'2026-10-26T10:00:00+01:00' / '2026-10-26' / {"dateTime": …} / {"date": …} -> (value, all_day)."""
    if isinstance(v, dict):
        v = v.get("dateTime") or v.get("date_time") or v.get("date")
    if not isinstance(v, str) or not v.strip():
        return None, False
    s = v.strip()
    try:
        if len(s) == 10:
            return date.fromisoformat(s), True
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None, False
    return (d if d.tzinfo else d.replace(tzinfo=tz())), False


def _items(data) -> list[dict]:
    """Every dict that looks like an event, wherever it sits (list, {"events": [...]}, …)."""
    if isinstance(data, list):
        return [e for x in data for e in _items(x)]
    if isinstance(data, dict):
        if _first(data, TITLE) is not None and _first(data, START) is not None:
            return [data]
        return [e for v in data.values() if isinstance(v, (list, dict)) for e in _items(v)]
    return []


def events_today(text: str, start: datetime, end: datetime) -> list[dict]:
    out = []
    for e in _items(parse_json(text)):
        s, all_day = _when(_first(e, START))
        f, _ = _when(_first(e, END))
        if s is None:
            continue
        if all_day:
            last = f if isinstance(f, date) and not isinstance(f, datetime) else s + timedelta(days=1)  # end date is exclusive
            if not (s <= start.date() < last):
                continue
            at, until = start, None
        else:
            until = f if isinstance(f, datetime) else None
            if not (s < end and (until or s) >= start):
                continue
            at = s
        out.append({"kind": "event", "at": at.astimezone(timezone.utc).isoformat(), "end": until.astimezone(timezone.utc).isoformat() if until else None,
                    "all_day": all_day, "title": str(_first(e, TITLE))[:120], "location": str(_first(e, PLACE) or "")[:80]})
    return sorted(out, key=lambda x: (not x["all_day"], x["at"]))


async def config(db: AsyncSession) -> dict | None:
    """The calendar to use (with args), or None when switched off / nothing found."""
    saved = await settings_store.get(db, KEY)
    if saved and saved.get("off"):
        return None
    if saved and saved.get("connector") and saved.get("tool"):
        return {"connector": saved["connector"], "tool": saved["tool"], "args": saved.get("args") or {}, "auto": False}
    conns = (await db.scalars(select(Connector).order_by(Connector.name))).all()
    found = candidates(list(conns))
    return {**found[0], "args": None, "auto": True} if found else None


async def fetch(db: AsyncSession, cfg: dict, use_cache: bool = True) -> dict:
    """{"events": [...], "error": str | None} for today."""
    start, end = _today()
    key = json.dumps([cfg.get("connector"), cfg.get("tool"), cfg.get("args"), start.date().isoformat()], sort_keys=True, default=str)
    hit = _cache.get(key)
    if use_cache and hit and time.time() - hit[0] < TTL:
        return hit[1]
    c = await db.get(Connector, cfg["connector"])
    try:
        if not c:
            raise client.McpError(f"MCP-Server „{cfg['connector']}“ gibt es nicht mehr")
        args = cfg.get("args")
        if args is None:  # automatic: fill the date arguments from the tool's schema
            schema = next((t.get("inputSchema") for t in await client.tool_defs(c) if t.get("name") == cfg["tool"]), None)
            args = suggest_args(schema)
        text = await client.call_tool(c, cfg["tool"], fill(args, start, end))
        res = {"events": events_today(text, start, end), "error": None}
    except Exception as e:  # noqa: BLE001 – the start page should show the rest anyway
        res = {"events": [], "error": str(e)[:200] or e.__class__.__name__}
    _cache[key] = (time.time(), res)
    return res


def clear_cache() -> None:
    _cache.clear()
