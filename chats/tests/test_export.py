"""Anonymer Export: ganzer Verlauf über mehrere Seiten, Decknamen statt Namen, Antworten als #Nummer."""
import time
from datetime import datetime, timezone

from app import api, export

API = "/api/v1"

PAGES = {
    "": {"items": [
        {"id": "m3", "timestamp": "2026-11-12T10:40:00Z", "senderID": "@luka:x", "senderName": "Luka Berger", "type": "TEXT",
         "text": "Ja Simon, ruf mich an: +49 151 2345678 oder luka@mail.de, https://www.youtube.com/watch?v=abc", "isSender": False,
         "linkedMessageID": "m1", "reactions": [{"reactionKey": "👍", "participantID": "@me:x", "isSender": True}]},
        {"id": "m2", "timestamp": "2026-11-12T10:39:00Z", "senderID": "@luka:x", "senderName": "Luka Berger", "type": "VOICE", "text": "", "isSender": False,
         "attachments": [{"srcURL": "mxc://x/v2", "mimeType": "audio/ogg", "isVoiceNote": True, "duration": 75, "transcription": {"transcription": "Hey Luka hier"}}]},
        {"id": "r1", "timestamp": "2026-11-12T10:39:30Z", "type": "REACTION", "text": "👍", "senderID": "@luka:x"},
        {"id": "s1", "timestamp": "2026-11-12T10:39:10Z", "type": "NOTICE", "text": "Luka Berger left the chat", "senderID": "@luka:x"}],
        "hasMore": True, "oldestCursor": "c1"},
    "c1": {"items": [
        {"id": "m1", "timestamp": "2026-11-10T08:00:00Z", "senderID": "@me:x", "senderName": "Simon Kulbarts", "type": "TEXT", "text": "Treffen morgen?", "isSender": True,
         "isDeleted": False, "attachments": [{"srcURL": "file:///C:/Users/Simon/AppData/Roaming/BeeperTexts/media/a/b.jpg", "mimeType": "image/jpeg", "fileName": "IMG_Simon.jpg"}]},
        {"id": "m0", "timestamp": "2026-11-09T08:00:00Z", "senderID": "@luka:x", "senderName": "Luka Berger", "type": "TEXT", "text": "", "isDeleted": True,
         "linkedMessageID": "uralt"}],
        "hasMore": True, "oldestCursor": "c1"},   # gleicher Cursor noch einmal: muss trotzdem aufhören
}


def run_export(client, monkeypatch, **body):
    calls = []

    async def fake_fetch(chat_id, cursor, direction):
        calls.append(cursor)
        return PAGES[cursor if cursor in PAGES else "c1"]

    async def fake_meta(cid):
        return {"type": "single", "accountID": "whatsapp.4915199999", "title": "Luka Berger",
                "participants": {"items": [{"id": "@me:x", "fullName": "Simon Kulbarts", "isSelf": True}, {"id": "@luka:x", "fullName": "Luka Berger", "username": "lukab"}]}}
    monkeypatch.setattr(api, "fetch_messages", fake_fetch)
    monkeypatch.setattr(export, "chat_meta", fake_meta)
    r = client.post(f"{API}/export", json={"chats": ["1"], **body})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    for _ in range(100):
        st = client.get(f"{API}/export/{jid}").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done", st
    f = client.get(f"{API}/export/{jid}/file")
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"]
    return f, calls


def test_export_text_anonym(client, monkeypatch):
    f, calls = run_export(client, monkeypatch)
    t = f.text
    assert calls == ["", "c1"]   # zwei Seiten, dann Schluss (Cursor wiederholt sich)
    for leak in ("Luka", "Simon", "Kulbarts", "Berger", "lukab", "151", "luka@mail.de", "youtube.com/watch", "4915199999", "C:/Users", "IMG_", "@luka:x", "mxc://"):
        assert leak not in t, leak
    assert "WhatsApp" in t and "4 Nachrichten" in t
    lines = [l for l in t.splitlines() if l.startswith("#")]
    assert lines[0].endswith("User 2 ↩ (auf eine ältere Nachricht): [gelöscht]")
    assert "User 1: [Bild] Treffen morgen?" in lines[1]
    assert "User 2: [Sprachnachricht 1:15] „Hey User 2 hier“" in lines[2]
    assert "User 2 ↩ #2 (User 1: „[Bild] Treffen morgen?“): Ja User 1, ruf mich an: [Nummer] oder [E-Mail], [Link: youtube.com]" in lines[3]
    assert "Reaktionen: 👍 User 1" in t
    assert "left the chat" not in t


def test_export_json_und_ich(client, monkeypatch):
    days = (datetime.now(timezone.utc) - datetime(2026, 11, 10, 8, tzinfo=timezone.utc)).days + 1   # Grenze zwischen m0 und m1
    f, _ = run_export(client, monkeypatch, format="json", me=True, days=days)
    d = f.json()
    ms = d["chats"][0]["messages"]
    assert [m["n"] for m in ms] == [1, 2, 3]   # m0 (09.11.) fällt raus, m1 (10.11.) bleibt
    assert ms[0]["from"] == "Ich" and ms[2]["reply_to"] == 1 and ms[2]["from"] == "User 1"
    assert "Simon" not in f.text and "Luka" not in f.text


def test_export_unbekannt(client):
    assert client.get(f"{API}/export/nix").status_code == 404
    assert client.post(f"{API}/export", json={"chats": []}).status_code == 422
