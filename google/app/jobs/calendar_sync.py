"""Kalender-Abgleich: IDA-Kalender <-> Google-Kalender, in beide Richtungen.

Grundidee: Je Termin merken wir uns pro Seite einen Fingerabdruck (Hash) vom Stand des letzten Abgleichs. Hat sich seitdem nur
eine Seite geaendert, geht die Aenderung auf die andere. Haben sich beide geaendert, gewinnt die juengere Aenderung. Weil jede
Seite nur mit ihrem EIGENEN letzten Stand verglichen wird, gibt es keine Endlosschleifen (Echo) und es muss nichts zwischen den
Formaten verglichen werden.

Bewusst nicht dabei: einzelne geaenderte Vorkommen einer Serie (Ausnahmen) -- die werden uebersprungen und gemeldet.
Sicherheitsnetz: Loescht ein Lauf mehr als `max_deletes` Termine, bricht er ab (nichts wird geloescht).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from app.google_client import GoogleApiClient, GoogleApiError
from app.jobs.state import State

log = logging.getLogger("ida-google.calsync")
UTC = timezone.utc
G_BASE = "https://www.googleapis.com/calendar/v3"
SCOPES = ["https://www.googleapis.com/auth/calendar"]
PRIVATE_KEY = "idaKalenderId"


class SyncAbort(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------------------------- Formate
def _utc_str(v: str) -> str:
    d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(v: str) -> datetime:
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _rule_norm(rule: str) -> str:
    parts = sorted(p for p in (rule or "").replace("RRULE:", "").split(";") if p and not p.startswith("WKST="))
    return ";".join(parts)


def fingerprint(ev: dict) -> str:
    """ev: gemeinsames Zwischenformat (siehe k_norm / g_norm)."""
    raw = json.dumps([ev["title"].strip(), (ev["description"] or "").strip(), (ev["location"] or "").strip(), ev["start"], ev["end"],
                      bool(ev["all_day"]), _rule_norm(ev["rrule"])], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()


def signature(ev: dict) -> tuple:
    return (ev["title"].strip().casefold(), ev["start"], ev["end"], bool(ev["all_day"]))


def k_norm(d: dict) -> dict:
    """IDA-Kalender-Termin (Detail-Ansicht) -> Zwischenformat."""
    all_day = bool(d.get("all_day"))
    return {"id": d["event_id"] if "event_id" in d else d["id"], "title": d.get("title") or "", "description": d.get("description") or "",
            "location": d.get("location") or "", "all_day": all_day, "tzid": d.get("tzid") or "Europe/Berlin", "rrule": d.get("rrule") or "",
            "start": d["start"][:10] if all_day else _utc_str(d["start"]), "end": d["end"][:10] if all_day else _utc_str(d["end"]),
            "updated": d.get("updated_at") or "", "raw_start": d["start"], "raw_end": d["end"]}


def g_norm(e: dict, default_tz: str) -> dict:
    s, en = e.get("start") or {}, e.get("end") or {}
    all_day = "date" in s
    rule = next((r for r in e.get("recurrence") or [] if r.upper().startswith("RRULE:")), "")
    return {"id": e["id"], "title": e.get("summary") or "", "description": e.get("description") or "", "location": e.get("location") or "",
            "all_day": all_day, "tzid": s.get("timeZone") or default_tz, "rrule": rule.replace("RRULE:", ""),
            "start": s["date"] if all_day else _utc_str(s["dateTime"]), "end": en["date"] if all_day else _utc_str(en["dateTime"]),
            "updated": e.get("updated") or "", "raw_start": s.get("date") or s.get("dateTime"), "raw_end": en.get("date") or en.get("dateTime")}


def _until_for_google(rule: str, tzid: str, all_day: bool) -> str:
    """UNTIL aus dem IDA-Kalender (Ortszeit ohne Z) -> Google (Datum bzw. UTC)."""
    def fix(m: re.Match) -> str:
        v = m.group(1)
        if len(v) == 8:
            return f"UNTIL={v}"
        if v.endswith("Z"):
            return f"UNTIL={v[:8]}" if all_day else f"UNTIL={v}"
        if all_day:
            return f"UNTIL={v[:8]}"
        loc = datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(tzid))
        return "UNTIL=" + loc.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return re.sub(r"UNTIL=(\w+)", fix, rule)


def to_google_body(k: dict, k_id: str, clear: bool = False) -> dict:
    """clear=True (beim Aendern): das jeweils andere Zeitfeld wird ausdruecklich geleert (Wechsel ganztaegig <-> mit Uhrzeit)."""
    body: dict = {"summary": k["title"], "description": k["description"], "location": k["location"],
                  "extendedProperties": {"private": {PRIVATE_KEY: k_id}}}
    if k["all_day"]:
        body["start"], body["end"] = {"date": k["raw_start"][:10]}, {"date": k["raw_end"][:10]}
        if clear:
            for f in (body["start"], body["end"]):
                f.update({"dateTime": None, "timeZone": None})
    else:
        body["start"] = {"dateTime": k["raw_start"], "timeZone": k["tzid"]}
        body["end"] = {"dateTime": k["raw_end"], "timeZone": k["tzid"]}
        if clear:
            body["start"]["date"] = body["end"]["date"] = None
    body["recurrence"] = [f"RRULE:{_until_for_google(k['rrule'], k['tzid'], k['all_day'])}"] if k["rrule"] else []
    return body


def to_kalender_body(g: dict) -> dict:
    body = {"title": g["title"].strip() or "(Ohne Titel)", "description": g["description"], "location": g["location"], "all_day": g["all_day"],
            "tzid": g["tzid"], "start": g["raw_start"], "end": g["raw_end"], "rrule": g["rrule"]}
    return body


# ---------------------------------------------------------------------------------------------------------------- Zugriff
class KalenderApi:
    """Duenner Client fuer die interne Kalender-API (ohne Token gilt der Aufruf als der Nutzer, im Docker-Netz)."""

    def __init__(self, base: str, token: str = "", by: str = "google-sync") -> None:
        self.base = base.rstrip("/") + "/api/v1"
        self.headers = {"Authorization": f"Bearer {token}", "X-Kalender-By": by} if token else {}

    def _call(self, method: str, path: str, **kw):
        r = requests.request(method, self.base + path, headers=self.headers, timeout=30, **kw)
        if r.status_code == 404:
            return None
        if r.status_code >= 300:
            try:
                msg = r.json().get("detail", r.text)
            except ValueError:
                msg = r.text
            raise GoogleApiError(f"Kalender-Fehler {r.status_code}: {str(msg)[:200]}")
        return r.json() if r.content else None

    def calendars(self) -> list[dict]:
        return (self._call("GET", "/calendars") or {}).get("calendars", [])

    def create_calendar(self, name: str) -> dict:
        return self._call("POST", "/calendars", json={"name": name})

    def event_ids(self, calendar_id: str, frm: str, to: str) -> list[str]:
        d = self._call("GET", "/events", params={"from": frm, "to": to, "calendars": calendar_id}) or {}
        return sorted({e["event_id"] for e in d.get("events", []) if e.get("event_id") and not e.get("read_only")})

    def get(self, event_id: str) -> dict | None:
        d = self._call("GET", f"/events/{event_id}")
        ev = (d or {}).get("event")
        return None if not ev or ev.get("deleted") else ev

    def create(self, body: dict) -> dict:
        return self._call("POST", "/events", json=body)["event"]

    def update(self, event_id: str, body: dict) -> dict:
        return self._call("PATCH", f"/events/{event_id}", params={"scope": "all"}, json=body)["event"]

    def delete(self, event_id: str) -> None:
        self._call("DELETE", f"/events/{event_id}", params={"scope": "all"})


class GoogleCal:
    def __init__(self, client: GoogleApiClient, calendar_id: str = "primary") -> None:
        self.c = client
        self.cal = requests.utils.quote(calendar_id, safe="")

    def _req(self, method, path="", **kw):
        return self.c.request(method, f"{G_BASE}/calendars/{self.cal}/events{path}", scopes=SCOPES, **kw)

    def list(self, tmin: str, tmax: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"timeMin": tmin, "timeMax": tmax, "singleEvents": False, "showDeleted": False, "maxResults": 250}
            if token:
                params["pageToken"] = token
            d = self._req("GET", params=params) or {}
            out += d.get("items", [])
            token = d.get("nextPageToken")
            if not token:
                return out

    def get(self, event_id: str) -> dict | None:
        try:
            e = self._req("GET", f"/{event_id}")
        except GoogleApiError as exc:
            if "Not Found" in str(exc) or "deleted" in str(exc).lower():
                return None
            raise
        return None if not e or e.get("status") == "cancelled" else e

    def create(self, body: dict) -> dict:
        return self._req("POST", json_body=body, params={"sendUpdates": "none"})

    def update(self, event_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/{event_id}", json_body=body, params={"sendUpdates": "none"})

    def delete(self, event_id: str) -> None:
        try:
            self._req("DELETE", f"/{event_id}", params={"sendUpdates": "none"})
        except GoogleApiError as exc:
            if "Resource has been deleted" not in str(exc) and "Not Found" not in str(exc):
                raise


# ---------------------------------------------------------------------------------------------------------------- Abgleich
class CalendarSync:
    def __init__(self, kal: KalenderApi, goo: GoogleCal, state: State, *, calendar: str = "", timezone_name: str = "Europe/Berlin",
                 past_days: int = 30, future_days: int = 365, max_deletes: int = 20, now: datetime | None = None) -> None:
        self.kal, self.goo, self.state = kal, goo, state
        self.calendar, self.tz, self.past, self.future, self.max_deletes = calendar, timezone_name, past_days, future_days, max_deletes
        self._now = now

    def _kalender_id(self) -> str:
        cals = [c for c in self.kal.calendars() if c.get("kind") == "normal" and not c.get("read_only")]
        if self.calendar:
            hit = next((c for c in cals if c["id"] == self.calendar or c["name"].casefold() == self.calendar.casefold()), None)
            if not hit:
                raise SyncAbort(f"Kalender „{self.calendar}“ gibt es im IDA-Kalender nicht")
            return hit["id"]
        hit = next((c for c in cals if c.get("is_default")), cals[0] if cals else None)
        if not hit:
            raise SyncAbort("Im IDA-Kalender gibt es keinen Kalender zum Abgleichen")
        return hit["id"]

    def run(self, dry_run: bool = False) -> dict:
        now = self._now or datetime.now(UTC)
        frm, to = (now - timedelta(days=self.past)), (now + timedelta(days=self.future))
        cal_id = self._kalender_id()
        rep = {"dry_run": dry_run, "pull": 0, "push": 0, "new_in_ida": 0, "new_in_google": 0, "linked": 0, "deleted_ida": 0,
               "deleted_google": 0, "skipped": [], "errors": [], "plan": []}

        def do(action: str, title: str, fn) -> bool:
            rep["plan"].append(f"{action}: {title}")
            if dry_run:
                return True
            try:
                fn()
                return True
            except (GoogleApiError, requests.RequestException, ValueError, KeyError) as exc:
                rep["errors"].append(f"{action} „{title}“: {exc}")
                log.warning("%s %s: %s", action, title, exc)
                return False

        # --- Stand beider Seiten
        k_all: dict[str, dict] = {}
        for kid in self.kal.event_ids(cal_id, frm.date().isoformat(), to.date().isoformat()):
            d = self.kal.get(kid)
            if not d:
                continue
            if d.get("parent_id"):
                rep["skipped"].append(f"Einzelnes Vorkommen einer Serie: {d.get('title')}")
                continue
            k_all[kid] = k_norm({**d, "event_id": kid})
        g_all: dict[str, dict] = {}
        for e in self.goo.list(frm.strftime("%Y-%m-%dT%H:%M:%SZ"), to.strftime("%Y-%m-%dT%H:%M:%SZ")):
            if e.get("status") == "cancelled" or e.get("recurringEventId") or e.get("eventType", "default") != "default" or "start" not in e:
                if e.get("recurringEventId"):
                    rep["skipped"].append(f"Einzelnes Vorkommen einer Serie: {e.get('summary', '')}")
                continue
            g_all[e["id"]] = g_norm(e, self.tz)

        # nur die Zuordnungen DIESES Kalenders (aeltere ohne Kalender werden uebernommen, wenn ihr Termin hier liegt)
        pairs = {}
        for p in self.state.pairs():
            if p["k_cal"] == cal_id or (p["k_cal"] == "" and p["k_id"] in k_all):
                if p["k_cal"] == "" and not dry_run:
                    self.state.set_cal(p["k_id"], cal_id)
                pairs[p["k_id"]] = p
        g_pairs = {p["g_id"]: p for p in pairs.values()}
        deletes: list = []

        # --- 1. Bereits verbundene Termine
        for kid, p in pairs.items():
            k = k_all.get(kid)
            if k is None:
                raw = self.kal.get(kid)
                k = k_norm({**raw, "event_id": kid}) if raw and not raw.get("parent_id") else None
            g = g_all.get(p["g_id"])
            if g is None:
                raw = self.goo.get(p["g_id"])
                g = g_norm(raw, self.tz) if raw and "start" in raw else None
            if k is None and g is None:
                if not dry_run:
                    self.state.drop_pair(kid)
                continue
            if k is None or g is None:
                deletes.append((kid, p, k, g))
                continue
            kc, gc = fingerprint(k) != p["k_hash"], fingerprint(g) != p["g_hash"]
            if not kc and not gc:
                continue
            to_google = kc and (not gc or _ts(k["updated"]) >= _ts(g["updated"]))
            if to_google:
                def push(kid=kid, k=k, p=p):
                    e = self.goo.update(p["g_id"], to_google_body(k, kid, clear=True))
                    self.state.put_pair(kid, p["g_id"], fingerprint(k), fingerprint(g_norm(e, self.tz)), cal_id)
                if do("Nach Google", k["title"], push):
                    rep["push"] += 1
            else:
                def pull(kid=kid, g=g, p=p):
                    d = self.kal.update(kid, to_kalender_body(g))
                    self.state.put_pair(kid, p["g_id"], fingerprint(k_norm({**d, "event_id": kid})), fingerprint(g), cal_id)
                if do("Nach IDA", g["title"], pull):
                    rep["pull"] += 1

        # --- Loeschungen (mit Sicherheitsnetz)
        if len(deletes) > self.max_deletes:
            raise SyncAbort(f"Abbruch: {len(deletes)} Termine waeren zu loeschen (Grenze {self.max_deletes}). Nichts wurde geloescht.")
        for kid, p, k, g in deletes:
            if k is None:   # in IDA geloescht -> auch in Google
                if do("In Google loeschen", g["title"], lambda g=g: self.goo.delete(g["id"])):
                    rep["deleted_google"] += 1
            else:           # in Google geloescht -> auch in IDA
                if do("In IDA loeschen", k["title"], lambda kid=kid: self.kal.delete(kid)):
                    rep["deleted_ida"] += 1
            if not dry_run:
                self.state.drop_pair(kid)

        # --- 2. Neue Termine
        new_k = {i: v for i, v in k_all.items() if i not in pairs}
        new_g = {i: v for i, v in g_all.items() if i not in g_pairs}
        by_sig: dict[tuple, str] = {}
        for gid, g in new_g.items():
            by_sig.setdefault(signature(g), gid)
        for kid, k in list(new_k.items()):
            gid = by_sig.pop(signature(k), None)
            if gid is None or gid not in new_g:
                continue
            g = new_g.pop(gid)
            new_k.pop(kid)
            if do("Verbinden", k["title"], lambda kid=kid, k=k, gid=gid, g=g: self.state.put_pair(kid, gid, fingerprint(k), fingerprint(g), cal_id)):
                rep["linked"] += 1
        for kid, k in new_k.items():
            def create_g(kid=kid, k=k):
                e = self.goo.create(to_google_body(k, kid))
                self.state.put_pair(kid, e["id"], fingerprint(k), fingerprint(g_norm(e, self.tz)), cal_id)
            if do("Neu in Google", k["title"], create_g):
                rep["new_in_google"] += 1
        for gid, g in new_g.items():
            def create_k(gid=gid, g=g):
                d = self.kal.create({**to_kalender_body(g), "calendar_id": cal_id, "uid": f"{gid}@google-sync"})
                kid = d["event_id"] if "event_id" in d else d["id"]
                self.state.put_pair(kid, gid, fingerprint(k_norm({**d, "event_id": kid})), fingerprint(g), cal_id)
            if do("Neu in IDA", g["title"], create_k):
                rep["new_in_ida"] += 1
        return rep


# ---------------------------------------------------------------------------------------------------------------- Mehrere Kalender
_NO_SYNC_IDS = ("#holiday@group.v.calendar.google.com", "#contacts@group.v.calendar.google.com", "@import.calendar.google.com")


def google_calendars(client: GoogleApiClient) -> list[dict]:
    """Alle Google-Kalender, in die geschrieben werden darf (ohne Feiertage, Geburtstage und Abos)."""
    d = client.request("GET", f"{G_BASE}/users/me/calendarList", scopes=SCOPES, params={"minAccessRole": "writer"}) or {}
    return [{"id": c["id"], "name": c.get("summaryOverride") or c.get("summary") or c["id"], "primary": bool(c.get("primary"))}
            for c in d.get("items", []) if not any(c["id"].endswith(x) for x in _NO_SYNC_IDS)]


class MultiSync:
    """Gleicht ALLE Kalender ab. Der Standardkalender von IDA gehoert zum Hauptkalender von Google, alle anderen Kalender werden ueber den
    Namen zugeordnet. Fehlt die Gegenseite, wird sie angelegt, aber nur, wenn der Kalender Termine hat (leere bleiben aussen vor)."""

    def __init__(self, kal: KalenderApi, client: GoogleApiClient, state: State, *, only_calendar: str = "", timezone_name: str = "Europe/Berlin",
                 past_days: int = 30, future_days: int = 365, max_deletes: int = 20, now: datetime | None = None) -> None:
        self.kal, self.client, self.state, self.only = kal, client, state, only_calendar
        self.kw = {"timezone_name": timezone_name, "past_days": past_days, "future_days": future_days, "max_deletes": max_deletes, "now": now}
        self._now = now or datetime.now(UTC)
        self.past, self.future = past_days, future_days

    def _window(self) -> tuple[str, str]:
        return (self._now - timedelta(days=self.past)).date().isoformat(), (self._now + timedelta(days=self.future)).date().isoformat()

    def _google_has_events(self, gid: str) -> bool:
        a, b = (self._now - timedelta(days=self.past)), (self._now + timedelta(days=self.future))
        items = GoogleCal(self.client, gid).list(a.strftime("%Y-%m-%dT%H:%M:%SZ"), b.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return any(e.get("status") != "cancelled" and "start" in e for e in items)

    def pairs(self, dry_run: bool, notes: list[str]) -> list[tuple[dict, dict]]:
        kcals = [c for c in self.kal.calendars() if c.get("kind") == "normal" and not c.get("read_only")]
        gcals = google_calendars(self.client)
        primary = next((g for g in gcals if g["primary"]), None)
        if not kcals or primary is None:
            raise SyncAbort("Es fehlt ein IDA-Kalender oder der Google-Hauptkalender")
        default = next((c for c in kcals if c.get("is_default")), kcals[0])
        if self.only:
            hit = next((c for c in kcals if c["id"] == self.only or c["name"].casefold() == self.only.casefold()), None)
            if not hit:
                raise SyncAbort(f"Kalender „{self.only}“ gibt es im IDA-Kalender nicht")
            return [(hit, primary)]
        pairs, used_k, used_g = [(default, primary)], {default["id"]}, {primary["id"]}
        frm, to = self._window()
        for k in kcals:                                    # IDA-Kalender -> Google (Name)
            if k["id"] in used_k:
                continue
            g = next((x for x in gcals if x["id"] not in used_g and x["name"].casefold() == k["name"].casefold()), None)
            if g is None:
                if not self.kal.event_ids(k["id"], frm, to):
                    continue
                notes.append(f"Neuer Kalender in Google: {k['name']}")
                if dry_run:
                    continue
                g = {"id": self.client.request("POST", f"{G_BASE}/calendars", scopes=SCOPES, json_body={"summary": k["name"]})["id"], "name": k["name"], "primary": False}
            used_k.add(k["id"]); used_g.add(g["id"])
            pairs.append((k, g))
        names = {c["name"].casefold() for c in kcals}
        for g in gcals:                                    # Google-Kalender -> IDA (Name)
            if g["id"] in used_g:
                continue
            k = next((x for x in kcals if x["id"] not in used_k and x["name"].casefold() == g["name"].casefold()), None)
            if k is None:
                if not self._google_has_events(g["id"]):
                    continue
                name = g["name"] if g["name"].casefold() not in names else f"{g['name']} (Google)"
                notes.append(f"Neuer Kalender in IDA: {name}")
                if dry_run:
                    continue
                k = self.kal.create_calendar(name)
                names.add(name.casefold())
            used_k.add(k["id"]); used_g.add(g["id"])
            pairs.append((k, g))
        return pairs

    def run(self, dry_run: bool = False) -> dict:
        notes: list[str] = []
        total = {"dry_run": dry_run, "pull": 0, "push": 0, "new_in_ida": 0, "new_in_google": 0, "linked": 0, "deleted_ida": 0,
                 "deleted_google": 0, "skipped": [], "errors": [], "plan": notes, "kalender": []}
        for k, g in self.pairs(dry_run, notes):
            try:
                rep = CalendarSync(self.kal, GoogleCal(self.client, g["id"]), self.state, calendar=k["id"], **self.kw).run(dry_run=dry_run)
            except (SyncAbort, GoogleApiError) as exc:
                total["errors"].append(f"{k['name']}: {exc}")
                continue
            total["kalender"].append(f"{k['name']} ⇄ {g['name']}")
            for key in ("pull", "push", "new_in_ida", "new_in_google", "linked", "deleted_ida", "deleted_google"):
                total[key] += rep[key]
            total["skipped"] += rep["skipped"]
            total["errors"] += [f"{k['name']}: {e}" for e in rep["errors"]]
            total["plan"] += [f"[{k['name']}] {p}" for p in rep["plan"]]
        return total
