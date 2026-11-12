"""IDA Mail: Ordner, Liste, Lesen, Anhänge, Senden/Antworten/Entwürfe, Aktionen, live, Oberfläche."""
import asyncio
import base64
import email
from email import policy

from .fake_gmail import BIG_TEXT

API = "/api/v1"


def test_token_kommt_vom_google_dienst_und_wird_erneuert(client, fake):
    from app import api
    api.gmail()._token = ""
    fake.tokens = 0
    fake.expire_once = True
    me = client.get(f"{API}/me").json()
    assert me["email"] == "simon@example.com" and [a["email"] for a in me["send_as"]] == ["simon@example.com", "info@kulbarts.com"]
    assert fake.last("/api/v1/token/gmail")["auth"] == "Bearer geheim"
    assert fake.tokens == 2  # abgelaufener Token: einmal neu geholt


def test_nicht_verbunden(client, fake):
    from app import api
    api.gmail()._token = ""
    fake.connected = False
    r = client.get(f"{API}/labels")
    assert r.status_code == 409 and "Einstellungen → Google" in r.json()["detail"]


def test_ordner_mit_ungelesenen(client):
    labels = client.get(f"{API}/labels").json()
    assert [l["id"] for l in labels] == ["INBOX", "SENT", "TRASH", "Label_1"]
    assert labels[0]["name"] == "Posteingang" and labels[0]["unread"] == 3 and labels[3]["name"] == "Schule" and labels[3]["color"] == "#16a765"


def test_liste(client, fake):
    d = client.get(f"{API}/threads", params={"label": "INBOX", "q": "from:lea"}).json()
    q = fake.last("/threads", "GET")["query"]
    assert q["labelIds"] == "INBOX" and q["q"] == "from:lea" and d["next"] == "p2"
    t = d["items"][0]
    assert t["subject"] == "Projektarbeit" and t["from"] == ["Lea Lehrer", "Ich"] and t["unread"] and t["starred"] and t["count"] == 2
    assert client.get(f"{API}/threads", params={"label": "../x"}).status_code == 400


def test_lesen(client):
    t = client.get(f"{API}/threads/t1").json()
    m1, m2 = t["messages"]
    assert "Grüße" in m1["html"] and m1["inline"] == {"logo1": "alogo"}  # Zeichensatz aus dem Content-Type
    assert [a["name"] for a in m1["attachments"]] == ["Themenblatt.pdf"]  # Inline-Bild ist kein Anhang
    assert m1["from"] == {"name": "Lea Lehrer", "email": "lea@schule.de"} and m1["message_id"] == "<m1@schule.de>"
    assert m2["text"] == BIG_TEXT  # großer Textteil wird nachgeladen
    assert [p["email"] for p in m2["cc"]] == ["max@x.de", "mia@x.de"] and t["unread"] and t["starred"]


def test_anhang(client):
    r = client.get(f"{API}/messages/m1/attachments/apdf", params={"name": "Themenblatt.pdf", "type": "application/pdf"})
    assert r.content == b"%PDF-1.4 fake" and r.headers["content-disposition"].startswith("inline")
    r = client.get(f"{API}/messages/m1/attachments/apdf", params={"name": "x.html", "type": "text/html"})
    assert r.headers["content-disposition"].startswith("attachment")  # fremdes HTML nie auf unserer Adresse anzeigen
    assert client.get(f"{API}/messages/m1/attachments/a%2Fb").status_code in (400, 404)


def raw_of(fake) -> email.message.EmailMessage:
    body = fake.last("/messages/send")["body"]
    return email.message_from_bytes(base64.urlsafe_b64decode(body["raw"] + "=="), policy=policy.default)


def test_antworten_mit_anhang(client, fake):
    r = client.post(f"{API}/send", data={"to": "Lea <lea@schule.de>", "subject": "Re: Projektarbeit", "text": "Danke!\n\n> alt",
                                        "thread_id": "t1", "in_reply_to": "<m1@schule.de>", "references": "<m0@x>", "sender": "Simon <simon@example.com>"},
                    files=[("files", ("notiz.txt", b"hallo", "text/plain"))])
    assert r.json()["thread_id"] == "t1" and fake.last("/messages/send")["body"]["threadId"] == "t1"
    m = raw_of(fake)
    assert m["To"] == "Lea <lea@schule.de>" and m["In-Reply-To"] == "<m1@schule.de>" and m["References"] == "<m0@x> <m1@schule.de>"
    assert m["From"] == "Simon <simon@example.com>"
    parts = list(m.iter_attachments())
    assert parts[0].get_filename() == "notiz.txt" and parts[0].get_content() == "hallo"


def test_weiterleiten_nimmt_anhaenge_mit(client, fake):
    client.post(f"{API}/send", data={"to": "max@x.de", "subject": "WG: Projektarbeit", "forward_from": "m1"})
    assert [p.get_filename() for p in raw_of(fake).iter_attachments()] == ["Themenblatt.pdf"]
    client.post(f"{API}/send", data={"to": "max@x.de", "subject": "WG", "forward_from": "m1", "forward_skip": "apdf"})
    assert list(raw_of(fake).iter_attachments()) == []


def test_pruefungen_beim_senden(client):
    assert client.post(f"{API}/send", data={"subject": "x"}).status_code == 400
    assert client.post(f"{API}/send", data={"to": "a@b.de\nBcc: evil@x.de"}).status_code == 400


def test_entwurf_und_senden_loescht_ihn(client, fake):
    d = client.post(f"{API}/send", data={"to": "", "subject": "Halb fertig", "draft": "true"}).json()
    assert d["draft_id"] == "d1"
    client.post(f"{API}/send", data={"to": "a@b.de", "subject": "Halb fertig", "draft": "true", "draft_id": "d1"})
    assert fake.last("/drafts/d1", "PUT")
    client.post(f"{API}/send", data={"to": "a@b.de", "subject": "Fertig", "draft_id": "d1"})
    assert fake.last("/drafts/d1", "DELETE")


def test_aktionen(client, fake):
    assert client.post(f"{API}/threads/t1/modify", json={"remove": ["INBOX"]}).json()["ok"]
    assert fake.last("/threads/t1/modify")["body"] == {"addLabelIds": [], "removeLabelIds": ["INBOX"]}
    client.post(f"{API}/threads/t1/trash")
    client.post(f"{API}/threads/t1/trash", params={"undo": "true"})
    assert fake.last("/threads/t1/untrash")
    assert client.post(f"{API}/threads/t1/modify", json={"add": ["a b"]}).status_code == 400


def test_live_meldet_neue_mails(client, fake, monkeypatch):
    from app import api, bus, live
    evts = []
    monkeypatch.setattr(bus, "publish", evts.append)        # mitschreiben, ohne als offenes Fenster zu zählen
    monkeypatch.setattr(api, "_gmail", None)                 # eigene Verbindung für diese Event-Schleife

    async def go():
        state = {}
        await live.check(state)
        assert state["history"] == "100" and not evts        # erster Lauf merkt sich nur den Stand
        fake.history = [{"messagesAdded": [{"message": {"id": "m9", "threadId": "t9", "labelIds": ["INBOX", "UNREAD"]}}]}]
        await live.check(state)
        assert {"type": "new", "thread": "t9", "from": "Paul", "subject": "Neu!"} in evts and evts[-1] == {"type": "changed"}
        assert state["history"] == "101"
        evts.clear()
        state["history"] = "1"                               # zu alt: neu anfangen
        await live.check(state)
        assert state["history"] == "" and evts == [{"type": "changed"}]
        await api.gmail().close()
    try:
        asyncio.run(go())
    finally:
        fake.history = []


def test_oberflaeche(client):
    page = client.get("/")
    assert page.status_code == 200 and "<title>Mail</title>" in page.text
    for ref in [h.split('"', 1)[0] for h in page.text.split('href="')[1:] + page.text.split('src="')[1:]]:
        if ref.startswith("/s/") or ref.startswith("/icons/"):
            assert client.get(ref).status_code == 200, ref
    js = page.text.split('<script type="module" src="', 1)[1].split('"', 1)[0]
    assert client.get(js.rsplit("/", 1)[0] + "/shared/js/dom.js").status_code == 200   # import "./shared/js/dom.js"
    m = client.get("/manifest.webmanifest").json()
    assert m["name"] == "IDA Mail" and all(client.get(i["src"]).status_code == 200 for i in m["icons"])
    assert client.get("/sw.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/api/v1/gibtsnicht").status_code == 404


def test_adressvorschlaege(client):
    r = client.get(f"{API}/contacts")
    assert r.status_code == 200
    mails = {c["email"]: c for c in r.json()}
    assert mails["p@x.de"]["name"] == "Paul" and mails["lena@x.de"]["name"] == "Lena Müller"
    assert all(c["email"].lower() != "simon@example.com" for c in r.json())   # eigene Adresse nicht vorschlagen
    assert client.get("/t/abc").status_code == 200 and client.get("/l/INBOX").status_code == 200 and client.get("/compose").status_code == 200   # Pfade laden die App
