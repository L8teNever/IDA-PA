"""Routine triggers: decide from the text result of a connector tool whether a routine should start.

The check itself needs no AI (the server calls the MCP tool); only a started routine uses the model.
  mode "new":      start when entries appear that were not there before (e.g. new mails). Entries are
                   identified by an id field of a JSON list, otherwise by line. The first check is the baseline.
  mode "change":   start when the result differs from the last check.
  mode "contains": start when the text starts to contain a phrase (again only after it was gone once).
"""
import hashlib
import json

MAX_KEYS = 500
ID_FIELDS = ("id", "messageId", "message_id", "uid", "threadId", "thread_id", "guid", "link", "url")


def _sha(s: str) -> str:
    return hashlib.sha1(str(s).encode()).hexdigest()[:12]


def _biggest_list(x, depth: int = 0):
    if isinstance(x, list):
        return x
    if not isinstance(x, dict) or depth > 2:
        return None
    best = None
    for v in x.values():
        a = _biggest_list(v, depth + 1)
        if a is not None and (best is None or len(a) > len(best)):
            best = a
    return best


def _key(it) -> str:
    if isinstance(it, dict):
        for k in ID_FIELDS:
            if it.get(k) is not None and not isinstance(it[k], (dict, list)):
                return f"{k}:{it[k]}"
        return "h:" + _sha(json.dumps(it, sort_keys=True))
    return "v:" + _sha(it)


def extract(text: str) -> list[dict]:
    """text -> [{"key", "label"}]"""
    t = str(text or "").strip()
    if not t:
        return []
    try:
        arr = _biggest_list(json.loads(t))
        if arr is not None:
            return [{"key": _key(it), "label": it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)} for it in arr]
    except ValueError:
        pass
    return [{"key": "l:" + _sha(line), "label": line} for line in (x.strip() for x in t.splitlines()) if line]


def evaluate(trigger: dict, text: str) -> dict:
    """-> {"fire", "state", "note", "context"?}"""
    state = trigger.get("state") or {}
    body = str(text or "")
    mode = trigger.get("mode", "new")
    if mode == "change":
        sig = _sha(body)
        if not state.get("init"):
            return {"fire": False, "state": {"init": True, "sig": sig}, "note": "Ausgangszustand gespeichert"}
        if sig != state.get("sig"):
            return {"fire": True, "state": {"init": True, "sig": sig}, "note": "Ergebnis hat sich geändert",
                    "context": "Der Prüf-Aufruf liefert jetzt ein anderes Ergebnis als zuvor:\n" + body[:3000]}
        return {"fire": False, "state": state, "note": "unverändert"}
    if mode == "contains":
        phrase = str(trigger.get("contains") or "")
        hit = phrase.lower() in body.lower()
        fire = hit and not state.get("hit")
        out = {"fire": fire, "state": {"init": True, "hit": hit},
               "note": ("Text gefunden" if fire else "Text weiterhin vorhanden") if hit else "Text nicht vorhanden"}
        if fire:
            out["context"] = f"Im Ergebnis des Prüf-Aufrufs steht jetzt „{phrase}“:\n{body[:3000]}"
        return out
    items = extract(body)
    if not state.get("init"):
        return {"fire": False, "state": {"init": True, "keys": [i["key"] for i in items][-MAX_KEYS:]},
                "note": f"Ausgangszustand gespeichert ({len(items)} Einträge)"}
    seen = set(state.get("keys") or [])
    fresh = [i for i in items if i["key"] not in seen]
    if not fresh:
        return {"fire": False, "state": state, "note": f"keine neuen Einträge ({len(items)} insgesamt)"}
    return {
        "fire": True,
        "state": {"init": True, "keys": (list(state.get("keys") or []) + [i["key"] for i in fresh])[-MAX_KEYS:]},
        "note": f"{len(fresh)} neue Einträge",
        "context": f"Neue Einträge seit der letzten Prüfung ({len(fresh)}):\n" + "\n".join("- " + i["label"][:600] for i in fresh)[:4000],
    }
