"""IDA Chats: Liste, Nachrichten, Senden/Antworten, Bilder, Sprachnachricht, live, Oberfläche."""
import threading
import time

API = "/api/v1"


def test_liste(client, fake):
    d = client.get(f"{API}/chats", params={"q": "lu"}).json()
    assert d["next"] == "abc"
    a, b = d["items"]
    assert (a["id"], a["title"], a["network"], a["unread"], a["type"]) == ("1", "Luka", "snapchat", 0, "single")
    assert (b["network"], b["unread"], b["muted"], b["type"]) == ("matrix", 4, True, "group")
    assert fake.last("search_chats")["query"] == "lu" and fake.last("search_chats")["inbox"] == "primary"


def test_nachrichten(client, fake):
    d = client.get(f"{API}/chats/1/messages").json()
    assert [m["id"] for m in d["items"]] == ["8", "9"] and d["more"] and d["oldest"] == "8"   # alt → neu
    v = d["items"][1]
    assert v["reply_to"] == "8" and v["files"][0]["kind"] == "audio" and v["files"][0]["duration"] == 12
    assert v["files"][0]["transcript"] == "Hallo Mama" and "encryptedFileInfoJSON" in v["files"][0]["src"]
    assert v["reactions"] == [{"key": "👍", "n": 2, "mine": True}]
    assert d["items"][0]["files"][0]["kind"] == "image"
    # Lesebestätigung: nur bei meinen Nachrichten, ohne mich selbst, mit Namen wo bekannt
    assert d["items"][0]["read_by"] == [{"id": "@luka:x", "name": "Luka", "at": "2026-11-12T10:39:30Z"}, {"id": "@anna:x", "name": "", "at": "2026-11-12T10:39:45Z"}]
    assert d["items"][1]["read_by"] == []
    assert fake.last("rest_messages") == {}   # schneller Weg über die REST-API
    client.get(f"{API}/chats/1/messages", params={"cursor": "8"})
    assert fake.last("rest_messages") == {"cursor": "8", "direction": "before"}
    n = len(fake.calls)
    client.get(f"{API}/chats/1/messages", params={"cursor": "8"})
    assert len(fake.calls) == n   # ältere Seite kommt aus dem Zwischenspeicher
    client.get(f"{API}/chats/2/messages", params={"cursor": "5"})   # REST kennt den Chat nicht → über MCP
    assert fake.last("list_messages") == {"chatID": "2", "cursor": "5", "direction": "before"}


def test_senden_und_antworten(client, fake):
    assert client.post(f"{API}/chats/1/send", json={"text": " Hallo ", "reply_to": "9"}).json()["ok"]
    assert fake.last("send_message") == {"chatID": "1", "text": "Hallo", "replyToMessageID": "9"}
    assert client.post(f"{API}/chats/1/send", json={"text": "   "}).status_code == 400


def test_bild_und_sprachnachricht(client, fake):
    r = client.get(f"{API}/image", params={"src": "mxc://x/img"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert client.get(f"{API}/image", params={"src": "http://böse"}).status_code == 400
    assert client.get(f"{API}/transcript", params={"src": "mxc://x/voice2"}).json() == {"text": "Hallo Welt"}
    n = len(fake.calls)
    client.get(f"{API}/transcript", params={"src": "mxc://x/voice2"})
    assert len(fake.calls) == n   # zweites Mal aus dem Speicher


def test_live_meldet_geaenderten_chat(client, fake):
    from app import bus
    q = bus.subscribe()
    try:
        time.sleep(3)   # die Schleife schläft ohne Fenster 2 s; die erste Abfrage merkt sich dann den Stand
        fake.chats = fake.chats.replace("It has 4 unread", "It has 5 unread")
        evt = None
        deadline = time.time() + 5
        while time.time() < deadline and evt is None:
            try:
                evt = q.get_nowait()
            except Exception:
                time.sleep(0.1)
        assert evt and evt["type"] == "chat" and evt["id"] == "2" and evt["new"] and evt["unread"] == 5
    finally:
        bus.unsubscribe(q)
        fake.chats = fake.chats.replace("It has 5 unread", "It has 4 unread")


def test_oberflaeche(client):
    h = client.get("/chat/1")
    assert h.status_code == 200 and "<title>Chats</title>" in h.text and "/static/" not in h.text
    m = client.get("/manifest.webmanifest").json()
    assert m["short_name"] == "Chats" and m["display"] == "standalone"
    assert client.get("/sw.js").status_code == 200
    assert client.get("/icons/icon-192.png").status_code == 200
    assert client.get("/api/v1/health").json() == {"ok": True}


def test_audio_mit_range_und_unsichere_dateien(client):
    src = "mxc://x/voice?encryptedFileInfoJSON=abc"
    r = client.get(f"{API}/media", params={"src": src, "mime": "audio/ogg"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and len(r.content) == 200 and r.headers["accept-ranges"] == "bytes"
    r = client.get(f"{API}/media", params={"src": src}, headers={"range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20)) and r.headers["content-range"] == "bytes 10-19/200"
    r = client.get(f"{API}/media", params={"src": src}, headers={"range": "bytes=-5"})
    assert r.status_code == 206 and r.content == bytes(range(195, 200))
    assert client.get(f"{API}/media", params={"src": src}, headers={"range": "bytes=500-600"}).status_code == 416
    r = client.get(f"{API}/media", params={"src": "mxc://x/page"})   # HTML kommt nie als HTML an
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"] == "attachment"


def test_profilbilder(client, fake):
    r = client.get(f"{API}/avatar/7")   # Gruppenbild (Windows-Pfad → file://)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert fake.served[-1] == "file:///C:/Users/x/grp.png"
    assert client.get(f"{API}/avatar/7", params={"user": "@luka:x"}).status_code == 200   # Gruppenmitglied
    assert client.get(f"{API}/avatar/7", params={"user": "@me:x"}).status_code == 404     # ohne Bild: Buchstabe bleibt
    assert client.get(f"{API}/avatar/1").status_code == 200 and fake.served[-1].endswith("luka.png")   # Einzelchat: die andere Person
    n = len(fake.served)
    client.get(f"{API}/avatar/7")
    assert len(fake.served) == n   # zwischengespeichert
    assert client.get(f"{API}/avatar/x%2F..%2Fy").status_code in (400, 404)


def test_reagieren(client, fake):
    assert client.post(f"{API}/chats/1/messages/9/react", json={"key": "❤️"}).json() == {"ok": True}
    assert fake.rest[-1] == ("POST", "/v1/chats/1/messages/9/reactions", {"reactionKey": "❤️"})
    assert client.delete(f"{API}/chats/1/messages/9/react", params={"key": "❤️"}).status_code == 200
    assert fake.rest[-1][0] == "DELETE" and fake.rest[-1][1] == "/v1/chats/1/messages/9/reactions/%E2%9D%A4%EF%B8%8F"
    assert client.post(f"{API}/chats/1/messages/a%2Fb/react", json={"key": "x"}).status_code in (400, 404, 405)
    assert client.post(f"{API}/chats/1/read").json() == {"ok": True}
    assert fake.rest[-1] == ("POST", "/v1/chats/1/read", {"messageID": "9"})   # bis zur neuesten fremden Nachricht (Snap = geöffnet)
    assert client.post(f"{API}/chats/1/unread").json()["ok"] is True
    assert fake.rest[-1] == ("POST", "/v1/chats/1/unread", {})
    client.post(f"{API}/chats/1/unread", json={"message_id": "9"})
    assert fake.rest[-1] == ("POST", "/v1/chats/1/unread", {"messageID": "9"})


def test_bild_und_sprachnachricht_senden(client, fake, monkeypatch):
    from app import api
    r = client.post(f"{API}/chats/1/upload", files={"file": ("foto.jpg", b"\xff\xd8\xffdata", "image/jpeg")}, data={"text": "Schau", "reply_to": "8"})
    assert r.json() == {"ok": True, "id": "p7", "type": "image"}
    up, send = fake.rest[-2], fake.rest[-1]
    assert up[0] == "UPLOAD" and b"foto.jpg" in up[2]["raw"] and up[2]["ct"] == "multipart/form-data"
    assert send[1] == "/v1/chats/1/messages" and send[2] == {"attachment": {"uploadID": "up1", "type": "image", "mimeType": "image/jpeg", "fileName": "foto.jpg"},
                                                            "text": "Schau", "replyToMessageID": "8"}

    async def fake_ogg(data):
        return b"OggS" + data
    monkeypatch.setattr(api, "to_ogg", fake_ogg)   # ffmpeg gibt es im Test nicht unbedingt
    r = client.post(f"{API}/chats/1/upload", files={"file": ("a.webm", b"webmdata", "audio/webm;codecs=opus")}, data={"voice": "true", "duration": "4.2"})
    assert r.json()["type"] == "voice-note"
    att = fake.rest[-1][2]["attachment"]
    assert att == {"uploadID": "up1", "type": "voice-note", "mimeType": "audio/ogg; codecs=opus", "fileName": "Sprachnachricht.ogg", "duration": 4.2}
    assert b"OggSwebmdata" in fake.rest[-2][2]["raw"]
    assert client.post(f"{API}/chats/1/upload", files={"file": ("x", b"", "text/plain")}).status_code == 400


def test_sprachnachricht_nicht_umgewandelt_wird_nicht_gesendet(client, fake, monkeypatch):
    """Schlägt ffmpeg fehl, darf kein rohes WebM/MP4 als Sprachnachricht rausgehen – der Empfänger (WhatsApp) könnte es nicht abspielen."""
    from app import api

    async def kein_ogg(data):
        return None
    monkeypatch.setattr(api, "to_ogg", kein_ogg)
    vorher = len(fake.rest)
    r = client.post(f"{API}/chats/1/upload", files={"file": ("a.webm", b"webmdata", "audio/webm;codecs=opus")}, data={"voice": "true", "duration": "4.2"})
    assert r.status_code == 422 and "nochmal aufnehmen" in r.json()["detail"]
    assert len(fake.rest) == vorher   # nichts hochgeladen, nichts gesendet
    # Eine Aufnahme, die schon Ogg ist (Firefox), geht auch ohne Umwandlung raus
    r = client.post(f"{API}/chats/1/upload", files={"file": ("a.ogg", b"OggSdata", "audio/ogg;codecs=opus")}, data={"voice": "true"})
    att = fake.rest[-1][2]["attachment"]
    assert r.status_code == 200 and att["type"] == "voice-note" and att["mimeType"] == "audio/ogg; codecs=opus"


def test_to_ogg_mit_echtem_ffmpeg(caplog):
    import asyncio
    import shutil
    import subprocess
    import pytest
    from app import api
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg fehlt")
    webm = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "libopus", "-f", "webm", "pipe:1"],
                          capture_output=True, check=True).stdout
    out = asyncio.run(api.to_ogg(webm))
    assert out and out.startswith(b"OggS")
    with caplog.at_level("WARNING", logger="ida.chats"):
        assert asyncio.run(api.to_ogg(b"das ist keine audiodatei")) is None
    assert "ffmpeg fehlgeschlagen" in caplog.text


def test_bild_schon_bei_beeper_heruntergeladen(client, fake):
    """Lädt Beeper ein Bild herunter, wird aus mxc:// ein lokaler Pfad – der muss weiter gehen, aber nur in Beepers Medienordner."""
    src = "file:///C:/Users/Docker/AppData/Roaming/BeeperTexts/media/local.beeper.com/l8tenever_QHHGiiEo9DGNXtDVOy7lcji6LxVad5CDG54ElzPeczWkmOcVPHk9IdIn8g3xjIoa"
    r = client.get(f"{API}/media", params={"src": src, "mime": "image/jpeg"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and fake.served[-1] == src
    r = client.get(f"{API}/media", params={"src": src.replace("l8tenever_Q", "l8tenever_Z")})   # ohne Typ: aus den ersten Bytes
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    for bad in ("file:///C:/Windows/win.ini", "file:///C:/Users/Docker/AppData/Roaming/BeeperTexts/media/../../secret.txt",
                "file:///C:/Users/Docker/Desktop/x.png", "http://evil/x.png"):
        assert client.get(f"{API}/media", params={"src": bad}).status_code == 400, bad


def test_anpinnen(client, fake):
    d = client.get(f"{API}/pins").json()
    assert d["max"] == 3 and [(c["id"], c["network"], c["unread"]) for c in d["items"]] == [("2", "whatsapp", 2)]
    for i in ("1", "3"):
        assert client.post(f"{API}/chats/{i}/pin", json={"pinned": True}).json() == {"ok": True}
    assert fake.rest[-1] == ("PATCH", "/v1/chats/3", {"isPinned": True})
    assert client.post(f"{API}/chats/4/pin", json={"pinned": True}).status_code == 400   # höchstens drei
    assert client.post(f"{API}/chats/2/pin", json={"pinned": False}).status_code == 200
    assert [c["id"] for c in client.get(f"{API}/pins").json()["items"]] == ["1", "3"]


def test_bearbeiten_und_weiterleiten(client, fake):
    assert client.put(f"{API}/chats/1/messages/9", json={"text": " Neu "}).json() == {"ok": True}
    assert fake.rest[-1] == ("PUT", "/v1/chats/1/messages/9", {"text": "Neu"})
    assert client.put(f"{API}/chats/1/messages/9", json={"text": "  "}).status_code == 400
    assert client.post(f"{API}/chats/2/forward", json={"text": "Hallo"}).json() == {"ok": True}
    assert fake.rest[-1] == ("POST", "/v1/chats/2/messages", {"text": "Hallo"})
    src = "file:///C:/Users/Docker/AppData/Roaming/BeeperTexts/media/local.beeper.com/abc"
    n = len(fake.rest)
    assert client.post(f"{API}/chats/2/forward", json={"text": "Schau", "files": [{"src": src, "mime": "image/png", "name": "", "kind": "image"}]}).status_code == 200
    assert [x[0] for x in fake.rest[n:]] == ["UPLOAD", "POST"]   # Text hängt am Bild, keine zweite Nachricht
    assert fake.rest[-1][2] == {"attachment": {"uploadID": "up1", "type": "image", "mimeType": "image/png", "fileName": "datei.png"}, "text": "Schau"}
    assert client.post(f"{API}/chats/2/forward", json={"files": [{"src": "file:///C:/Windows/win.ini"}]}).status_code == 400
    assert client.post(f"{API}/chats/2/forward", json={}).status_code == 400


def test_html_wird_zu_text():
    from app.api import plain
    assert plain("<p>Hallo</p>\n<p>Welt &amp; so</p>") == "Hallo\nWelt & so"
    assert plain('<a href="https://a.de/x?y=1" rel="noopener" target="_blank">https://a.de/x?y=1</a>') == "https://a.de/x?y=1"
    assert plain('Schau <a href="https://a.de">hier</a><br>und <b>da</b>') == "Schau hier (https://a.de)\nund da"
    assert plain("a < b und c > d <3") == "a < b und c > d <3"
    assert plain("Hab grad n Jahr &quot;frei&quot; &amp; so&#33;") == "Hab grad n Jahr \"frei\" & so!"
    assert plain("Tom & Jerry") == "Tom & Jerry"


def test_geloeschtes_bleibt_lesbar_und_loeschen(client, fake):
    from app.api import keep_deleted
    m = {"id": "a1", "at": "2026-11-12T10:00:00Z", "text": "Geheim", "files": [], "deleted": False}
    assert keep_deleted("c9", [dict(m)])[0].get("was") is None
    gone = keep_deleted("c9", [{**m, "text": "", "deleted": True}])[0]
    assert gone["deleted"] and gone["text"] == "" and gone["was"] == {"text": "Geheim", "files": []}
    assert keep_deleted("c9", [{"id": "nie", "text": "", "files": [], "deleted": True}])[0]["was"] is None
    # für mich löschen: geht an Beeper und bleibt danach ausgeblendet
    assert client.delete(f"{API}/chats/c9/messages/a1").json() == {"ok": True}
    assert fake.rest[-1][:2] == ("DELETE", "/v1/chats/c9/messages/a1?forEveryone=false")
    assert keep_deleted("c9", [dict(m)]) == []
    client.delete(f"{API}/chats/c9/messages/a2", params={"everyone": "true"})
    assert fake.rest[-1][1].endswith("forEveryone=true")


def test_merge_combines_chats(client, monkeypatch):
    from app import api, store
    pages = {"a": {"items": [{"id": "1", "timestamp": "2026-11-12T10:00:00Z", "text": "von A"}], "hasMore": True, "oldestCursor": "ca"},
             "b": {"items": [{"id": "1", "timestamp": "2026-11-12T11:00:00Z", "text": "von B", "linkedMessageID": "0"}], "hasMore": False}}

    async def fake(chat_id, cursor, direction):
        return pages[chat_id] if not cursor else {"items": [], "hasMore": False}
    monkeypatch.setattr(api, "fetch_messages", fake)
    r = client.post("/api/v1/merges", json={"chats": [{"id": "a", "title": "Arne", "network": "whatsapp"}, {"id": "b", "network": "signal"}]})
    assert r.json()["id"] == "a"
    assert client.get("/api/v1/merges").json()["items"][0]["chats"][1]["network"] == "signal"
    d = client.get("/api/v1/chats/a/messages").json()
    assert [(m["chat"], m["mid"], m["id"]) for m in d["items"]] == [("a", "1", "a~1"), ("b", "1", "b~1")]
    assert d["items"][1]["reply_to"] == "b~0" and d["more"]
    assert client.get("/api/v1/chats/a/messages", params={"cursor": d["oldest"]}).json()["items"] == []
    assert client.patch("/api/v1/merges/a", json={"name": " Arne ", "avatar": "b", "send": "b"}).status_code == 200
    assert client.patch("/api/v1/merges/a", json={"send": "x"}).status_code == 400
    g = client.get("/api/v1/merges").json()["items"][0]
    assert (g["name"], g["avatar"], g["send"]) == ("Arne", "b", "b")
    client.delete("/api/v1/merges/a", params={"chat": "b"})
    assert store.groups() == {}


def test_about(client, monkeypatch):
    from app import api

    class R:
        status_code = 200
        def json(self):
            return {"title": "Luka", "network": "WhatsApp", "description": "<p>Hey</p>", "type": "single", "isMuted": True,
                    "participants": {"items": [{"id": "u1", "fullName": "Luka", "phoneNumber": "+49 1", "imgURL": "file:///x"}, {"id": "me", "isSelf": True}], "total": 2}}

    async def fake(path, **kw):
        return R()
    monkeypatch.setattr(api, "rest_get", fake)
    d = client.get("/api/v1/chats/7/about").json()["chats"][0]
    assert (d["description"], d["muted"], d["total"]) == ("Hey", True, 2)
    assert d["people"][0]["phone"] == "+49 1" and "img" not in d["people"][0]


def test_korrektur(client, monkeypatch):
    from app import api
    from app.config import get_settings
    assert client.post(f"{API}/korrektur", json={"text": "eknn"}).json() == {"changed": False}   # Dienst aus
    monkeypatch.setattr(get_settings(), "korrektur_url", "http://127.0.0.1:9")                  # nicht erreichbar
    assert client.post(f"{API}/korrektur", json={"text": "eknn"}).json() == {"changed": False}
    api._fix = None


def test_push_und_stumm(client, monkeypatch):
    import asyncio

    from app import api, live, push
    key = client.get(f"{API}/push").json()["key"]
    assert len(key) > 80 and key == client.get(f"{API}/push").json()["key"]          # Schlüssel einmal erzeugt, bleibt gleich
    assert client.post(f"{API}/push/subscribe", json={"endpoint": "http://x", "keys": {"p256dh": "a", "auth": "b"}}).status_code == 400
    assert client.post(f"{API}/push/subscribe", json={"endpoint": "https://push.example/1", "keys": {"p256dh": "a", "auth": "b"}}).json()["ok"]
    assert push.has_subs()

    sent: list[dict] = []

    async def fake_send(p):
        sent.append(p)
        return 1
    monkeypatch.setattr(push, "send", fake_send)

    async def fake_rest():
        return [{"id": "1", "preview": {"text": "Na du?", "senderName": "Luka", "isSender": False, "timestamp": "2026-11-12T11:00:00Z"}},
                {"id": "2", "preview": {"text": "", "senderName": "Anna Maier", "attachments": [{"mimeType": "image/jpeg"}]}}]
    monkeypatch.setattr(api, "rest_chats", fake_rest)

    one = {"id": "1", "title": "Luka", "type": "single", "unread": 1, "at": "x", "muted": False}
    grp = {"id": "2", "title": "Maurice", "type": "group", "unread": 3, "at": "x", "muted": False}
    asyncio.run(live.notify([one, grp]))
    assert [(p["chat"], p["title"], p["body"], p["av"]) for p in sent] == [("1", "Luka", "Na du?", "1"), ("2", "Maurice", "Anna Maier: 📷 Foto", "2")]

    # Stumm für 8 Stunden: keine Meldung mehr; in Beeper stumm: auch keine
    sent.clear()
    r = client.put(f"{API}/chats/1/mute", json={"hours": 8}).json()
    assert r["until"] > time.time() + 7.9 * 3600
    asyncio.run(live.notify([one, {**grp, "muted": True}]))
    assert sent == []
    assert {m["id"] for m in client.get(f"{API}/mutes").json()["items"]} == {"1"}
    # für immer, dann aufheben
    assert client.put(f"{API}/chats/1/mute", json={"hours": 0}).json()["until"] == 0
    client.delete(f"{API}/chats/1/mute")
    assert client.get(f"{API}/mutes").json()["items"] == []
    asyncio.run(live.notify([one]))
    assert len(sent) == 1
    # abgelaufene Stummschaltung zählt nicht mehr
    push.mute("1", 0.0001); time.sleep(0.5)
    assert not push.muted("1")
    client.post(f"{API}/push/unsubscribe", json={"endpoint": "https://push.example/1"})
    assert not push.has_subs()


def test_pins_mit_lokaler_id(client, monkeypatch):
    """REST liefert Matrix-IDs, die Chatliste lokale – Pins müssen die lokale tragen, sonst steht der Chat doppelt da."""
    from app import api

    async def rest():
        return [{"id": "!abc:beeper.local", "localChatID": "66736", "title": "Ida", "isPinned": True}]
    monkeypatch.setattr(api, "rest_chats", rest)
    monkeypatch.setattr(api, "_pins", None)
    assert [c["id"] for c in client.get(f"{API}/pins").json()["items"]] == ["66736"]


def test_snap_oeffnen(monkeypatch):
    """Snapchat: gelesen markieren öffnet den Chat zusätzlich in Beeper Desktop und springt danach auf den Park-Chat."""
    import asyncio

    from app import api
    calls = []

    class R:
        status_code = 200

        def __init__(self, items):
            self._items = items

        def json(self):
            return {"items": self._items}

    async def rest_get(path, **params):
        if path == "/v1/chats":
            return R([{"id": "!n:x", "localChatID": 5, "type": "single", "title": "Note to self", "participants": {"items": [{}]}}])
        net = "snapchat" if "/7/" in path else "whatsapp"
        return R([{"id": "3", "isSender": True, "accountID": net}, {"id": "2", "isSender": False, "accountID": net}])

    async def rest_call(method, path, body=None):
        calls.append((path, body))
        return {}

    monkeypatch.setattr(api, "rest_get", rest_get)
    monkeypatch.setattr(api, "rest_call", rest_call)
    monkeypatch.setattr(api, "OPEN_HOLD_S", 0)
    api._park.clear()

    async def run():
        await api.beeper_read("7")
        await api.beeper_read("8")
        await asyncio.gather(*api._open_tasks)

    asyncio.run(run())
    # WhatsApp: nur gelesen. Snapchat: erst öffnen (noch ungelesen), dann gelesen, dann Park-Chat
    assert calls == [("/v1/chats/8/read", {"messageID": "2"}), ("/v1/focus", {"chatID": "7", "messageID": "2"}),
                     ("/v1/chats/7/read", {"messageID": "2"}), ("/v1/focus", {"chatID": "5"})]


def test_beeper_sammelchat_ueber_die_chats_dahinter_lesen(monkeypatch):
    """Auf einem Sammelchat von Beeper (Feld „merge“) antwortet /read mit 500 – gelesen wird über die Chats dahinter, die Ungelesenes haben."""
    import asyncio
    from app import api
    calls = []

    class R:
        status_code = 200

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    chats = {"9": {"id": "!m:x", "localChatID": "9", "merge": {"chatIDs": ["!a:x", "!b:x", "!c:x", "!weg:x"]}, "unreadCount": 3},
             "!a:x": {"id": "!a:x", "localChatID": "11", "unreadCount": 2},
             "!b:x": {"id": "!b:x", "localChatID": "12", "unreadCount": 0},
             "!c:x": {"id": "!c:x", "localChatID": "13", "unreadCount": 1}}

    async def rest_get(path, **params):
        if path.endswith("/messages"):
            return R({"items": [{"id": "5", "isSender": False, "accountID": "whatsapp.1"}]})
        d = chats.get(path.rsplit("/", 1)[1].replace("%21", "!").replace("%3A", ":"))
        if d is None:   # Beeper kennt den Chat nicht mehr
            r = R({})
            r.status_code = 404
            return r
        return R(d)

    async def rest_call(method, path, body=None):
        calls.append(path)
        if path == "/v1/chats/13/read":
            raise api.HTTPException(502, "Beeper: kaputt")
        return {}
    monkeypatch.setattr(api, "rest_get", rest_get)
    monkeypatch.setattr(api, "rest_call", rest_call)

    async def run():
        try:
            await api.beeper_read("9")
        except api.HTTPException as e:
            return e
    err = asyncio.run(run())
    # nur die Chats mit Ungelesenem, nie der Sammelchat selbst, der unbekannte (404) wird übersprungen; der Fehler von 13 kommt erst nach dem Rest
    assert calls == ["/v1/chats/11/read", "/v1/chats/13/read"] and err and err.status_code == 502


def test_gruppe_lesen_ein_fehler_haelt_die_anderen_nicht_auf(client, monkeypatch):
    from app import api
    done = []

    async def fake_read(c):
        done.append(c)
        if c == "1":
            raise api.HTTPException(502, "Beeper: kaputt")
    monkeypatch.setattr(api, "beeper_read", fake_read)
    monkeypatch.setattr(api, "group_members", lambda cid: ["1", "2", "3"] if cid == "g" else [])
    r = client.post(f"{API}/chats/g/read")
    assert done == ["1", "2", "3"] and r.status_code == 502


def test_angepinnte_nachrichten(client, monkeypatch):
    """Beeper hat kein Pin-Feld – nur den Hinweis „Pinned a message“ mit Verweis auf die Nachricht. Gelöschtes und Hinweise ohne Verweis zählen nicht."""
    from app import api

    class R:
        status_code = 200

        def __init__(self, data, code=200):
            self._d, self.status_code = data, code

        def json(self):
            return self._d
    notes = [
        {"id": "9", "type": "NOTICE", "text": "Pinned a message", "linkedMessageID": "5", "timestamp": "2026-11-17T16:20:52.000Z", "senderName": "Maurice"},
        {"id": "8", "type": "NOTICE", "text": "Pinned a message", "linkedMessageID": "4", "timestamp": "2026-11-16T10:00:00.000Z", "senderName": "Finja"},
        {"id": "7", "type": "NOTICE", "text": "Pinned a message", "timestamp": "2026-11-15T10:00:00.000Z"},                      # ohne Verweis
        {"id": "6", "type": "NOTICE", "text": "Pinned a message", "linkedMessageID": "3", "timestamp": "2026-11-14T10:00:00.000Z"},   # Nachricht gelöscht
        {"id": "5", "type": "TEXT", "text": "pinned sessions are great", "linkedMessageID": "2", "timestamp": "2026-11-17T09:00:00.000Z"},   # nur das Wort, kein Hinweis
    ]
    targets = {"5": {"id": "5", "senderID": "@me:x", "isSender": True, "text": "Wer wäre bei Kino dabei", "isDeleted": False, "timestamp": "2026-11-17T16:17:49.000Z"},
               "4": {"id": "4", "senderID": "@f:x", "senderName": "Finja", "type": "TEXT", "text": "Treffpunkt 18 Uhr", "isDeleted": False, "timestamp": "2026-11-16T09:00:00.000Z"},
               "3": {"id": "3", "type": "TEXT", "text": "weg", "isDeleted": True}}
    seen = []

    async def rest_get(path, **params):
        seen.append((path, params))
        if path == "/v1/messages/search":
            return R({"items": notes})
        mid = path.rsplit("/", 1)[1]
        return R(targets[mid]) if mid in targets else R({}, 404)
    monkeypatch.setattr(api, "rest_get", rest_get)
    d = client.get(f"{API}/chats/9/pins").json()
    assert [(x["id"], x["text"], x["pinned_by"]) for x in d["items"]] == [("5", "Wer wäre bei Kino dabei", "Maurice"), ("4", "Treffpunkt 18 Uhr", "Finja")]
    assert d["items"][0]["pinned_at"] == "2026-11-17T16:20:52.000Z" and d["days"] == api.PIN_DAYS
    srch = seen[0][1]
    assert srch["chatIDs"] == ["9"] and srch["dateAfter"].endswith("Z")   # nur die letzten Tage


def test_systemhinweise_erkennen():
    """Hinweise des Netzwerks (Snapstreak, „left the chat“) sind keine Nachrichten von jemandem – Beeper schickt sie als NOTICE oder ganz ohne Typ."""
    from app import api
    assert api.is_system({"type": "NOTICE", "text": "You and Sophia started a Snapstreak", "isDeleted": False})
    assert api.is_system({"text": "Abi. Jeyaseelan left the chat", "senderName": "@signal_ddf1da39:beeper.local"})   # Signal: ganz ohne type und isDeleted
    assert api.is_system({"text": "Disappearing messages turned off"})
    assert not api.is_system({"type": "TEXT", "text": "Hi", "isDeleted": False})
    assert not api.is_system({"text": "Neu im Kino: Wild Horse Nine", "isDeleted": True})   # WhatsApp-Bot: ohne type, aber echter Inhalt
    assert not api.is_system({"type": "NOTICE", "text": ""}) and not api.is_system({"text": "  "})
    assert api.message({"id": "1", "text": "x left the chat"})["sys"] is True
    assert api.message({"id": "2", "type": "TEXT", "text": "hallo", "isDeleted": False})["sys"] is False


def test_snaps_still(client, monkeypatch):
    """Einstellung „Snaps nicht als neue Nachricht“: Snapchat-Chats zählen nur echte Nachrichten (Zeit, Ungelesen, Vorschau)."""
    from app import api
    assert api.is_snap({"type": "IMAGE"}) and api.is_snap({"type": "NOTICE", "text": "Your 46-day Snapstreak ended"})
    assert api.is_snap({"type": "IMAGE", "isSender": True, "attachments": [{"fileName": "SNAP-1.jpg"}]})
    assert not api.is_snap({"type": "TEXT", "text": "Hi"}) and not api.is_snap({"type": "IMAGE", "attachments": [{"fileName": "foto.jpg"}]})
    msgs = {"items": [{"id": "3", "timestamp": "2026-11-12T10:40:00Z", "type": "IMAGE", "isUnread": True},
                      {"id": "2", "timestamp": "2026-11-12T10:30:00Z", "type": "TEXT", "text": "Na?", "senderName": "Luka"},
                      {"id": "1", "timestamp": "2026-11-12T10:20:00Z", "type": "TEXT", "text": "Hey", "isUnread": True}]}
    calls = []

    async def fake_fetch(chat_id, cursor, direction):
        calls.append(chat_id)
        return msgs
    monkeypatch.setattr(api, "fetch_messages", fake_fetch)
    api._snap_cache.clear()
    assert client.get(f"{API}/prefs").json() == {"snaps_quiet": False, "snap_join": True, "stt_local": False, "stt_auto": False}
    assert [c["id"] for c in client.get(f"{API}/chats").json()["items"]] == ["1", "2"] and not calls   # aus: wie Beeper
    assert client.put(f"{API}/prefs", json={"snaps_quiet": True}).json() == {"snaps_quiet": True, "snap_join": True, "stt_local": False, "stt_auto": False}
    assert client.put(f"{API}/prefs", json={"snap_join": False}).json() == {"snaps_quiet": True, "snap_join": False, "stt_local": False, "stt_auto": False}
    client.put(f"{API}/prefs", json={"snap_join": True})
    items = client.get(f"{API}/chats").json()["items"]
    assert [c["id"] for c in items] == ["2", "1"]   # der Snap schiebt den Chat nicht mehr nach oben
    assert (items[1]["at"], items[1]["unread"], items[1]["last"]["text"]) == ("2026-11-12T10:30:00Z", 1, "Na?")
    client.get(f"{API}/chats")
    assert calls == ["1"]   # gemerkt, solange im Chat nichts Neues passiert
    client.put(f"{API}/prefs", json={"snaps_quiet": False})


def test_fcm_android(client, monkeypatch):
    """Android-App: Firebase-Dateien liegen nur im Dienst, die App holt sich ihre Verbindungsdaten, Geräte melden sich an."""
    import base64
    import json

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    from app import push
    assert client.get(f"{API}/fcm").json() == {"client": False, "account": False, "project": "", "account_project": "", "package": "", "devices": 0}
    assert client.get(f"{API}/fcm/client").status_code == 404
    assert client.put(f"{API}/fcm/client", json={"foo": 1}).status_code == 400
    services = {"project_info": {"project_number": "123", "project_id": "ida-x"}, "client": [
        {"client_info": {"mobilesdk_app_id": "1:123:android:zzz", "android_client_info": {"package_name": "other.app"}}, "api_key": [{"current_key": "KEY-OTHER"}]},
        {"client_info": {"mobilesdk_app_id": "1:123:android:abc", "android_client_info": {"package_name": "com.kulbarts.chats"}}, "api_key": [{"current_key": "KEY"}]}]}
    assert client.put(f"{API}/fcm/client", json=services).json()["client"] is True
    assert client.get(f"{API}/fcm/client").json() == {"project_id": "ida-x", "sender_id": "123", "app_id": "1:123:android:abc", "api_key": "KEY", "package": "com.kulbarts.chats"}
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    assert client.put(f"{API}/fcm/account", json={"type": "service_account", "project_id": "ida-x"}).status_code == 400
    st = client.put(f"{API}/fcm/account", json={"type": "service_account", "project_id": "ida-x", "private_key": pem, "client_email": "a@ida-x.iam.gserviceaccount.com"}).json()
    assert st["account"] is True and st["account_project"] == "ida-x" and "private_key" not in json.dumps(st)
    # Das Anmelde-JWT fürs Dienstkonto ist gültig signiert
    h, b, sig = push._jwt(push.fcm_account()).split(".")
    key.public_key().verify(base64.urlsafe_b64decode(sig + "=="), f"{h}.{b}".encode(), padding.PKCS1v15(), hashes.SHA256())
    assert json.loads(base64.urlsafe_b64decode(b + "=="))["scope"] == push.FCM_SCOPE
    tok = "t" * 40
    assert client.post(f"{API}/fcm/register", json={"token": tok}).json() == {"ok": True}
    assert client.get(f"{API}/fcm").json()["devices"] == 1 and push.has_subs()
    client.post(f"{API}/fcm/unregister", json={"token": tok})
    assert client.delete(f"{API}/fcm").json()["client"] is False and push.fcm_account() is None


def test_fcm_reserved_keys():
    from app import push
    assert [push._fcm_key(k) for k in ("from", "chat", "gcm.x", "notification")] == ["sender", "chat", "x_gcm.x", "x_notification"]


def test_ordnung_startseite_und_listen(client, fake):
    # Sichtbarkeit pro Chat und pro Netzwerk, eigene Listen – gemerkt im Dienst, für alle Geräte
    assert client.get(f"{API}/org").json() == {"chats": [], "nets": {}, "labels": []}
    st = client.put(f"{API}/chats/1/vis", json={"mode": "hidden", "title": "Luka", "network": "snapchat"}).json()
    assert st["chats"] == [{"id": "1", "mode": "hidden", "title": "Luka", "network": "snapchat"}]
    assert client.put(f"{API}/chats/1/vis", json={"mode": "egal"}).status_code == 400
    assert client.put(f"{API}/chats/1/vis", json={"mode": ""}).json()["chats"] == []
    assert client.put(f"{API}/nets/snapchat/vis", json={"mode": "new"}).json()["nets"] == {"snapchat": "new"}
    assert client.put(f"{API}/nets/Snap Chat/vis", json={"mode": "new"}).status_code in (400, 404)
    assert client.put(f"{API}/nets/snapchat/vis", json={"mode": ""}).json()["nets"] == {}
    r = client.post(f"{API}/labels", json={"name": "  Tagesmutter "}).json()
    lid = r["created"]
    assert [x["name"] for x in r["labels"]] == ["Tagesmutter"]
    assert client.post(f"{API}/labels", json={"name": "tagesmutter"}).status_code == 409
    st = client.put(f"{API}/labels/{lid}/chats/2", json={"on": True, "title": "Familie", "network": "whatsapp"}).json()
    assert st["labels"][0]["chats"] == [{"id": "2", "title": "Familie", "network": "whatsapp"}]
    assert client.put(f"{API}/labels/{lid}", json={"name": "Kita"}).json()["labels"][0]["name"] == "Kita"
    assert client.put(f"{API}/labels/zzz/chats/2", json={"on": True}).status_code == 404
    assert client.delete(f"{API}/labels/{lid}").json()["labels"] == []


def test_archivieren(client, fake):
    n = len(fake.rest)
    assert client.post(f"{API}/chats/1/archive", json={"archived": True}).json() == {"ok": True}
    assert fake.rest[n:] == [("POST", "/v1/chats/1/archive", {"archived": True})]


def test_suche_kurz_gemerkt(client, fake):
    client.get(f"{API}/chats", params={"q": "zwischen"})
    n = sum(1 for c, _ in fake.calls if c == "search_chats")
    client.get(f"{API}/chats", params={"q": "zwischen"})
    assert sum(1 for c, _ in fake.calls if c == "search_chats") == n   # zweites Mal aus dem Zwischenspeicher
    client.get(f"{API}/chats")
    assert sum(1 for c, _ in fake.calls if c == "search_chats") == n + 1   # die normale Liste ist immer frisch


def test_meldung_weg_wenn_gelesen(monkeypatch):
    """Gelesen (hier oder anderswo): die Android-App bekommt ein stilles Signal und nimmt die Meldung weg – nur einmal."""
    import asyncio

    from app import api, live, push
    got: list[tuple[dict, bool]] = []

    async def fake_fcm(p, tokens=None, urgent=True):
        got.append((p, urgent))
        return 1
    monkeypatch.setattr(push, "fcm_send", fake_fcm)
    monkeypatch.setattr(push, "fcm_tokens", lambda: ["t" * 40])
    monkeypatch.setattr(push, "_dismissed", {})
    monkeypatch.setattr(push, "has_subs", lambda: False)
    monkeypatch.setattr(push, "snaps_quiet", lambda: False)
    monkeypatch.setattr(push, "note_key", lambda c: c)
    unread = {"n": 2}

    class B:
        async def text(self, *_a):
            return ""
    monkeypatch.setattr(api, "parse_chats", lambda _t: ([{"id": "1", "title": "Luka", "at": "x", "unread": unread["n"]}], None))
    monkeypatch.setattr(live, "parse_chats", api.parse_chats)
    monkeypatch.setattr(live, "beeper", lambda: B())
    monkeypatch.setattr(live.get_settings(), "archive_poll_s", 0)

    async def run():
        st: dict = {}
        await live.check(st)
        unread["n"] = 0           # auf einem anderen Gerät gelesen
        await live.check(st)
        await asyncio.sleep(0)
        await push.dismiss("1")   # gleich danach noch einmal (Markieren + Abgleich): nichts Doppeltes
    asyncio.run(run())
    assert got == [({"type": "dismiss", "chat": "1"}, False)]
    push.new_note("1")            # neue Meldung: das nächste Gelesen darf sie wieder wegnehmen
    asyncio.run(push.dismiss("1"))
    assert len(got) == 2


def test_vorschlaege_zusammenfuehren(client):
    from app import org
    assert org.norm_name("꧁☬Moro☬꧂") == "moro" and org.norm_name("Lúka  Meier (Arbeit)") == "luka meier"
    chats = [{"id": "w1", "title": "Luka Meier", "network": "whatsapp", "type": "single"},
             {"id": "i1", "title": "luka.meier", "network": "instagramgo", "type": "single"},
             {"id": "s1", "title": "Luka", "network": "snapchat", "type": "single"},
             {"id": "s2", "title": "Luka", "network": "snapchat", "type": "single"},       # zweiter Luka im selben Netzwerk: nicht dazu
             {"id": "w2", "title": "Familie", "network": "whatsapp", "type": "group"},
             {"id": "i2", "title": "Anna", "network": "instagram", "type": "single"},
             {"id": "m1", "title": "Luka Meier", "network": "matrix", "type": "single"}]    # Beeper selbst: nie
    got = org.suggest(chats, set(), set())
    assert len(got) == 1
    ids = {c["id"] for c in got[0]["chats"]}
    assert {"w1", "i1"} <= ids and len(ids & {"s1", "s2"}) == 1 and "m1" not in ids and got[0]["reason"] == "Gleicher Name"
    # schon zusammengeführt oder abgelehnt: kommt nicht mehr
    assert org.suggest(chats, {"w1", "i1"}, set()) == []   # übrig: zwei Snapchat-Chats – dasselbe Netzwerk wird nie vorgeschlagen
    assert client.post(f"{API}/merges/suggestions/dismiss", json={"chats": ["w1", "i1"]}).json() == {"ok": True}
    nope = org.nopes()
    assert ("i1", "w1") in nope
    assert all({"w1", "i1"} - {c["id"] for c in s["chats"]} for s in org.suggest(chats, set(), nope))
    assert client.post(f"{API}/merges/suggestions/dismiss", json={"chats": ["w1"]}).status_code == 422


def _future(seconds=3600):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_spaeter_senden_anlegen_aendern_loeschen(client):
    from datetime import datetime, timedelta, timezone
    r = client.post(f"{API}/chats/1/schedule", json={"text": "  Guten Morgen  ", "at": _future(), "silent": True, "reply_to": "5"})
    assert r.status_code == 200
    x = r.json()
    assert (x["chat"], x["target"], x["text"], x["status"], x["silent"], x["reply_to"]) == ("1", "1", "Guten Morgen", "pending", True, "5")
    assert any(i["id"] == x["id"] for i in client.get(f"{API}/scheduled").json()["items"])
    # ändern: Zeit und „nur wenn ich nichts schreibe“
    new = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    y = client.patch(f"{API}/scheduled/{x['id']}", json={"at": new, "silent": False}).json()
    assert y["silent"] is False and y["at"] != x["at"]
    # Fehler: Vergangenheit, Ida/WebUntis, leer, fremder Zielchat, Unsinn
    assert client.post(f"{API}/chats/1/schedule", json={"text": "x", "at": "2020-01-01T00:00:00Z"}).status_code == 400
    assert client.post(f"{API}/chats/ida/schedule", json={"text": "x", "at": _future()}).status_code == 400
    assert client.post(f"{API}/chats/1/schedule", json={"text": "   ", "at": _future()}).status_code == 400
    assert client.post(f"{API}/chats/1/schedule", json={"text": "x", "at": _future(), "target": "2"}).status_code == 400
    assert client.post(f"{API}/chats/1/schedule", json={"text": "x", "at": "morgen"}).status_code == 400
    assert client.delete(f"{API}/scheduled/{x['id']}").status_code == 200
    assert not any(i["id"] == x["id"] for i in client.get(f"{API}/scheduled").json()["items"])
    assert client.delete(f"{API}/scheduled/{x['id']}").status_code == 404


def test_spaeter_senden_takt(client, monkeypatch):
    """Der Takt sendet Fälliges; mit „nur wenn ich nichts schreibe“ nur, wenn man bis dahin nicht selbst geschrieben hat."""
    import asyncio
    import time
    from app import api, schedule
    sent, mine = [], []

    async def fake_send(chat_id, body):
        if body.text == "kaputt":
            raise api.HTTPException(502, "Beeper nicht erreichbar")
        sent.append((chat_id, body.text, body.reply_to))
        return {"ok": True}

    async def fake_fetch(chat_id, cursor, direction):
        return {"items": mine}
    monkeypatch.setattr(api, "send", fake_send)
    monkeypatch.setattr(api, "fetch_messages", fake_fetch)

    def make(text, silent=False, secs=120):
        r = client.post(f"{API}/chats/1/schedule", json={"text": text, "at": _future(secs), "silent": silent}).json()
        return r["id"], schedule._get(r["id"])["due"]

    def status(sid):
        return next((i for i in client.get(f"{API}/scheduled").json()["items"] if i["id"] == sid), None)

    # 1) normal: gesendet, danach nicht mehr in der Liste (ist jetzt eine echte Nachricht)
    a, due = make("Hallo")
    asyncio.run(schedule.run_due(now=due - 10))
    assert sent == [] and status(a)["status"] == "pending"   # noch nicht fällig
    asyncio.run(schedule.run_due(now=due + 1))
    assert sent == [("1", "Hallo", None)] and status(a) is None
    # 2) nur wenn ich nichts schreibe – nichts dazwischen: gesendet; Eigenes von VOR dem Planen und Reaktionen zählen nicht
    mine[:] = [{"isSender": True, "type": "TEXT", "text": "alt", "timestamp": "2020-01-01T00:00:00.000Z"},
               {"isSender": True, "type": "REACTION", "timestamp": "2099-01-01T00:00:00.000Z"}]
    b, due = make("Ruhig", silent=True)
    asyncio.run(schedule.run_due(now=due + 1))
    assert sent[-1][1] == "Ruhig"
    # 3) selbst geschrieben, nachdem geplant wurde: nicht gesendet, bleibt als „übersprungen“ sichtbar
    mine[:] = [{"isSender": True, "type": "TEXT", "text": "bin schon da", "timestamp": "2099-01-01T00:00:00.000Z"}]
    n = len(sent)
    c, due = make("Zu spät", silent=True)
    asyncio.run(schedule.run_due(now=due + 1))
    assert len(sent) == n and status(c)["status"] == "skipped" and "selbst geschrieben" in status(c)["note"]
    #    … und „Jetzt senden“ geht trotzdem
    assert client.post(f"{API}/scheduled/{c}/send").status_code == 200 and sent[-1][1] == "Zu spät" and status(c) is None
    # 4) Dienst war aus: viel zu spät → verpasst, nicht mehr gesendet
    d, due = make("Viel zu spät")
    n = len(sent)
    asyncio.run(schedule.run_due(now=due + schedule.GRACE_S + 60))
    assert len(sent) == n and status(d)["status"] == "missed"
    #    neu planen setzt es wieder auf „steht aus“
    assert client.patch(f"{API}/scheduled/{d}", json={"at": _future(600)}).json()["status"] == "pending"
    # 5) Senden schlägt fehl: noch zweimal versucht, dann „fehlgeschlagen“
    e, due = make("kaputt")
    now = due + 1
    for _ in range(schedule.MAX_TRIES):
        asyncio.run(schedule.run_due(now=now))
        now += schedule.RETRY_S + 1
    assert status(e)["status"] == "failed" and "Beeper nicht erreichbar" in status(e)["note"]
    for sid in (d, e):
        client.delete(f"{API}/scheduled/{sid}")


def test_automatisch_gelesen_pro_app(client):
    from app import push
    sms = "gmessages.l8tenever@gmail.com/1782000966145"   # so steht SMS (Google Messages) in Beeper
    assert client.put("/api/v1/nets/gmessages/autoread", json={"mode": "bogus"}).status_code == 400
    assert client.put("/api/v1/nets/Evil!/autoread", json={"mode": "open"}).status_code == 400
    assert client.put("/api/v1/nets/gmessages/autoread", json={"mode": "always"}).status_code == 200
    assert client.get("/api/v1/autoread").json()["nets"] == {"gmessages": "always"}
    assert push.autoread_of("c1", network=sms) == "always"          # App-Regel greift …
    assert push.autoread_of("c1", network="whatsapp") == ""         # … nur für diese App
    assert push.autoread_of("c1") == ""                             # ohne Netzwerk nichts
    assert client.put("/api/v1/chats/c1/autoread", json={"mode": "off"}).status_code == 200
    assert push.autoread_of("c1", network=sms) == ""                # "off" am Chat schlägt die App-Regel
    assert client.put("/api/v1/chats/c1/autoread", json={"mode": "open"}).status_code == 200
    assert push.autoread_of("c1", network=sms) == "open"            # eigene Einstellung schlägt die App-Regel
    client.put("/api/v1/chats/c1/autoread", json={"mode": ""})
    assert push.autoread_of("c1", network=sms) == "always"          # zurück auf „wie alle“
    client.post("/api/v1/merges", json={"chats": [{"id": "c1", "title": "A", "network": "gmessages"}, {"id": "c2", "network": "whatsapp"}]})
    assert push.autoread_of("c1", network=sms) == ""                # zusammengeführte Chats folgen keiner App-Regel
    client.delete("/api/v1/merges/c1")
    client.put("/api/v1/nets/gmessages/autoread", json={"mode": ""})
    assert client.get("/api/v1/autoread").json()["nets"] == {}


def test_beeper_sammelchat_netzwerke(client, monkeypatch):
    """Beeper-Sammelchat hat selbst kein Netzwerk („Chat“): es kommen die Konten der Chats dahinter, ein normaler Chat bleibt unberührt."""
    from app import api
    import asyncio

    class R:
        def __init__(self, d, code=200):
            self._d, self.status_code = d, code

        def json(self):
            return self._d

    chats = {"66675": {"accountID": "matrix", "merge": {"chatIDs": ["!i:x", "!w:x", "!i2:x"]}},
             "!i:x": {"accountID": "instagramgo.1"}, "!w:x": {"accountID": "whatsapp.2"}, "!i2:x": {"accountID": "instagramgo.1"}}

    async def rest_get(path, **params):
        d = chats.get(path.rsplit("/", 1)[1].replace("%21", "!").replace("%3A", ":"))
        return R(d) if d is not None else R({}, 404)
    monkeypatch.setattr(api, "rest_get", rest_get)
    api._nets_cache.clear()

    items = [{"id": "66675", "network": ""}, {"id": "5", "network": "whatsapp.2"}, {"id": "66637", "network": ""}, {"id": "ida", "network": ""}]
    out = asyncio.run(api.fill_nets(items))
    assert [c["network"] for c in out] == ["instagramgo.1|whatsapp.2", "whatsapp.2", "", ""]   # doppelte Konten einmal, Unbekanntes bleibt leer


def test_bearbeitet_zeigt_fruehere_fassungen(client, fake):
    from app.api import keep_deleted
    m = {"id": "e1", "at": "2026-11-12T10:00:00Z", "text": "Eins", "files": [], "deleted": False, "edited": False}
    keep_deleted("c8", [dict(m)])
    assert keep_deleted("c8", [{**m, "text": "Zwei", "edited": True}])[0]["prev"] == ["Eins"]
    out = keep_deleted("c8", [{**m, "text": "Drei", "edited": True}])[0]
    assert out["prev"] == ["Eins", "Zwei"]
    assert keep_deleted("c8", [{**m, "text": "Drei", "edited": True}])[0]["prev"] == ["Eins", "Zwei"]


def test_beeper_sammelchat_wird_aufgeteilt(client, monkeypatch):
    """Ein Sammelchat von Beeper (Instagram + WhatsApp in einem) erscheint als die Chats dahinter, jeder mit eigenem Netzwerk;
    Gruppen, die ihn noch enthalten, bekommen an seiner Stelle die Chats dahinter."""
    import asyncio
    from app import api, store
    asked = []

    class R:
        def __init__(self, data, code=200):
            self._d, self.status_code = data, code

        def json(self):
            return self._d
    chats = {"9": {"id": "!m:x", "localChatID": "9", "accountID": "matrix", "title": "Nils", "merge": {"chatIDs": ["!ig:x", "!wa:x", "!weg:x"]},
                   "preview": {"id": "50", "chatID": "!wa:x", "text": "Ja", "timestamp": "2026-11-18T16:16:17Z", "isSender": False}},
             "!ig:x": {"id": "!ig:x", "localChatID": "11", "accountID": "instagramgo.1", "title": "Nils", "type": "single", "lastActivity": "2026-07-29T01:41:24Z", "unreadCount": 0},
             "!wa:x": {"id": "!wa:x", "localChatID": "12", "accountID": "whatsapp.49", "title": "Nils Primel", "type": "single", "lastActivity": "2026-11-18T16:16:17Z", "unreadCount": 2}}

    async def rest_get(path, **params):
        asked.append(path)
        d = chats.get(path.rsplit("/", 1)[1].replace("%21", "!").replace("%3A", ":"))
        return R(d) if d else R({}, 404)

    async def msgs(chat_id, cursor, direction):
        return {"items": [{"id": "7", "text": "Hey", "timestamp": "2026-07-29T01:41:24Z"}] if chat_id == "11" else []}
    monkeypatch.setattr(api, "rest_get", rest_get)
    monkeypatch.setattr(api, "fetch_messages", msgs)
    monkeypatch.setattr(api, "_subs_cache", {})
    monkeypatch.setattr(api, "_last_cache", {})
    items = [{"id": "1", "title": "Luka", "network": "snapchat", "type": "single", "at": "2026-11-18T10:00:00Z", "unread": 0},
             {"id": "9", "title": "Nils", "network": "", "type": "single", "at": "2026-11-18T16:16:17Z", "unread": 2}]
    out = asyncio.run(api.split_merged([dict(c) for c in items], with_last=True))
    assert [(c["id"], c["network"], c["unread"]) for c in out] == [("12", "whatsapp.49", 2), ("1", "snapchat", 0), ("11", "instagramgo.1", 0)]
    assert out[0]["last"]["text"] == "Ja" and out[2]["last"]["text"] == "Hey" and out[0]["title"] == "Nils Primel"
    n = len(asked)
    asyncio.run(api.split_merged([dict(c) for c in items]))   # unverändert: aus dem Gedächtnis, keine neuen Aufrufe
    assert len(asked) == n
    # kein Sammelchat (404 / ohne merge): bleibt, wie er ist
    assert asyncio.run(api.split_merged([{"id": "5", "network": "", "at": "", "unread": 0}])) == [{"id": "5", "network": "", "at": "", "unread": 0}]

    # alte Gruppe mit dem Sammelchat als Profilbild und Standard: die Chats dahinter rücken an seine Stelle
    store.merge([{"id": "9", "title": "Nils", "network": ""}, {"id": "30", "title": "Nils", "network": "snapchat"}])
    store.set_meta("9", avatar="9", send="9")
    monkeypatch.setattr(api, "rest_chats", lambda: asyncio.sleep(0, []))
    r = client.get(f"{API}/merges").json()["items"]
    assert [[m["id"] for m in g["chats"]] for g in r] == [["11", "12", "30"]] and r[0]["id"] == "9"
    assert r[0]["avatar"] == "11" and r[0]["send"] == ""
    store.split("9")
