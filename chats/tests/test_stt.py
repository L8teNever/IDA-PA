"""Sprachnachrichten als Text: Beeper oder lokal (Whisper), gesichert in chats.db, automatisch umwandeln."""
import time
from datetime import datetime, timezone

from .conftest import MSGS

API = "/api/v1"


def transcribed(fake) -> list[str]:
    return [a["srcURL"] for n, a in fake.calls if n == "transcribe_voice_message"]


def restart_db():
    """Wie ein Neustart des Containers: die Verbindung zu chats.db zu, die Datei im Volume bleibt."""
    from app import store
    with store._lock:
        store._db.close()
        store._db = None


def voice_msg(mid: str, src: str, mine: bool = False, at: str | None = None, transcript: str = "") -> dict:
    att = {"srcURL": src, "type": "audio", "mimeType": "audio/ogg", "isVoiceNote": True, "duration": 5}
    if transcript:
        att["transcription"] = {"transcription": transcript}
    return {"id": mid, "timestamp": at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "senderID": "@me:x" if mine else "@luka:x",
            "senderName": "me" if mine else "Luka", "type": "VOICE", "text": "", "isSender": mine, "attachments": [att]}


def set_pref(client, **kw):
    r = client.put(f"{API}/prefs", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def test_umwandlung_bleibt_in_der_datenbank(client, fake):
    from app import store
    assert client.get(f"{API}/transcript", params={"src": "mxc://x/voice3?encryptedFileInfoJSON=abc"}).json() == {"text": "Hallo Welt"}
    assert store.transcripts(["mxc://x/voice3"]) == {"mxc://x/voice3": "Hallo Welt"}
    restart_db()   # Neustart: der Text ist noch da und Beeper wird nicht noch einmal gefragt
    n = len(transcribed(fake))
    # dieselbe Datei mit anderen Verschlüsselungsangaben ist dieselbe Sprachnachricht
    assert client.get(f"{API}/transcript", params={"src": "mxc://x/voice3?encryptedFileInfoJSON=zzz"}).json() == {"text": "Hallo Welt"}
    assert len(transcribed(fake)) == n
    assert client.get(f"{API}/transcript", params={"src": "untis:x:y"}).status_code == 400
    assert client.get(f"{API}/transcript", params={"src": "http://böse"}).status_code == 400


def test_gespeicherter_text_steht_in_den_nachrichten(client, fake):
    from app import store
    store.save_transcript("mxc://x/voice4", "Aus der Datenbank", "lokal", "de")
    store.save_transcript("mxc://x/voice4stille", "", "lokal")
    fake.msgs = {**MSGS, "items": [voice_msg("20", "mxc://x/voice4?encryptedFileInfoJSON=q"), voice_msg("21", "mxc://x/voice4stille?x=1"), *MSGS["items"]]}
    try:
        items = client.get(f"{API}/chats/1/messages").json()["items"]
    finally:
        fake.msgs = MSGS
    by = {m["id"]: m["files"][0] for m in items if m["files"] and m["files"][0]["kind"] == "audio"}
    assert by["20"]["transcript"] == "Aus der Datenbank" and by["21"]["transcript"] == ""
    # Beepers eigener Text wird dabei gesichert (Beeper schaltet die Umwandlung bald ab)
    assert by["9"]["transcript"] == "Hallo Mama"
    assert store.transcripts(["mxc://x/voice"]) == {"mxc://x/voice": "Hallo Mama"}


def test_beeper_text_ersetzt_nur_leeren(client):
    from app import store
    store.save_transcript("mxc://x/voice7", "", "lokal")
    store.save_transcript("mxc://x/voice7", "Später nachgeliefert", "beeper")
    store.save_transcript("mxc://x/voice7", "Anderer Text", "lokal")
    assert store.transcripts(["mxc://x/voice7"]) == {"mxc://x/voice7": "Später nachgeliefert"}


def test_lokal_mit_whisper(client, fake, whisper):
    from app import store
    assert set_pref(client, stt_local=True)["stt_local"] is True
    try:
        r = client.get(f"{API}/transcript", params={"src": "mxc://x/voice5?encryptedFileInfoJSON=abc", "mime": "audio/ogg"})
        assert r.json() == {"text": "Hallo aus Whisper"}
        assert whisper.calls[-1] == {"path": "/api/v1/transkribieren", "size": 200, "ct": "audio/ogg"}   # die Audiodatei selbst, roh
        assert "mxc://x/voice5?encryptedFileInfoJSON=abc" not in transcribed(fake)   # Beeper wandelt nicht um
        assert store.transcripts(["mxc://x/voice5"]) == {"mxc://x/voice5": "Hallo aus Whisper"}
        n = len(whisper.calls)
        restart_db()
        assert client.get(f"{API}/transcript", params={"src": "mxc://x/voice5"}).json() == {"text": "Hallo aus Whisper"}
        assert len(whisper.calls) == n   # aus der Datenbank

        # Modell lädt noch (503): Fehlermeldung, nichts gespeichert – später klappt es
        whisper.status = 503
        r = client.get(f"{API}/transcript", params={"src": "mxc://x/voice6"})
        assert r.status_code == 503 and "lädt" in r.json()["detail"]
        assert store.transcripts(["mxc://x/voice6"]) == {}
        whisper.status = 200
        assert client.get(f"{API}/transcript", params={"src": "mxc://x/voice6"}).json() == {"text": "Hallo aus Whisper"}

        # Stille: Whisper findet nichts – das ist ein Ergebnis und wird gesichert
        whisper.text = ""
        assert client.get(f"{API}/transcript", params={"src": "mxc://x/voicestill"}).json() == {"text": ""}
        n = len(whisper.calls)
        assert client.get(f"{API}/transcript", params={"src": "mxc://x/voicestill"}).json() == {"text": ""}
        assert len(whisper.calls) == n and store.transcripts(["mxc://x/voicestill"]) == {"mxc://x/voicestill": ""}
    finally:
        whisper.status, whisper.text = 200, "Hallo aus Whisper"
        set_pref(client, stt_local=False)


def test_dauert_es_laenger_heisst_es_noch_in_arbeit(client, whisper, monkeypatch):
    """Cloudflare bricht Anfragen nach ~100 s ab: nach WAIT_S kommt „pending“, die Umwandlung läuft weiter und wird gesichert."""
    from app import stt
    monkeypatch.setattr(stt, "WAIT_S", 0.3)
    set_pref(client, stt_local=True)
    whisper.delay = 1.0
    try:
        n = len(whisper.calls)
        src = {"src": "mxc://x/voiceslow"}
        assert client.get(f"{API}/transcript", params=src).json() == {"text": "", "pending": True}
        assert client.get(f"{API}/transcript", params=src).json() == {"text": "", "pending": True}   # hängt sich an dieselbe Arbeit
        time.sleep(1.2)
        assert client.get(f"{API}/transcript", params=src).json() == {"text": "Hallo aus Whisper"}
        assert len(whisper.calls) == n + 1   # nur einmal gerechnet
    finally:
        whisper.delay = 0.0
        set_pref(client, stt_local=False)


def test_lokal_ohne_dienst_nicht_waehlbar(client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "whisper_url", "")
    r = client.put(f"{API}/prefs", json={"stt_local": True})
    assert r.status_code == 409 and "WHISPER_URL" in r.json()["detail"]
    assert client.get(f"{API}/prefs").json()["stt_local"] is False
    d = client.get(f"{API}/stt/local").json()
    assert (d["configured"], d["status"], d["model"]) == (False, "aus", "")


def test_status_des_whisper_dienstes(client):
    d = client.get(f"{API}/stt/local").json()
    assert (d["configured"], d["status"], d["model"]) == (True, "bereit", "large-v3-turbo") and d["saved"] >= 1


def test_automatisch_nur_neue_sprachnachrichten_anderer(client, fake):
    from app import bus, store
    old = voice_msg("30", "mxc://x/voiceold", at="2026-01-01T10:00:00Z")
    later = datetime.fromtimestamp(time.time() + 30, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")   # kommt „nach dem Einschalten“
    mine = voice_msg("31", "mxc://x/voicemine", mine=True, at=later)
    long = voice_msg("33", "mxc://x/voicelong", at=later)
    long["attachments"][0]["duration"] = 3600
    new = voice_msg("32", "mxc://x/voiceauto?encryptedFileInfoJSON=a", at=later)
    before = voice_msg("34", "mxc://x/voicebefore", at=datetime.fromtimestamp(time.time() - 5, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))   # kam, bevor „Automatisch“ an war
    fake.msgs = {**MSGS, "items": [new, old, mine, long, before, *MSGS["items"]]}

    def load():
        return client.get(f"{API}/chats/1/messages").json()["items"]

    try:
        # aus (Standard): nichts passiert von selbst
        load(); time.sleep(0.5)
        assert not any("voiceauto" in s for s in transcribed(fake))

        q = bus.subscribe()
        try:
            set_pref(client, stt_auto=True)
            load()
            deadline = time.time() + 5
            while time.time() < deadline and not store.transcripts(["mxc://x/voiceauto"]):
                time.sleep(0.1)
            assert store.transcripts(["mxc://x/voiceauto"]) == {"mxc://x/voiceauto": "Hallo Welt"}
            evt = None
            while time.time() < deadline and not evt:
                try:
                    e = q.get_nowait()
                    evt = e if e["type"] == "stt" else None
                except Exception:
                    time.sleep(0.1)
            assert evt == {"type": "stt", "id": "1"}   # offene Fenster laden den Chat neu
        finally:
            bus.unsubscribe(q)

        time.sleep(0.3)
        got = [s for s in transcribed(fake) if any(w in s for w in ("voiceauto", "voiceold", "voicemine", "voicelong", "voicebefore"))]
        assert got == ["mxc://x/voiceauto?encryptedFileInfoJSON=a"]   # nicht: alt, eigene, zu lang, vor dem Einschalten – und nur einmal
        load()
        time.sleep(0.3)
        assert [s for s in transcribed(fake) if "voiceauto" in s] == got   # schon umgewandelt: nicht noch einmal
        assert next(m for m in load() if m["id"] == "32")["files"][0]["transcript"] == "Hallo Welt"
        # nicht rückwirkend: Alte, eigene und zu lange Sprachnachrichten bleiben ohne Text, bis jemand sie antippt
        assert store.transcripts(["mxc://x/voiceold", "mxc://x/voicemine", "mxc://x/voicelong", "mxc://x/voicebefore"]) == {}
    finally:
        fake.msgs = MSGS
        set_pref(client, stt_auto=False)


def test_automatisch_fehler_wird_nicht_gesichert_und_nicht_gehaemmert(client, fake, whisper):
    from app import store
    new = voice_msg("40", "mxc://x/voicefail")
    fake.msgs = {**MSGS, "items": [new, *MSGS["items"]]}
    whisper.status = 503
    try:
        set_pref(client, stt_local=True, stt_auto=True)
        client.get(f"{API}/chats/1/messages")
        deadline = time.time() + 5
        while time.time() < deadline and not any(c["path"] == "/api/v1/transkribieren" for c in whisper.calls):
            time.sleep(0.1)
        time.sleep(0.5)
        n = len(whisper.calls)
        assert n >= 1 and store.transcripts(["mxc://x/voicefail"]) == {}
        client.get(f"{API}/chats/1/messages"); client.get(f"{API}/chats/1/messages")
        time.sleep(0.5)
        assert len(whisper.calls) == n   # Pause nach Fehlschlag: der nächste Versuch kommt erst nach einiger Zeit
    finally:
        whisper.status = 200
        fake.msgs = MSGS
        set_pref(client, stt_local=False, stt_auto=False)


def test_stt_prefs_standard(client):
    assert client.get(f"{API}/prefs").json() == {"snaps_quiet": False, "snap_join": True, "stt_local": False, "stt_auto": False, "snap_open": False}
