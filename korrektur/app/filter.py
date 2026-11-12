"""Sicherheitsfilter hinter dem Modell: übernommen werden nur echte Tippfehler-Korrekturen.

Das Modell schreibt gern um, wiederholt Sätze oder lässt Teile weg. Darum wird seine Ausgabe Wort für Wort gegen das
Original gelegt: ein Wort wird nur ersetzt, wenn das neue dem alten ähnlich genug ist ("eknn" → "kann", "Diener" → "deiner").
Eingefügte oder weggelassene Wörter, Wiederholungen, Links, Zahlen, @Namen, Abkürzungen und Emojis bleiben wie im Original.
Groß-/Kleinschreibung und Kommas des Modells sind erlaubt; das Satzzeichen am Ende der Nachricht bleibt immer deins.
"""
import re
from difflib import SequenceMatcher

WORD = re.compile(r"\S+")
EDGE = re.compile(r"^\W+|\W+$")
END = re.compile(r"[.!?…]+$")
MAX_JOIN = 3   # so viele Wörter dürfen höchstens zusammengezogen oder getrennt werden ("zusammen gehören" → "zusammengehören")


def core(tok: str) -> str:
    """Das Wort ohne Satzzeichen drumherum, klein geschrieben – so wird verglichen."""
    return EDGE.sub("", tok).lower()


def protected(tok: str) -> bool:
    """Wörter, die das Modell nicht anfassen darf."""
    c = EDGE.sub("", tok)
    if not any(ch.isalpha() for ch in c):                        # Emojis, Zahlen, nur Satzzeichen
        return True
    if "://" in tok or tok.lower().startswith("www.") or "/" in c or "@" in tok or tok.startswith("#"):
        return True
    if any(ch.isdigit() for ch in c):
        return True
    return len(c) >= 2 and c.isupper()                            # Abkürzungen wie IDA, MCP


def similar(a: str, b: str) -> bool:
    """Ist b eine plausible Tippfehler-Korrektur von a? (beide schon klein und ohne Satzzeichen)"""
    if a == b:
        return True
    if not a or not b:
        return False
    if abs(len(a) - len(b)) > max(2, len(a) // 2):
        return False
    if a.startswith(b):          # nur abgeschnitten ("reel" → "ree") ist keine Korrektur
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6


LEAD, TAIL = re.compile(r"^\W*"), re.compile(r"\W*$")
STOPS = ".!?…"


def tidy(orig: str, new: str, start: bool) -> str:
    """Satzzeichen und Großschreibung im Zaum halten: kein neuer Punkt mitten im Text (das Modell fängt sonst neue Sätze an),
    der erste Buchstabe folgt nur am Satzanfang dem Modell, sonst bleibt er wie getippt."""
    lead, tail = LEAD.match(new).group(0), TAIL.search(new).group(0)
    body = new[len(lead):len(new) - len(tail)] if tail else new[len(lead):]
    otail = TAIL.search(orig).group(0)
    tail = "".join(ch for ch in tail if ch not in STOPS or ch in otail)
    ob = EDGE.sub("", orig)
    if body and ob and not start:
        body = (body[0].upper() if ob[0].isupper() else body[0].lower()) + body[1:]
    return lead + body + tail


def keep_end(orig: str, new: str) -> str:
    """Das Satzzeichen am Ende der Nachricht bleibt wie getippt (kein angehängter Punkt im Chat)."""
    o, n = END.search(orig), END.search(new)
    stem = new[: n.start()] if n else new
    return stem + (o.group(0) if o else "")


def merge(original: str, model: str) -> dict:
    """Legt die Modell-Ausgabe auf das Original. Ergebnis: Text, Teile (neu = vom Modell geändert) und ob sich ein Wort geändert hat."""
    toks = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(original)]
    new = [m.group(0) for m in WORD.finditer(model)]
    a, b = [core(t[2]) for t in toks], [core(t) for t in new]
    edits: list[tuple[int, int, str]] = []   # (von Original-Wort, bis, ersetzt durch)

    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                o, n = toks[i1 + k][2], new[j1 + k]
                if n != o and not protected(o):
                    edits.append((i1 + k, i1 + k + 1, n))
        elif op == "replace":
            if i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    o, n = toks[i1 + k][2], new[j1 + k]
                    if not protected(o) and similar(a[i1 + k], b[j1 + k]):
                        edits.append((i1 + k, i1 + k + 1, n))
            elif max(i2 - i1, j2 - j1) <= MAX_JOIN and not any(protected(t[2]) for t in toks[i1:i2]):
                if similar("".join(a[i1:i2]), "".join(b[j1:j2])):
                    edits.append((i1, i2, " ".join(new[j1:j2])))
        # insert (vom Modell dazuerfunden) und delete (vom Modell weggelassen): Original bleibt

    def start(i: int) -> bool:
        if i == 0:
            return True
        return toks[i - 1][2][-1] in STOPS or "\n" in original[toks[i - 1][1]:toks[i][0]]

    edits = [(i1, i2, " ".join(tidy(toks[i1 + x if x < i2 - i1 else i2 - 1][2], w, start(i1) and x == 0)
                               for x, w in enumerate(n.split()))) for i1, i2, n in edits]

    if toks:   # Ende der Nachricht: dein Satzzeichen
        last = len(toks) - 1
        for x, (i1, i2, n) in enumerate(edits):
            if i2 - 1 == last:
                edits[x] = (i1, i2, keep_end(toks[last][2], n))

    parts: list[dict] = []
    changed, pos, k = False, 0, 0
    by_start = {e[0]: e for e in edits}

    def add(t: str, neu: bool):
        if parts and parts[-1]["neu"] == neu:
            parts[-1]["t"] += t
        elif t:
            parts.append({"t": t, "neu": neu})

    while k < len(toks):
        s = toks[k][0]
        add(original[pos:s], False)
        e = by_start.get(k)
        if e and e[2] != original[s:toks[e[1] - 1][1]]:
            i1, i2, n = e
            add(n, True)
            if "".join(a[i1:i2]) != "".join(core(w) for w in n.split()):
                changed = True
            pos, k = toks[i2 - 1][1], i2
        else:
            add(toks[k][2], False)
            pos, k = toks[k][1], k + 1
    add(original[pos:], False)
    return {"text": "".join(p["t"] for p in parts), "parts": parts, "changed": changed}
