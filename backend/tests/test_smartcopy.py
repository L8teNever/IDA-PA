"""Smart Copy: der interne Endpunkt, den der Dienst "chats" fragt (Claude Haiku zerlegt eine Nachricht in kopierbare Teile)."""
import pytest


@pytest.fixture()
def login(monkeypatch):
    """Anmeldung und Limit nachgestellt (der Server läuft im selben Prozess): kein gemeinsamer Zustand, nichts aufzuräumen, keine Abfrage bei Anthropic."""
    from app.services import providers
    state = {"angemeldet": True}

    async def frei(acc):
        return False
    monkeypatch.setattr(providers, "logged_in", lambda acc: state["angemeldet"])
    monkeypatch.setattr(providers, "exhausted", frei)
    return state


def ask(client, text):
    r = client.post("/api/v1/smartcopy/haiku", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_haiku_bekommt_die_nachricht_und_den_auftrag(client, login):
    d = ask(client, "Epic Games: Dein Bestätigungscode ist 037428 SHOWSYS")
    assert d["ok"] and d["model"] == "claude-haiku-5-5" and d["account"]
    assert "<nachricht>\nEpic Games: Dein Bestätigungscode ist 037428" in d["text"]   # der Ersatz gibt den Prompt zurück
    assert "SYSTEM:" in d["text"] and "JSON-Array" in d["text"] and "WÖRTLICH" in d["text"]   # der Auftrag: nur Wörtliches, als JSON


def test_leer_und_zu_lang(client, login):
    assert ask(client, "   ") == {"ok": False}
    assert client.post("/api/v1/smartcopy/haiku", json={"text": "x" * 4001}).status_code == 422


def test_limit_naechster_account(client, login):
    zweit = client.post("/api/v1/accounts", json={"name": "SmartZweit"}).json()["id"]
    erster = client.get("/api/v1/accounts").json()[0]["name"]
    try:
        d = ask(client, "Code 123456 LIMIT")   # der erste Account meldet das Limit, ein späterer antwortet (andere Tests lassen Accounts zurück)
        assert d["ok"] and d["account"] and d["account"] != erster
    finally:
        client.delete(f"/api/v1/accounts/{zweit}")


def test_ohne_angemeldeten_account_kein_ergebnis(client, login):
    login["angemeldet"] = False
    assert ask(client, "Code 123456") == {"ok": False}   # dann nimmt chats die einfache Erkennung
