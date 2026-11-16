"""Ordnung in der Chat-Liste – nur in IDA Chats, Beeper bleibt unberührt, gilt auf allen Geräten.

- Sichtbarkeit pro Chat: "show" = immer auf der Startseite (auch wenn sein Netzwerk ausgeblendet ist), "new" = nur solange
  ungelesene Nachrichten da sind, "hidden" = nie (dann nur über den Filter „Ausgeblendet“ und die Suche).
- Sichtbarkeit pro Netzwerk (z. B. alle Snapchat-Chats): "new" oder "hidden". Zusammengeführte Chats zählen nicht dazu – sie
  bleiben auf der Startseite, und man kann darin weiter über das ausgeblendete Netzwerk schreiben (das entscheidet die Seite).
- Eigene Listen (z. B. „Tagesmutter“) als Filter. Titel und Netzwerk eines Chats werden mitgemerkt, damit die Liste ihn auch
  zeigen kann, wenn er weit unten in Beeper steht und noch nicht geladen ist.
Benachrichtigungen ändert nichts davon – dafür gibt es Stummschalten.
"""
import re
import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import store

router = APIRouter(prefix="/api/v1")
CHAT_VIS = ("show", "new", "hidden")
NET_VIS = ("new", "hidden")
MAX_LABELS = 30
NET = re.compile(r"^[a-z]{2,20}$")


def _db():
    d = store.db()
    d.execute("CREATE TABLE IF NOT EXISTS chat_vis (chat TEXT PRIMARY KEY, mode TEXT, title TEXT, network TEXT)")
    d.execute("CREATE TABLE IF NOT EXISTS net_vis (net TEXT PRIMARY KEY, mode TEXT)")
    d.execute("CREATE TABLE IF NOT EXISTS labels (id TEXT PRIMARY KEY, name TEXT, pos INTEGER)")
    d.execute("CREATE TABLE IF NOT EXISTS label_chats (label TEXT, chat TEXT, title TEXT, network TEXT, PRIMARY KEY (label, chat))")
    return d


def _check(chat_id: str) -> None:
    if not re.match(r"^[\w!:.@=+$-]{1,300}$", chat_id or ""):
        raise HTTPException(400, "Ungültige ID")


def state() -> dict:
    with store._lock:
        d = _db()
        chats = [{"id": c, "mode": m, "title": t or "", "network": n or ""} for c, m, t, n in d.execute("SELECT chat, mode, title, network FROM chat_vis")]
        nets = dict(d.execute("SELECT net, mode FROM net_vis").fetchall())
        labels = [{"id": i, "name": n, "chats": []} for i, n in d.execute("SELECT id, name FROM labels ORDER BY pos, name")]
        by = {lb["id"]: lb for lb in labels}
        for lid, c, t, n in d.execute("SELECT label, chat, title, network FROM label_chats"):
            if lid in by:
                by[lid]["chats"].append({"id": c, "title": t or "", "network": n or ""})
    return {"chats": chats, "nets": nets, "labels": labels}


class Vis(BaseModel):
    mode: str = ""
    title: str = Field("", max_length=200)
    network: str = Field("", max_length=60)


@router.get("/org")
async def get_org():
    return state()


@router.put("/chats/{chat_id}/vis")
async def put_chat_vis(chat_id: str, body: Vis):
    _check(chat_id)
    if body.mode and body.mode not in CHAT_VIS:
        raise HTTPException(400, "Unbekannte Einstellung")
    with store._lock:
        d = _db()
        if body.mode:
            d.execute("INSERT OR REPLACE INTO chat_vis (chat, mode, title, network) VALUES (?,?,?,?)", (chat_id, body.mode, body.title, body.network))
        else:
            d.execute("DELETE FROM chat_vis WHERE chat=?", (chat_id,))
        d.commit()
    return state()


@router.put("/nets/{net}/vis")
async def put_net_vis(net: str, body: Vis):
    if not NET.match(net):
        raise HTTPException(400, "Unbekanntes Netzwerk")
    if body.mode and body.mode not in NET_VIS:
        raise HTTPException(400, "Unbekannte Einstellung")
    with store._lock:
        d = _db()
        if body.mode:
            d.execute("INSERT OR REPLACE INTO net_vis (net, mode) VALUES (?,?)", (net, body.mode))
        else:
            d.execute("DELETE FROM net_vis WHERE net=?", (net,))
        d.commit()
    return state()


class LabelIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def _name(n: str) -> str:
    n = re.sub(r"\s+", " ", n).strip()
    if not n:
        raise HTTPException(400, "Name fehlt")
    return n


@router.post("/labels")
async def add_label(body: LabelIn):
    name = _name(body.name)
    with store._lock:
        d = _db()
        if d.execute("SELECT COUNT(*) FROM labels").fetchone()[0] >= MAX_LABELS:
            raise HTTPException(400, f"Höchstens {MAX_LABELS} Listen")
        if d.execute("SELECT 1 FROM labels WHERE lower(name)=lower(?)", (name,)).fetchone():
            raise HTTPException(409, "Diese Liste gibt es schon")
        lid = secrets.token_hex(6)
        pos = d.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM labels").fetchone()[0]
        d.execute("INSERT INTO labels (id, name, pos) VALUES (?,?,?)", (lid, name, pos))
        d.commit()
    return {**state(), "created": lid}


def _label(d, lid: str) -> None:
    if not re.match(r"^[0-9a-f]{12}$", lid) or not d.execute("SELECT 1 FROM labels WHERE id=?", (lid,)).fetchone():
        raise HTTPException(404, "Liste nicht gefunden")


@router.put("/labels/{lid}")
async def rename_label(lid: str, body: LabelIn):
    name = _name(body.name)
    with store._lock:
        d = _db()
        _label(d, lid)
        if d.execute("SELECT 1 FROM labels WHERE lower(name)=lower(?) AND id<>?", (name, lid)).fetchone():
            raise HTTPException(409, "Diese Liste gibt es schon")
        d.execute("UPDATE labels SET name=? WHERE id=?", (name, lid))
        d.commit()
    return state()


@router.delete("/labels/{lid}")
async def del_label(lid: str):
    with store._lock:
        d = _db()
        _label(d, lid)
        d.execute("DELETE FROM labels WHERE id=?", (lid,))
        d.execute("DELETE FROM label_chats WHERE label=?", (lid,))
        d.commit()
    return state()


class LabelChat(BaseModel):
    on: bool
    title: str = Field("", max_length=200)
    network: str = Field("", max_length=60)


@router.put("/labels/{lid}/chats/{chat_id}")
async def label_chat(lid: str, chat_id: str, body: LabelChat):
    _check(chat_id)
    with store._lock:
        d = _db()
        _label(d, lid)
        if body.on:
            d.execute("INSERT OR REPLACE INTO label_chats (label, chat, title, network) VALUES (?,?,?,?)", (lid, chat_id, body.title, body.network))
        else:
            d.execute("DELETE FROM label_chats WHERE label=? AND chat=?", (lid, chat_id))
        d.commit()
    return state()


# ---------------- Vorschläge zum Zusammenführen ----------------
# Dieselbe Person in mehreren Apps (z. B. „Luka“ in WhatsApp und „luka.m“ … bzw. „Luka Meier“ in Instagram): gleicher oder sehr
# ähnlicher Name, gleiche Art (Einzel-/Gruppenchat), verschiedene Netzwerke – pro Netzwerk höchstens ein Chat je Vorschlag.
# Chats, die schon zusammengeführt sind, und Paare, die du abgelehnt hast, kommen nicht mehr. Entscheiden tust immer du.
NET_KEYS = (("whatsapp", "whatsapp"), ("instagram", "instagram"), ("snapchat", "snapchat"), ("telegram", "telegram"), ("signal", "signal"),
            ("discord", "discord"), ("messenger", "messenger"), ("facebook", "messenger"), ("gmessages", "gmessages"), ("googlechat", "googlechat"),
            ("slack", "slack"), ("linkedin", "linkedin"), ("imessage", "imessage"), ("twitter", "x"))
SKIP_NETS = {"", "matrix", "beeper", "hungryserv"}
MAX_SUGG = 20


def net_key(n: str) -> str:
    n = (n or "").lower()
    return next((v for k, v in NET_KEYS if k in n), n)


def norm_name(t: str) -> str:
    """„꧁☬Moro☬꧂“ → „moro“, „Lúka  Meier (Arbeit)“ → „luka meier“: nur Buchstaben/Ziffern, ohne Akzente, klein."""
    import unicodedata
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t or "")
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s]|_", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _nope_db():
    d = store.db()
    d.execute("CREATE TABLE IF NOT EXISTS merge_nope (a TEXT, b TEXT, PRIMARY KEY (a, b))")
    return d


def nopes() -> set[tuple[str, str]]:
    with store._lock:
        return {(a, b) for a, b in _nope_db().execute("SELECT a, b FROM merge_nope")}


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _score(x: dict, y: dict) -> tuple[float, str]:
    import difflib
    a, b = x["norm"], y["norm"]
    if len(a) < 2 or len(b) < 2:
        return 0, ""
    if a == b:
        return 1.0, "Gleicher Name"
    if a.replace(" ", "") == b.replace(" ", ""):
        return 0.95, "Gleicher Name"
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if r >= 0.86 and min(len(a), len(b)) >= 4:
        return r * 0.9, "Ähnlicher Name"
    ta, tb = a.split(), b.split()
    # „Luka“ und „Luka Meier“: Vorname gleich, einer hat nur den Vornamen (nur Einzelchats – bei Gruppen zu ungenau)
    if x["type"] != "group" and ta[0] == tb[0] and len(ta[0]) >= 3 and (len(ta) == 1 or len(tb) == 1):
        return 0.7, "Gleicher Vorname"
    return 0, ""


def suggest(chats: list[dict], grouped: set[str], nope: set[tuple[str, str]]) -> list[dict]:
    """chats: [{id, title, network, type}] → [{id, chats: [...], reason}] – stärkste Treffer zuerst."""
    items = []
    for c in chats:
        net = net_key(c.get("network", ""))
        if not c.get("id") or c["id"] in grouped or net in SKIP_NETS:
            continue
        items.append({"id": c["id"], "title": (c.get("title") or "").strip(), "network": net, "type": c.get("type") or "single", "norm": norm_name(c.get("title") or "")})
    pairs = []
    for i, x in enumerate(items):
        for y in items[i + 1:]:
            if x["network"] == y["network"] or x["type"] != y["type"] or _pair(x["id"], y["id"]) in nope:
                continue
            s, why = _score(x, y)
            if s:
                pairs.append((s, why, x, y))
    pairs.sort(key=lambda p: -p[0])
    where: dict[str, dict] = {}
    groups: list[dict] = []
    for s, why, x, y in pairs:
        gx, gy = where.get(x["id"]), where.get(y["id"])
        if gx and gy:
            continue
        if not gx and not gy:
            g = {"chats": [x, y], "score": s, "reason": why}
            groups.append(g)
            where[x["id"]] = where[y["id"]] = g
            continue
        g, new = (gx, y) if gx else (gy, x)
        # dazu nur, wenn das Netzwerk noch fehlt und der Neue zu allen passt (und keiner davon abgelehnt ist)
        if any(m["network"] == new["network"] for m in g["chats"]) or len(g["chats"]) >= 6:
            continue
        if all(_score(m, new)[0] and _pair(m["id"], new["id"]) not in nope for m in g["chats"]):
            g["chats"].append(new)
            where[new["id"]] = g
    groups.sort(key=lambda g: (-g["score"], -len(g["chats"])))
    return [{"id": "|".join(sorted(m["id"] for m in g["chats"])), "reason": g["reason"],
             "chats": [{k: m[k] for k in ("id", "title", "network", "type")} for m in g["chats"]]} for g in groups[:MAX_SUGG]]


@router.get("/merges/suggestions")
async def merge_suggestions():
    from . import api
    raw = await api.rest_chats()
    chats = [{"id": str(c.get("localChatID") or c["id"]), "title": c.get("title") or "", "network": c.get("accountID") or c.get("network") or "",
              "type": c.get("type") or ""} for c in raw if c.get("id") and not c.get("isArchived")]
    grouped = {m["id"] for g in store.groups().values() for m in g}
    return {"items": suggest(chats, grouped, nopes())}


class Nope(BaseModel):
    chats: list[str] = Field(min_length=2, max_length=10)


@router.post("/merges/suggestions/dismiss")
async def dismiss_suggestion(body: Nope):
    """Vorschlag ablehnen: diese Chats nie wieder miteinander vorschlagen."""
    for c in body.chats:
        _check(c)
    ids = sorted(set(body.chats))
    with store._lock:
        _nope_db().executemany("INSERT OR IGNORE INTO merge_nope (a, b) VALUES (?, ?)", [_pair(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]])
        store.db().commit()
    return {"ok": True}
