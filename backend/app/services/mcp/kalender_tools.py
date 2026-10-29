"""calendar_*-Tools des internen MCP: Ida bedient den lokalen Kalender-Dienst (siehe docs/KALENDER.md).

Die Tools sind dünne Clients: sie rufen die API des Kalender-Dienstes auf (`kalender_client`) und setzen `X-Kalender-By` auf die
Agent-ID. Was ein Agent in welchem Kalender darf, entscheidet der Kalender-Dienst (agent_access, vom Nutzer eingestellt).
"""
import re
from datetime import date, datetime, timedelta

from ...config import get_settings
from ..errors import Invalid
from .. import files
from .. import kalender_client as kc

ALL, MAIN, MAIN_LIVE = "all", "main", "main_live"
E = {"type": "string"}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WD = ["mo", "di", "mi", "do", "fr", "sa", "so"]
_WD_NAMES = {"mo": 0, "montag": 0, "di": 1, "dienstag": 1, "mi": 2, "mittwoch": 2, "do": 3, "donnerstag": 3, "fr": 4, "freitag": 4,
             "sa": 5, "samstag": 5, "so": 6, "sonntag": 6}
_MON = ["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]
_DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def enabled() -> bool:
    return kc.enabled()


def spec() -> list[tuple[str, str, str, dict]]:
    tzn = get_settings().timezone
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    when = {**E, "description": f"Zeitpunkt in Ortszeit ({tzn}): JJJJ-MM-TTTHH:MM, z. B. 2026-10-30T15:00 – oder nur JJJJ-MM-TT für einen ganztägigen Termin. "
            "Rechne „morgen“, „Freitag“ usw. mit der aktuellen Uhrzeit um"}
    scope = {"type": "string", "enum": ["this", "following", "all"], "description": "Nur bei Serien: this = nur dieser Termin, following = dieser und alle folgenden, all = die ganze Serie (Standard)"}
    cal = {**E, "description": "Kalender (Name oder ID, siehe calendar_list); weglassen = Standardkalender"}
    fields = {
        "title": E, "start": when,
        "end": {**E, "description": "Ende (wie start). Bei GANZTÄGIGEN Terminen der LETZTE Tag (inklusive), z. B. Urlaub 24.–26.12. → start 2026-12-24, end 2026-12-26. Statt end geht duration_min"},
        "duration_min": {"type": "integer", "minimum": 1, "description": "Dauer in Minuten (statt end); Standard 60"},
        "all_day": {"type": "boolean", "description": "ganztägig (auch, wenn start nur ein Datum ist)"},
        "calendar": cal, "location": E, "description": {**E, "description": "Beschreibung/Notizen (Markdown, „- [ ] Punkt“ für eine Checkliste)"},
        "url": E,
        "repeat": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly", "weekdays"], "description": "Wiederholung (weekdays = Mo–Fr); Ende mit until oder count, sonst endlos"},
        "interval": {"type": "integer", "minimum": 1, "description": "jede n-te Woche/Tag/…, Standard 1"},
        "days": {"type": "array", "items": E, "description": "Wochentage bei repeat=weekly: [\"mo\",\"mi\"] (Mo, Di, Mi, Do, Fr, Sa, So); bei monthly mit nth z. B. der 2. Dienstag"},
        "nth": {"type": "integer", "description": "monthly: der n-te Wochentag aus days (2 = zweiter, -1 = letzter)"},
        "until": {**E, "description": "Wiederholung endet am (JJJJ-MM-TT, dieser Tag zählt noch mit)"},
        "count": {"type": "integer", "minimum": 1, "description": "oder: Anzahl der Termine insgesamt"},
        "rrule": {**E, "description": "Fortgeschritten: fertige RRULE statt repeat (z. B. FREQ=MONTHLY;BYDAY=-1FR)"},
        "alarms": {"type": "array", "items": {"type": "integer", "minimum": 0}, "description": "Erinnerungen: Minuten vor dem Termin, z. B. [30, 1440]. Weglassen = Standard des Kalenders. Kommt als Info in der App"},
        "alarm_action": {"type": "string", "enum": ["notify", "agent"], "description": "notify = nur Benachrichtigung; agent = zur Erinnerungszeit bekommt ein Agent die Aufgabe alarm_task"},
        "alarm_task": {**E, "description": "Aufgabe für den Agenten bei alarm_action=agent (muss allein verständlich sein)"},
        "color": {**E, "description": "rot, koralle, orange, gelb, salbei, gruen, tuerkis, blau, lavendel, lila, graphit; Standard: Farbe des Kalenders"},
        "status": {"type": "string", "enum": ["confirmed", "tentative", "cancelled"]},
        "transparency": {"type": "string", "enum": ["busy", "free"], "description": "free = blockiert keine freie Zeit"},
        "travel_min": {"type": "integer", "minimum": 0, "description": "Reisezeit vor dem Termin in Minuten (gilt bei freier Zeit als belegt)"},
    }
    upd = {k: v for k, v in fields.items() if k != "calendar"} | {"calendar": {**cal, "description": "in diesen Kalender verschieben (Name oder ID)"}}
    return [
        (ALL, "calendar_list", "Listet die Kalender (Name, ID, Farbe, Standard, was Agents dort dürfen).", obj({})),
        (ALL, "calendar_events", "Termine in einem Zeitraum (Serien sind aufgelöst, Feiertage dabei). from/to: JJJJ-MM-TT oder heute, morgen, diese Woche, nächste Woche, JJJJ-MM. "
                                 "Nur from = dieser Tag bzw. Zeitraum. Mit query nur Treffer.", obj({"from": E, "to": E, "calendar": cal, "query": E})),
        (ALL, "calendar_event_get", "Ein Termin mit allen Feldern (Beschreibung, Serie, Erinnerungen, Verlauf-Urheber). id aus calendar_events; bei Serien „<id>_<Zeit>“ für ein Vorkommen.", obj({"id": E}, ("id",))),
        (ALL, "calendar_event_create", "Legt einen Termin im Kalender an (Uhrzeit und Dauer – alles mit festem Termin gehört hierher; reminder_create nur für Erinnerungen ohne Termin). "
                                       "Meldet Überschneidungen mit anderen Terminen mit (nicht blockierend: im Zweifel dem Nutzer sagen).", obj(fields, ("title", "start"))),
        (ALL, "calendar_event_update", "Ändert einen Termin (nur die angegebenen Felder). Bei Serien mit scope bestimmen, was geändert wird; start verschiebt, die Dauer bleibt.",
         obj({"id": E, "scope": scope, **upd}, ("id",))),
        (ALL, "calendar_event_move", "Verschiebt einen Termin auf eine neue Zeit (die Dauer bleibt, außer end/duration_min ist angegeben).",
         obj({"id": E, "start": when, "end": {**E, "description": "optional neues Ende"}, "duration_min": {"type": "integer", "minimum": 1}, "scope": scope}, ("id", "start"))),
        (ALL, "calendar_event_delete", "Löscht einen Termin (Papierkorb, ohne Rückfrage; mit calendar_undo zurückholbar). Bei Serien scope angeben.", obj({"id": E, "scope": scope}, ("id",))),
        (ALL, "calendar_free_time", "Findet freie Zeitfenster. Ganztägige Termine und Termine mit transparency=free blockieren nicht; Reisezeit schon.",
         obj({"from": E, "to": E, "duration_min": {"type": "integer", "minimum": 1, "description": "so lang muss das Fenster mindestens sein, Standard 60"},
              "within": {**E, "description": "Tagesfenster, z. B. 08:00-20:00 (Standard)"}, "days": {"type": "array", "items": E, "description": "nur diese Wochentage, z. B. [\"mo\",\"mi\"]"}})),
        (ALL, "calendar_conflicts", "Prüft, ob ein Zeitraum frei ist (vor dem Anlegen); liefert die überschneidenden Termine.", obj({"start": when, "end": when}, ("start", "end"))),
        (ALL, "calendar_day_brief", "Kompakte Tagesübersicht (Termine, nächster Termin, freie Fenster) – z. B. für Morgen-Routinen. date: JJJJ-MM-TT, heute, morgen.", obj({"date": E})),
        (ALL, "calendar_search", "Volltextsuche in Titel, Ort, Beschreibung (Umlaute egal, Wortanfänge reichen, -wort schließt aus). Zeitangaben im Text wie „nächste woche“ gelten als Zeitraum.",
         obj({"query": E, "from": E, "to": E, "calendar": cal}, ("query",))),
        (ALL, "calendar_stats", "Wie viel Zeit geht für etwas drauf: Stunden je Kalender oder je Titel in einem Zeitraum (ohne ganztägige Termine).",
         obj({"from": E, "to": E, "group_by": {"type": "string", "enum": ["calendar", "title"]}})),
        (ALL, "calendar_undo", "Macht deine letzte Kalender-Änderung rückgängig (oder die mit change_id; die Id steht in den Antworten der Tools). Nur eigene Änderungen.", obj({"change_id": {"type": "integer"}})),
        (MAIN_LIVE, "calendar_create", "Legt einen neuen Kalender an (z. B. „Schule“).", obj({"name": E, "color": {**E, "description": "rot, koralle, orange, gelb, salbei, gruen, tuerkis, blau, lavendel, lila, graphit"},
                                                                                           "default_alarms": {"type": "array", "items": {"type": "integer"}}}, ("name",))),
        (MAIN_LIVE, "calendar_update", "Benennt einen Kalender um oder ändert Farbe/Standard-Erinnerungen. (Was Agents dürfen, stellt nur der Nutzer ein.)",
         obj({"id": E, "name": E, "color": E, "default_alarms": {"type": "array", "items": {"type": "integer"}}}, ("id",))),
        (MAIN, "calendar_import_ics", "Importiert eine .ics-Datei aus dem Chat (z. B. eine Einladung oder einen Stundenplan) in einen Kalender. Gleiche Termine (UID) werden nicht doppelt angelegt.",
         obj({"file_id": {**E, "description": "ID der Datei aus dem Chat"}, "calendar": cal}, ("file_id",))),
    ]


# ---------------- Hilfen ----------------
def _day(d: date) -> str:
    return f"{_DAYS[d.weekday()]}, {d.day}. {_MON[d.month - 1]}"


def when_text(v: dict) -> str:
    """„Do, 30. Okt. 15:00–16:00“ aus der Ansicht eines Vorkommens."""
    if v.get("all_day"):
        s, e = date.fromisoformat(v["start"][:10]), date.fromisoformat(v["end"][:10]) - timedelta(days=1)
        return f"{_day(s)} – {_day(e)} (ganztägig)" if e > s else f"{_day(s)} (ganztägig)"
    s, e = datetime.fromisoformat(v["start"]), datetime.fromisoformat(v["end"])
    if s.date() == e.date():
        return f"{_day(s.date())} {s:%H:%M}–{e:%H:%M}"
    return f"{_day(s.date())} {s:%H:%M} – {_day(e.date())} {e:%H:%M}"


def short(v: dict) -> dict:
    out = {"id": v["id"], "titel": v["title"], "wann": when_text(v), "start": v["start"], "end": v["end"], "ganztaegig": v["all_day"], "kalender": v["calendar"]}
    if v.get("location"):
        out["ort"] = v["location"]
    if v.get("recurrence_text"):
        out["serie"] = v["recurrence_text"]
    if v.get("status") and v["status"] != "confirmed":
        out["status"] = v["status"]
    if v.get("created_by") not in (None, "user", "system"):
        out["angelegt_von"] = v["created_by"]
    if v.get("holiday"):
        out["feiertag"] = True
    if v.get("age"):
        out["alter"] = v["age"]
    return out


def _date_only(v) -> bool:
    return isinstance(v, str) and bool(_DATE.match(v.strip()))


def _days(v) -> list[int] | None:
    if not v:
        return None
    out = []
    for d in v if isinstance(v, list) else [v]:
        i = _WD_NAMES.get(str(d).strip().lower().rstrip(".")) if not isinstance(d, int) else d
        if i is None or not 0 <= i <= 6:
            raise Invalid(f"Unbekannter Wochentag: {d} (mo, di, mi, do, fr, sa, so)")
        out.append(i)
    return out


async def _cal_id(by: str, ref: str | None) -> str | None:
    """Name oder ID → ID (der Kalender-Dienst versteht beides, aber für Filter brauchen wir die ID)."""
    if not ref:
        return None
    cals = (await kc.request("GET", "/api/v1/calendars", by=by))["calendars"]
    for c in cals:
        if ref in (c["id"],) or c["name"].lower() == str(ref).strip().lower():
            return c["id"]
    raise Invalid(f"Kalender „{ref}“ gibt es nicht (calendar_list zeigt die vorhandenen)")


def _body(a: dict, creating: bool, all_day_hint: bool | None = None) -> dict:
    """Tool-Argumente → Body für die Kalender-API."""
    b = {k: a[k] for k in ("title", "start", "end", "duration_min", "all_day", "location", "description", "url", "alarms", "alarm_action", "alarm_task",
                           "color", "status", "transparency", "travel_min", "rrule") if k in a and a[k] is not None}
    if a.get("calendar"):
        b["calendar"] = a["calendar"]
    if a.get("repeat"):
        b["repeat"] = {"freq": a["repeat"], "interval": a.get("interval") or 1, "days": _days(a.get("days")), "until": a.get("until"),
                       "count": a.get("count"), "nth": a.get("nth")}
    elif creating and (a.get("until") or a.get("count") or a.get("days")) and not a.get("rrule"):
        raise Invalid("until/count/days gehören zu einer Wiederholung – dann repeat angeben (daily, weekly, monthly, yearly, weekdays)")
    # ganztägig: der Kalender rechnet mit exklusivem Ende, das Tool mit dem letzten Tag
    all_day = a.get("all_day") if a.get("all_day") is not None else (_date_only(a.get("start")) if a.get("start") else all_day_hint)
    if all_day and _date_only(a.get("end")):
        b["end"] = (date.fromisoformat(a["end"].strip()) + timedelta(days=1)).isoformat()
    if all_day and creating and "start" in a and not _date_only(a["start"]):
        b["start"] = a["start"][:10]
    return b


def _ev_reply(res: dict, **extra) -> dict:
    ev = res["event"]
    out = {"ok": True, "id": ev["id"], "titel": ev["title"], "wann": when_text(ev), "kalender": ev["calendar"], **extra}
    if ev.get("recurrence_text"):
        out["serie"] = ev["recurrence_text"]
    if res.get("change_id"):
        out["change_id"] = res["change_id"]
        out["hinweis"] = "Mit calendar_undo(change_id) rückgängig zu machen."
    if res.get("conflicts"):
        out["ueberschneidung"] = [f"{c['title']} ({when_text({'start': c['start'], 'end': c['end'], 'all_day': False})})" for c in res["conflicts"]]
        out["hinweis"] = "Überschneidet sich mit einem anderen Termin – dem Nutzer sagen, falls das nicht gewollt ist."
    return out


# ---------------- Aufruf ----------------
async def call(db, me, name: str, a: dict) -> dict:
    """Führt ein calendar_*-Tool aus. Fehler des Kalenders kommen als Invalid/NotFound/KalenderOffline heraus."""
    by = me.agent_id
    R = lambda method, path, **kw: kc.request(method, path, by=by, **kw)  # noqa: E731
    try:
        out = await _run(db, R, by, name, a)
    except kc.KalenderOffline as e:
        return {"error": str(e)}
    if name not in ("calendar_list", "calendar_events", "calendar_event_get", "calendar_free_time", "calendar_conflicts", "calendar_day_brief",
                    "calendar_search", "calendar_stats"):
        kc.invalidate()
    return out


async def _run(db, R, by: str, name: str, a: dict) -> dict:
    if name == "calendar_list":
        cals = (await R("GET", "/api/v1/calendars"))["calendars"]
        return {"kalender": [{"id": c["id"], "name": c["name"], "farbe": c["color"], "standard": c["is_default"], "art": c["kind"],
                              "agent_recht": c["agent_access"], "termine": c.get("events", 0)} for c in cals]}
    if name == "calendar_events":
        params = {"from": a.get("from") or "heute"}
        if a.get("to"):
            params["to"] = a["to"]
        if a.get("calendar"):
            params["calendars"] = await _cal_id(by, a["calendar"])
        if a.get("query"):
            params["q"] = a["query"]
        evs = (await R("GET", "/api/v1/events", params=params))["events"]
        out = {"anzahl": len(evs), "termine": [short(v) for v in evs[:80]]}
        if len(evs) > 80:
            out["hinweis"] = f"Nur die ersten 80 von {len(evs)} – Zeitraum einengen."
        return out
    if name == "calendar_event_get":
        ev = (await R("GET", f"/api/v1/events/{a['id']}"))["event"]
        out = short(ev) | {"id": ev["id"], "beschreibung": ev.get("description", ""), "erinnerungen_min": ev.get("alarms", []), "farbe": ev.get("color"),
                           "reisezeit_min": ev.get("travel_min", 0), "frei_gebucht": ev.get("transparency") == "free", "zuletzt_geaendert_von": ev.get("updated_by")}
        if ev.get("rrule"):
            out["rrule"] = ev["rrule"]
            out["ausgelassen"] = ev.get("exdates", [])
        if ev.get("url"):
            out["link"] = ev["url"]
        if ev.get("alarm_action") == "agent":
            out["erinnerung_aufgabe"] = ev.get("alarm_task")
        return out
    if name == "calendar_event_create":
        res = await R("POST", "/api/v1/events", json=_body(a, True))
        return _ev_reply(res)
    if name in ("calendar_event_update", "calendar_event_move"):
        hint = None
        if _date_only(a.get("end")) and a.get("all_day") is None and not _date_only(a.get("start")):
            hint = bool((await R("GET", f"/api/v1/events/{a['id']}"))["event"]["all_day"])
        body = _body(a, False, hint)
        body.pop("id", None)
        if not body:
            raise Invalid("Nichts zu ändern angegeben")
        res = await R("PATCH", f"/api/v1/events/{a['id']}", params={"scope": a.get("scope") or "all"}, json=body)
        return _ev_reply(res)
    if name == "calendar_event_delete":
        res = await R("DELETE", f"/api/v1/events/{a['id']}", params={"scope": a.get("scope") or "all"})
        return {"ok": True, "geloescht": res["title"], "change_id": res.get("change_id"), "hinweis": "Liegt im Papierkorb; mit calendar_undo(change_id) zurückzuholen."}
    if name == "calendar_free_time":
        params = {"from": a.get("from") or "heute", "duration": a.get("duration_min") or 60}
        if a.get("to"):
            params["to"] = a["to"]
        if a.get("within"):
            params["within"] = a["within"]
        d = _days(a.get("days"))
        if d:
            params["days"] = ",".join(map(str, d))
        slots = (await R("GET", "/api/v1/freebusy", params=params))["slots"]
        return {"anzahl": len(slots), "freie_fenster": [{"wann": s["text"], "minuten": s["minutes"], "start": s["start"], "end": s["end"]} for s in slots],
                **({} if slots else {"hinweis": "Keine freie Zeit in diesem Zeitraum – anderes Fenster/kürzere Dauer probieren."})}
    if name == "calendar_conflicts":
        r = await R("GET", "/api/v1/conflicts", params={"start": a["start"], "end": a["end"]})
        return {"frei": r["free"], "ueberschneidungen": [short(v) for v in r["conflicts"]]}
    if name == "calendar_day_brief":
        r = await R("GET", "/api/v1/brief", params={"date": a.get("date") or "heute"})
        return {"datum": r["datum"], "wochentag": r["wochentag"], "uebersicht": r["uebersicht"], "naechster": r["naechster"], "freie_fenster": r["freie_fenster"],
                "termine": [short(v) for v in r["termine"]]}
    if name == "calendar_search":
        params = {"q": a["query"]}
        if a.get("from"):
            params["from"] = a["from"]
        if a.get("to"):
            params["to"] = a["to"]
        if a.get("calendar"):
            params["calendars"] = await _cal_id(by, a["calendar"])
        r = await R("GET", "/api/v1/search", params=params)
        out = {"treffer": r["total"], "demnaechst": [short(v) | ({"weitere_termine": v["more"]} if v.get("more") else {}) for v in r["upcoming"][:25]],
               "vergangen": [short(v) for v in r["past"][:15]]}
        if r.get("range"):
            out["zeitraum"] = r["range"]["label"]
        return out
    if name == "calendar_stats":
        r = await R("GET", "/api/v1/stats", params={"from": a.get("from") or "diese Woche", **({"to": a["to"]} if a.get("to") else {}), "group_by": a.get("group_by") or "calendar"})
        return {"gesamt_stunden": r["gesamt_stunden"], "gruppiert_nach": r["gruppiert_nach"], "zeilen": r["zeilen"]}
    if name == "calendar_undo":
        r = await R("POST", "/api/v1/undo", json={"change_id": a["change_id"]} if a.get("change_id") else {})
        return {"ok": True, "rueckgaengig": r["titles"], "change_id": r.get("change_id"), "hinweis": "Mit calendar_undo(change_id) lässt sich das wiederum rückgängig machen."}
    if name == "calendar_create":
        body = {k: a[k] for k in ("name", "color", "default_alarms") if a.get(k) is not None}
        c = await R("POST", "/api/v1/calendars", json=body)
        return {"ok": True, "id": c["id"], "name": c["name"], "farbe": c["color"]}
    if name == "calendar_update":
        body = {k: a[k] for k in ("name", "color", "default_alarms") if a.get(k) is not None}
        if not body:
            raise Invalid("Nichts zu ändern angegeben")
        c = await R("PATCH", f"/api/v1/calendars/{await _cal_id(by, a['id'])}", json=body)
        return {"ok": True, "id": c["id"], "name": c["name"], "farbe": c["color"]}
    if name == "calendar_import_ics":
        f = await files.get(db, str(a["file_id"]))
        data = files.path(f).read_bytes()
        r = await R("POST", "/api/v1/import", files={"file": (f.name or "import.ics", data, "text/calendar")}, data={"calendar": a["calendar"]} if a.get("calendar") else None, timeout=60)
        return {"ok": True, "neu": r["neu"], "aktualisiert": r["aktualisiert"], "uebersprungen": r["uebersprungen"], **({"fehler": r["fehler"]} if r.get("fehler") else {})}
    return {"error": f"Unbekanntes Tool: {name}"}
