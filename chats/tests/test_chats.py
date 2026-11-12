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
    assert att == {"uploadID": "up1", "type": "voice-note", "mimeType": "audio/ogg", "fileName": "Sprachnachricht.ogg", "duration": 4.2}
    assert b"OggSwebmdata" in fake.rest[-2][2]["raw"]
    assert client.post(f"{API}/chats/1/upload", files={"file": ("x", b"", "text/plain")}).status_code == 400


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
    assert calls == [("/v1/chats/7/read", {"messageID": "2"}), ("/v1/chats/8/read", {"messageID": "2"}),
                     ("/v1/focus", {"chatID": "7", "messageID": "2"}), ("/v1/focus", {"chatID": "5"})]   # WhatsApp: nur gelesen
