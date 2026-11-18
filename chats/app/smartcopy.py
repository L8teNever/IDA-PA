"""Smart Copy: „Text kopieren“ im Nachrichten-Menü gedrückt halten → die Nachricht wird in alles zerlegt, was man einzeln kopieren könnte (Bestätigungscode,
E-Mail-Adresse, Telefonnummer, Adresse, Link, IBAN, Datum, Uhrzeit, Betrag, Bestell-/Sendungsnummer …), jedes mit einem Tipp kopierbar.
Zwei Wege: `local_items()` erkennt das Wichtigste ohne KI (sofort, auch wenn Haiku nicht erreichbar ist), Claude Haiku (über IDA, `IDA_URL`) versteht mehr
(Codes ohne Stichwort, Adressen, Sendungsnummern) und benennt es („Bestätigungscode von Epic Games“). Haikus Antwort zählt nur, wenn der Wert WÖRTLICH in der
Nachricht steht – was das Modell erfindet oder umformuliert, fliegt raus. Das Ergebnis ist die Vereinigung beider; ohne Haiku bleibt die einfache Erkennung."""
import hashlib
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .config import get_settings

log = logging.getLogger("chats.smartcopy")
router = APIRouter(prefix="/api/v1")

TYPES = ("code", "email", "phone", "address", "link", "iban", "date", "time", "amount", "number", "other")
LABEL = {"code": "Code", "email": "E-Mail", "phone": "Telefon", "address": "Adresse", "link": "Link", "iban": "IBAN", "date": "Datum", "time": "Uhrzeit",
         "amount": "Betrag", "number": "Nummer", "other": "Sonstiges"}
DIGITS = ("phone", "code", "iban", "number")   # Werte, bei denen nur die Ziffern/Zeichen zählen (Leerzeichen, Bindestriche dürfen anders stehen)
MAX_ITEMS = 20
MAX_VALUE = 300
CACHE_TTL = 3600
CACHE_MAX = 200
HAIKU_TIMEOUT = 60

# ---------------- einfache Erkennung (ohne KI) ----------------
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"(?:https?://|www\.)[^\s<>\"')\]]+", re.I)
PHONE = re.compile(
    r"(?<![\w@.])(?:\+|00)\d{1,3}[\s().\/-]*\d(?:[\s().\/-]?\d){5,13}(?!\d)"   # +49 151 12345678, 0049 …
    r"|(?<![\w@.+])0\d{1,5}[\s\/-]\d(?:[\s\/-]?\d){3,10}(?!\d)"                  # 0711 123456, 07151/123456
    r"|(?<![\w@.+])0\d{9,13}(?!\d)")                                              # 015112345678
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,3})?\b")
KEY = r"(?:\w*code|\w*kode|pin|tan|otp|kennwort|passwort|password|token|verifizierung\w*|bestätigung\w*|verification|confirmation)"
CODE_VAL = r"(?:[A-Z]-\d{4,8}|\d{3}[ -]\d{3}|\d{4,8})"
CODE_AFTER = re.compile(rf"\b{KEY}\b[^\d\n]{{0,30}}?(?<![\w-])({CODE_VAL})(?![\w])", re.I)
CODE_BEFORE = re.compile(rf"(?<![\w-])({CODE_VAL})(?![\w])[^\n\d]{{0,40}}?\b{KEY}\b", re.I)
STREET = re.compile(
    r"\b[A-ZÄÖÜ][\wäöüß.-]*(?:[Ss]traße|[Ss]trasse|[Ss]tr\.|[Ww]eg|[Pp]latz|[Aa]llee|[Gg]asse|[Rr]ing|[Dd]amm|[Uu]fer|[Cc]haussee|[Pp]fad)\s+\d{1,4}\s?[a-zA-Z]?\b"
    r"(?:,?\s+\d{5}\s+[A-ZÄÖÜ][\wäöüß-]+(?:\s[A-ZÄÖÜ][\wäöüß-]+)?)?")
MONTHS = r"(?:Jan(?:uar|\.)?|Feb(?:ruar|\.)?|März|Mär\.|Apr(?:il|\.)?|Mai|Juni?|Juli?|Aug(?:ust|\.)?|Sep(?:t(?:ember)?|\.)?|Okt(?:ober|\.)?|Nov(?:ember|\.)?|Dez(?:ember|\.)?)"
DATE = re.compile(rf"(?<![\d.])(?:\d{{1,2}}\.\d{{1,2}}\.(?:\d{{4}}|\d{{2}})?|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}\.\s?{MONTHS}(?:\s\d{{4}})?)(?![\d])")
TIME = re.compile(r"(?<![\d:.])(?:[01]?\d|2[0-3])(?::[0-5]\d(?::[0-5]\d)?(?:\s?Uhr)?|\s?Uhr)(?![\d:])")
AMOUNT = re.compile(r"(?:[€$£]\s?\d[\d.,]*|(?<![\w.,])\d[\d.]*(?:,\d{1,2})?\s?(?:€|EUR\b|Euro\b|\$|USD\b|CHF\b|£))")
GROUPED = re.compile(r"(?<![\w-])[A-Z]{0,2}\d{2,6}(?: [A-Z0-9]{2,6}){2,}(?![\w])")   # in Blöcken geschrieben (Kontonummer, Seriennummer): eine Zeile, nicht vier
NUMBER = re.compile(r"(?<![\w-])(?=[\w-]*\d)[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*(?![\w])")   # Bestell-, Rechnungs-, Sendungsnummern, Kennungen


def iban_ok(s: str) -> bool:
    t = re.sub(r"\s", "", s).upper()
    return 15 <= len(t) <= 34 and int("".join(str(int(c, 36)) for c in t[4:] + t[:4])) % 97 == 1


def local_items(text: str) -> list[dict]:
    """Alles Kopierbare ohne KI: [{type, label, value}] in der Reihenfolge von TYPES (Code, E-Mail, Telefon, Adresse, Link, IBAN, Datum, Uhrzeit, Betrag, Nummer).
    Jede Stelle im Text zählt nur einmal."""
    taken: list[tuple[int, int]] = []
    found: list[dict] = []

    def add(kind: str, m: re.Match, group: int = 0) -> None:
        a, b = m.span(group)
        if any(a < y and x < b for x, y in taken):
            return
        taken.append((a, b))
        found.append({"type": kind, "label": LABEL[kind], "value": m.group(group).strip().rstrip(".,;:!?)")})

    for m in EMAIL.finditer(text):
        add("email", m)
    for m in URL.finditer(text):
        if not any(m.start() >= a and m.end() <= b for a, b in taken):   # nicht der Teil nach dem @ einer E-Mail-Adresse
            add("link", m)
    for m in IBAN.finditer(text):
        if iban_ok(m.group()):
            add("iban", m)
    for rx in (CODE_AFTER, CODE_BEFORE):
        for m in rx.finditer(text):
            add("code", m, 1)
    for m in STREET.finditer(text):
        add("address", m)
    for kind, rx in (("date", DATE), ("time", TIME), ("amount", AMOUNT)):
        for m in rx.finditer(text):
            add(kind, m)
    for m in PHONE.finditer(text):
        if 7 <= len(re.sub(r"\D", "", m.group())) <= 15:
            add("phone", m)
    for m in GROUPED.finditer(text):
        if len(re.sub(r"\D", "", m.group())) >= 8:
            add("number", m)
    for m in NUMBER.finditer(text):   # was sonst noch nach Nummer aussieht: mind. 4 Ziffern, oder Buchstaben + Ziffern gemischt (AB12-9XY)
        v, digits = m.group(), len(re.sub(r"\D", "", m.group()))
        if digits >= 4 or (digits >= 2 and re.search(r"[A-Za-z]", v) and len(v) >= 5):
            add("number", m)
    return sorted(found, key=lambda x: TYPES.index(x["type"]))


# ---------------- Haiku ----------------
def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def alnum(s: str) -> str:
    return re.sub(r"[^\w]", "", s).lower()


def present(value: str, kind: str, text: str) -> bool:
    """Steht der Wert wörtlich in der Nachricht? Codes, Nummern und IBANs müssen als ganze Zahl dastehen (nicht als Stück einer längeren) – dort dürfen nur
    Leerzeichen, Bindestriche, Punkte, Schrägstriche oder Klammern anders stehen. Alles andere (Adresse, Name …) muss als Text enthalten sein."""
    if kind in DIGITS:
        chars = alnum(value)
        if len(chars) < 4:
            return False
        sep = r"[\s.\-/()]*"
        return bool(re.search(r"(?<![\w])" + sep.join(re.escape(c) for c in chars) + r"(?![\w])", text, re.I))
    return squash(value) in squash(text)


def parse_haiku(raw: str, text: str) -> list[dict] | None:
    """Antwort des Modells (JSON-Array) → geprüfte Einträge; None, wenn sie sich nicht lesen lässt."""
    raw = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    a, b = raw.find("["), raw.rfind("]")
    if a < 0 or b < a:
        return None
    try:
        arr = json.loads(raw[a:b + 1])
    except ValueError:
        return None
    if not isinstance(arr, list):
        return None
    out = []
    for x in arr:
        if not isinstance(x, dict) or not isinstance(x.get("value"), str):
            continue
        kind = x.get("type") if x.get("type") in TYPES else "other"
        value = x["value"].strip()
        if kind == "link":
            value = value.rstrip(".,;:!?)")
        label = x.get("label") if isinstance(x.get("label"), str) else ""
        if value and len(value) <= MAX_VALUE and present(value, kind, text):
            out.append({"type": kind, "label": label.strip()[:60] or LABEL[kind], "value": value})
    return out


def model_name(m: str | None) -> str:
    x = re.match(r"^claude-([a-z]+)-(\d+)(?:-(\d+))?$", m or "")
    return f"Claude {x[1].capitalize()} {x[2]}" + (f".{x[3]}" if x[3] else "") if x else (m or "Haiku")


_http: httpx.AsyncClient | None = None


async def ask_haiku(text: str) -> tuple[list[dict], str] | None:
    global _http
    url = get_settings().ida_url.rstrip("/")
    if not url:
        return None
    if _http is None:
        _http = httpx.AsyncClient(timeout=HAIKU_TIMEOUT)
    try:
        r = await _http.post(f"{url}/api/v1/smartcopy/haiku", json={"text": text})
        d = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return None
    if not d.get("ok"):
        return None
    items = parse_haiku(d.get("text") or "", text)
    return None if items is None else (items, model_name(d.get("model")))


def merge(first: list[dict], second: list[dict]) -> list[dict]:
    """Haikus Einträge zuerst (mit ihren Namen), dazu was die einfache Erkennung zusätzlich fand; gleiche Werte nur einmal; nach Art sortiert."""
    out: list[dict] = []
    seen: set[str] = set()
    for x in [*first, *second]:
        k = alnum(x["value"]) if x["type"] in DIGITS else squash(x["value"])
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return sorted(out, key=lambda x: TYPES.index(x["type"]))[:MAX_ITEMS]


_cache: dict[str, tuple[float, dict]] = {}


class SmartText(BaseModel):
    text: str = Field(max_length=4000)
    fast: bool = False   # nur die einfache Erkennung (sofort); sonst auch Haiku


@router.post("/smartcopy")
async def smartcopy(body: SmartText):
    """{items: [{type, label, value}], engine: "haiku" | "lokal", model}. Mit fast gibt es nur die einfache Erkennung – die Oberfläche zeigt sie sofort, bis Haiku fertig ist."""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Kein Text")
    local = local_items(text)
    if body.fast:
        return {"items": local[:MAX_ITEMS], "engine": "lokal", "model": ""}
    key = hashlib.sha1(text.encode()).hexdigest()
    if (hit := _cache.get(key)) and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    got = await ask_haiku(text)
    if got is None:   # Haiku nicht erreichbar oder am Limit: die einfache Erkennung genügt, und beim nächsten Mal wird neu gefragt
        return {"items": local[:MAX_ITEMS], "engine": "lokal", "model": ""}
    res = {"items": merge(got[0], local), "engine": "haiku", "model": got[1]}
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.time(), res)
    return res
