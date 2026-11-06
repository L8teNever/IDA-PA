"""tasks_*-Tools des internen MCP: Ida und Stella bedienen die Aufgaben-App (siehe docs/TASKS.md).

Dünne Clients auf die API des Tasks-Dienstes (`tasks_client`), `X-Tasks-By` = Agent-ID (die App zeigt „von Stella“).
Tage dürfen als Wort kommen („morgen“, „freitag“, „12.11.“) – der Dienst rechnet sie selbst um.
"""
from .. import tasks_client as tc

ALL = "all"
E = {"type": "string"}
PRIO = {"keine": 0, "niedrig": 1, "mittel": 2, "hoch": 3}
PRIO_NAME = {v: k for k, v in PRIO.items()}
DAY_HINT = "JJJJ-MM-TT oder ein Wort: heute, morgen, übermorgen, freitag, nächste woche, in 3 tagen, 12.11."


def enabled() -> bool:
    return tc.enabled()


def spec() -> list[tuple[str, str, str, dict]]:
    obj = lambda props, req=(): {"type": "object", "properties": props, **({"required": list(req)} if req else {})}  # noqa: E731
    prio = {"type": "string", "enum": list(PRIO), "description": "Priorität: hoch, mittel, niedrig oder keine"}
    day = {**E, "description": f"Ab diesem Tag steht sie auf der Startseite („mach ich am …“); bis dahin unter Geplant. {DAY_HINT}. Weglassen/heute = sofort"}
    due = {**E, "description": f"Optional: bis wann sie fertig sein muss (Frist, Ende eines Zeitraums). {DAY_HINT}"}
    return [
        (ALL, "tasks_list", "Aufgaben aus der Aufgaben-App (Tasks). view: heute = Startseite (fällig), geplant = später eingeplant, offen = alles "
                            "Unerledigte, erledigt, alle. Mit from/to nur Aufgaben, deren Tag oder Frist in dem Zeitraum liegt.",
         obj({"view": {"type": "string", "enum": ["heute", "geplant", "offen", "erledigt", "alle"], "description": "Standard: offen"},
              "query": {**E, "description": "Suchwörter in Titel/Notiz"}, "from": {**E, "description": DAY_HINT}, "to": {**E, "description": DAY_HINT}})),
        (ALL, "tasks_create", "Legt eine Aufgabe in der Aufgaben-App an. Für mehrere Aufgaben das Tool mehrmals aufrufen.",
         obj({"title": {**E, "description": "kurz und klar, z. B. „Zahnarzt anrufen“"}, "note": {**E, "description": "Details (optional)"},
              "priority": prio, "day": day, "due": due}, ("title",))),
        (ALL, "tasks_update", "Ändert eine Aufgabe (id aus tasks_list): Titel, Notiz, Priorität, Tag (planen/verschieben) oder Frist. "
                              "Nur angegebene Felder ändern sich; day=\"heute\" holt sie auf die Startseite, due=\"\" entfernt die Frist.",
         obj({"id": E, "title": E, "note": E, "priority": prio, "day": day, "due": due}, ("id",))),
        (ALL, "tasks_done", "Hakt eine Aufgabe als erledigt ab – oder mit undo=true wieder zurück auf offen.",
         obj({"id": E, "undo": {"type": "boolean"}}, ("id",))),
        (ALL, "tasks_delete", "Löscht eine Aufgabe endgültig (Erledigtes besser mit tasks_done abhaken).", obj({"id": E}, ("id",))),
    ]


def short(t: dict) -> dict:
    out = {"id": t["id"], "titel": t["title"], "status": t["state"]}
    if t.get("priority"):
        out["prioritaet"] = PRIO_NAME.get(t["priority"], t["priority"])
    if t.get("day"):
        out["tag"] = t["day"]
    if t.get("due"):
        out["bis"] = t["due"]
    if t.get("overdue"):
        out["ueberfaellig"] = True
    if t.get("note"):
        out["notiz"] = t["note"][:300]
    if t.get("created_by") and t["created_by"] != "user":
        out["von"] = t["created_by"]
    return out


def _prio(v) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, int) and 0 <= v <= 3:
        return v
    p = PRIO.get(str(v).strip().lower())
    if p is None:
        raise ValueError("priority: hoch, mittel, niedrig oder keine")
    return p


async def call(db, me, name: str, a: dict) -> dict:
    by = me.agent_id
    R = lambda method, path, **kw: tc.request(method, path, by=by, **kw)  # noqa: E731
    try:
        return await _run(R, name, a)
    except tc.TasksOffline as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": str(e)}


async def _run(R, name: str, a: dict) -> dict:
    if name == "tasks_list":
        params = {"view": a.get("view") or "offen", "limit": 200}
        if a.get("query"):
            params["q"] = a["query"]
        if a.get("from"):
            params["day_from"] = a["from"]
        if a.get("to"):
            params["day_to"] = a["to"]
        got = await R("GET", "/api/v1/tasks", params=params)
        tasks = got["tasks"]
        out = {"heute": got.get("today"), "anzahl": len(tasks), "aufgaben": [short(t) for t in tasks[:80]]}
        if len(tasks) > 80:
            out["hinweis"] = f"Nur die ersten 80 von {len(tasks)}."
        return out
    if name == "tasks_create":
        body = {"title": str(a.get("title") or "").strip(), "note": str(a.get("note") or ""), "day": a.get("day") or None, "due": a.get("due") or None}
        p = _prio(a.get("priority"))
        if p is not None:
            body["priority"] = p
        t = (await R("POST", "/api/v1/tasks", json=body))["task"]
        return {"ok": True, "aufgabe": short(t)}
    if name == "tasks_update":
        body = {k: a[k] for k in ("title", "note") if a.get(k) is not None}
        for k in ("day", "due"):
            if k in a and a[k] is not None:
                body[k] = a[k] or None
        p = _prio(a.get("priority"))
        if p is not None:
            body["priority"] = p
        if not body:
            return {"error": "Nichts zu ändern angegeben"}
        t = (await R("PATCH", f"/api/v1/tasks/{a['id']}", json=body))["task"]
        return {"ok": True, "aufgabe": short(t)}
    if name == "tasks_done":
        t = (await R("PATCH", f"/api/v1/tasks/{a['id']}", json={"done": not a.get("undo")}))["task"]
        return {"ok": True, "aufgabe": short(t)}
    if name == "tasks_delete":
        t = (await R("DELETE", f"/api/v1/tasks/{a['id']}"))["task"]
        return {"ok": True, "geloescht": t["title"]}
    return {"error": f"Unbekanntes Tool: {name}"}
