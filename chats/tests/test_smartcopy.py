"""Smart Copy: Nachricht in kopierbare Teile zerlegen (einfache Erkennung + Claude Haiku über IDA)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.smartcopy import TYPES

API = "/api/v1"
EPIC = "Epic Games: Dein Bestätigungscode ist 037428"


class FakeIda:
    """Ersatz für IDA: POST /api/v1/smartcopy/haiku → {ok, text, model}. `text` ist die rohe Modell-Antwort."""

    def __init__(self):
        self.calls: list[str] = []
        self.ok = True
        self.text = "[]"
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.calls.append(body["text"])
                out = json.dumps({"ok": outer.ok, "text": outer.text, "model": "claude-haiku-5-5"}).encode()
                self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


@pytest.fixture()
def ida(client, monkeypatch):
    from app import smartcopy
    from app.config import get_settings
    f = FakeIda()
    monkeypatch.setattr(get_settings(), "ida_url", f.url)
    smartcopy._cache.clear()
    return f


def post(client, text, fast=False):
    r = client.post(f"{API}/smartcopy", json={"text": text, "fast": fast})
    assert r.status_code == 200, r.text
    return r.json()


def pairs(d):
    return [(x["type"], x["value"]) for x in d["items"]]


@pytest.mark.parametrize("text,want", [
    (EPIC, [("code", "037428")]),
    ("Dein Code lautet 482913. Gültig für 10 Minuten.", [("code", "482913")]),
    ("123456 is your verification code. Don't share it.", [("code", "123456")]),
    ("Ihr Google-Bestätigungscode lautet G-123456", [("code", "G-123456")]),
    ("Mail an max.mustermann@web.de, Tel. +49 151 12345678", [("email", "max.mustermann@web.de"), ("phone", "+49 151 12345678")]),
    ("Ruf an: 0711 123456 oder 07151/987654", [("phone", "0711 123456"), ("phone", "07151/987654")]),
    ("Büro: Hauptstraße 12, 71364 Winnenden", [("address", "Hauptstraße 12, 71364 Winnenden")]),
    ("Überweise an DE89 3704 0044 0532 0130 00 bitte", [("iban", "DE89 3704 0044 0532 0130 00")]),
    ("Siehe https://example.com/pay?id=7, danke", [("link", "https://example.com/pay?id=7")]),
    # alles, was man kopieren könnte: Datum, Uhrzeit, Betrag, Rechnungs-/Sendungsnummer, Nummer in Blöcken (falsche IBAN bleibt eine Nummer)
    ("Treffen am 01.12.2026 um 15:30, Rechnung 2026-1234, Betrag 19,99 €",
     [("date", "01.12.2026"), ("time", "15:30"), ("amount", "19,99 €"), ("number", "2026-1234")]),
    ("Am 3. Dezember 2026 um 18 Uhr, kostet 12 Euro, Bestellnr. 302-4419822-1170710",
     [("date", "3. Dezember 2026"), ("time", "18 Uhr"), ("amount", "12 Euro"), ("number", "302-4419822-1170710")]),
    ("Hi wie geht's? Wir sehen uns um 20:15 im Kino", [("time", "20:15")]),
    ("Sendungsnummer 00340434161094042557 wurde zugestellt", [("number", "00340434161094042557")]),
    ("Konto DE00 1234 5678 9012 3456 78", [("number", "DE00 1234 5678 9012 3456 78")]),
    # kleine Zahlen im Fließtext sind nichts zum Kopieren
    ("Ich hab 3 Katzen und 10 Minuten Zeit", []),
])
def test_einfache_erkennung(client, text, want):
    assert pairs(post(client, text, fast=True)) == want


def test_sofortige_erkennung_fragt_haiku_nicht(client, ida):
    d = post(client, EPIC, fast=True)
    assert d == {"items": [{"type": "code", "label": "Code", "value": "037428"}], "engine": "lokal", "model": ""}
    assert ida.calls == []


def test_haiku_benennt_ergaenzt_und_erfindet_nichts(client, ida):
    text = "Hallo Max, dein Paket kommt morgen. Fragen? max@firma.de oder 0711 123456. Abholcode: AB12-9XY, Lager Gartenweg 4, 70173 Stuttgart"
    ida.text = ("```json\n" + json.dumps([
        {"type": "code", "label": "Abholcode", "value": "AB12-9XY"},
        {"type": "address", "label": "Lager", "value": "Gartenweg 4, 70173 Stuttgart"},
        {"type": "phone", "label": "Hotline", "value": "0711 123 456"},     # Leerzeichen anders: zählt (gleiche Ziffern)
        {"type": "phone", "label": "Erfunden", "value": "0711 999999"},    # steht nicht in der Nachricht: fliegt raus
        {"type": "email", "label": "Absender", "value": "chef@firma.de"},  # dito
        {"type": "bogus", "label": "Seltsam", "value": "Gartenweg"},       # unbekannte Art → Sonstiges
        {"type": "code", "value": 5},                                       # kaputt
    ]) + "\n```")
    d = post(client, text)
    assert d["engine"] == "haiku" and d["model"] == "Claude Haiku 5.5"
    got = {(x["type"], x["value"]): x["label"] for x in d["items"]}
    assert got[("code", "AB12-9XY")] == "Abholcode" and got[("address", "Gartenweg 4, 70173 Stuttgart")] == "Lager"
    assert got[("phone", "0711 123 456")] == "Hotline"
    assert not any(v in ("0711 999999", "chef@firma.de") for _, v in got)
    assert got[("other", "Gartenweg")] == "Seltsam"
    assert got[("email", "max@firma.de")] == "E-Mail"          # fand nur die einfache Erkennung – kommt dazu
    assert ("phone", "0711 123456") not in got                  # dieselbe Nummer wie Haikus „0711 123 456“: nur einmal
    assert [x["type"] for x in d["items"]] == sorted((x["type"] for x in d["items"]), key=list(TYPES).index)
    assert ida.calls == [text]


def test_haiku_ohne_fund_laesst_einfache_erkennung_stehen(client, ida):
    ida.text = "[]"
    d = post(client, EPIC)
    assert d["engine"] == "haiku" and pairs(d) == [("code", "037428")]


@pytest.mark.parametrize("ok,text", [(False, ""), (True, "Hier sind die Werte: nichts"), (True, "{\"a\": 1}"), (True, "[1, 2")])
def test_haiku_kaputt_oder_am_limit_einfache_erkennung(client, ida, ok, text):
    ida.ok, ida.text = ok, text
    d = post(client, EPIC)
    assert d["engine"] == "lokal" and pairs(d) == [("code", "037428")]


def test_ida_nicht_erreichbar_oder_nicht_eingerichtet(client, ida, monkeypatch):
    from app import smartcopy
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "ida_url", "http://127.0.0.1:9")   # niemand zu Hause
    assert post(client, EPIC)["engine"] == "lokal"
    monkeypatch.setattr(get_settings(), "ida_url", "")
    smartcopy._cache.clear()
    assert post(client, EPIC)["engine"] == "lokal"


def test_ergebnis_wird_gemerkt_ein_fehler_nicht(client, ida):
    ida.ok = False
    assert post(client, EPIC)["engine"] == "lokal"
    ida.ok, ida.text = True, json.dumps([{"type": "code", "label": "Bestätigungscode von Epic Games", "value": "037428"}])
    d = post(client, EPIC)   # der Fehler wurde nicht gemerkt: es wird neu gefragt
    assert d["engine"] == "haiku" and d["items"][0]["label"] == "Bestätigungscode von Epic Games"
    n = len(ida.calls)
    assert post(client, EPIC) == d and len(ida.calls) == n   # jetzt aus dem Speicher


def test_eingabe_pruefen(client):
    assert client.post(f"{API}/smartcopy", json={"text": "   "}).status_code == 400
    assert client.post(f"{API}/smartcopy", json={"text": "x" * 4001}).status_code == 422


def test_wortlaut_pruefung():
    from app.smartcopy import model_name, present
    assert present("0711 123456", "phone", "ruf 0711-123456 an") and present("g-123456", "code", "Code G-123456")
    assert not present("0711 999999", "phone", "ruf 0711 123456 an") and not present("x@y.de", "email", "mail an a@b.de")
    assert not present("12", "code", "Code 123456") and not present("3456", "code", "Code 123456") and not present("1234", "phone", "Tel 991234")   # nur ganze Zahlen, keine Stücke
    assert present("0711 123 456", "phone", "ruf 0711 123456 an") and present("+49 151 12345678", "phone", "Tel: +49 151 12345678.")
    assert present("Gartenweg 4", "address", "Lager am gartenweg  4, Stuttgart")   # Adresse: als Text enthalten, Groß-/Kleinschreibung und Leerraum egal
    assert model_name("claude-haiku-5-5") == "Claude Haiku 5.5" and model_name("claude-sonnet-5") == "Claude Sonnet 5" and model_name("x") == "x"
