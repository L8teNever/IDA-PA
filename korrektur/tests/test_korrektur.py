"""Filter und API des Korrektur-Dienstes – ohne echtes Modell (das ist zu groß für die Tests)."""
from fastapi.testclient import TestClient

from app import main, model
from app.filter import merge


def test_tippfehler_werden_korrigiert():
    r = merge("oder eknn man das machen", "Oder kann man das machen.")
    assert r["text"] == "Oder kann man das machen"   # Großschreibung am Anfang ja, Punkt am Ende nein
    assert r["changed"]
    assert {"t": "kann", "neu": True} in r["parts"]


def test_wiederholung_und_umbau_bleiben_draussen():
    r = merge("Der docker soll laufen", "Der Docker soll laufen. Der Docker soll laufen. Der")
    assert r["text"] == "Der docker soll laufen"
    assert not r["changed"]


def test_weggelassenes_und_erfundenes_bleibt_original():
    assert merge("also ja geoscheuebugn und so wäre gut", "So wäre gut.")["text"] == "also ja geoscheuebugn und so wäre gut"
    assert merge("ich nach hause", "ich gehe nach Hause")["text"] == "ich nach hause"


def test_kein_punkt_und_keine_grossschreibung_mitten_im_satz():
    r = merge("anschauen wäre es Diener Meinung", "anschauen. Wäre es deiner Meinung")
    assert r["text"] == "anschauen wäre es Deiner Meinung"


def test_kommas_sind_erlaubt():
    assert merge("ja oke mach ma", "Ja, oke, mach ma!")["text"] == "Ja, oke, mach ma"


def test_geschuetztes_bleibt():
    r = merge("schau https://insta.com/x 😂 IDA 12uhr @luka", "schau https://instagram.com/x IDE 12 Uhr luka")
    assert r["text"] == "schau https://insta.com/x 😂 IDA 12uhr @luka"


def test_zusammenziehen_und_abschneiden():
    assert merge("das gehört zusammen gehören", "das gehört zusammengehören")["text"] == "das gehört zusammengehören"
    assert merge("den reel von ihm", "den ree von ihm")["text"] == "den reel von ihm"


def test_zeilen_und_leerzeichen_bleiben():
    r = merge("hallo  du\nwie gehst", "Hallo du\nWie gehts")
    assert r["text"] == "Hallo  du\nWie gehts"
    assert "".join(p["t"] for p in r["parts"]) == r["text"]


def test_stuecke():
    assert model.chunks("a b c. d e f g", 2) == ["a b", "c.", "d e", "f g"]


def test_api(monkeypatch):
    c = TestClient(main.create_app(autoload=False))
    assert c.get("/api/v1/health").json()["ok"]
    monkeypatch.setitem(model._state, "status", "lädt")
    assert c.post("/api/v1/korrigieren", json={"text": "eknn"}).status_code == 503
    assert c.post("/api/v1/korrigieren", json={"text": "  "}).json()["changed"] is False
    monkeypatch.setitem(model._state, "status", "bereit")
    monkeypatch.setattr(model, "fix", lambda t: "kann man")
    model._cache.clear()
    assert c.post("/api/v1/korrigieren", json={"text": "eknn man"}).json()["text"] == "kann man"
