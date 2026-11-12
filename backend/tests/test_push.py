"""Nachrichten-App: Web Push (Verschlüsselung nach RFC 8291, VAPID), Geräte, Push bei Nachrichten an dich, eigene Adresse."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.services.push import _hkdf, b64u, encrypt, unb64u, vapid_header
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .conftest import chat, wait

API = "/api/v1"


def test_encryption_matches_rfc8291():
    """Das Beispiel aus RFC 8291, Anhang A – Byte für Byte."""
    server = ec.derive_private_key(int.from_bytes(unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    out = encrypt(b"When I grow up, I want to be a watermelon",
                  "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4", "BTBZMqHH6r4Tts7J_aSIgg",
                  salt=unb64u("DGv6ra1nlYgDCS1FRnbzlw"), server_key=server)
    assert b64u(out) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
                         "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_vapid_header_is_a_valid_es256_jwt():
    key = ec.generate_private_key(ec.SECP256R1())
    h = vapid_header(key, "https://fcm.googleapis.com/fcm/send/abc")
    t, k = h.removeprefix("vapid t=").split(", k=")
    head, claims, sig = t.split(".")
    assert json.loads(unb64u(claims))["aud"] == "https://fcm.googleapis.com"
    raw = unb64u(sig)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(k))
    pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


class Device:
    """Ein Browser: eigener Schlüssel + ein Ersatz für den Push-Dienst, der mitschreibt und entschlüsselt."""

    def __init__(self, status=201):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        self.got: list[dict] = []
        self.headers: list[dict] = []
        me = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["content-length"]))
                me.headers.append(dict(self.headers))
                me.got.append(json.loads(me.decrypt(body)))
                self.send_response(status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.endpoint = f"http://127.0.0.1:{self.srv.server_address[1]}/push/{id(self)}"

    @property
    def public(self) -> bytes:
        return self.key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def sub(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64u(self.public), "auth": b64u(self.auth)}, "device": "Test-Handy"}

    def decrypt(self, body: bytes) -> bytes:
        salt, n = body[:16], body[20]
        as_public, ct = body[21:21 + n], body[21 + n:]
        shared = self.key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ikm = _hkdf(self.auth, shared, b"WebPush: info\x00" + self.public + as_public, 32)
        plain = AESGCM(_hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(_hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), ct, None)
        return plain.rstrip(b"\x00")[:-1]

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def device(client):
    d = Device()
    yield d
    client.post(f"{API}/push/unsubscribe", json={"endpoint": d.endpoint})
    d.close()


def test_subscribe_and_test_push(client, device):
    key = client.get(f"{API}/push/key").json()["key"]
    assert len(unb64u(key)) == 65 and client.get(f"{API}/push/key").json()["key"] == key  # bleibt gleich
    assert client.post(f"{API}/push/subscribe", json={"endpoint": "ftp://x", "keys": {"p256dh": "a", "auth": "b"}}).status_code == 400
    assert client.post(f"{API}/push/subscribe", json=device.sub()).json()["ok"]
    client.post(f"{API}/push/subscribe", json=device.sub())  # zweimal = ein Gerät
    assert [d["device"] for d in client.get(f"{API}/push/devices").json()].count("Test-Handy") == 1
    r = client.post(f"{API}/push/test").json()
    assert r["sent"] >= 1
    assert device.got[0]["title"] == "Test" and "Push klappt" in device.got[0]["body"]
    h = {k.lower(): v for k, v in device.headers[0].items()}
    assert h["content-encoding"] == "aes128gcm" and h["urgency"] == "high" and h["authorization"].startswith("vapid t=")
    assert h["authorization"].endswith(", k=" + key)


def test_message_to_you_is_pushed(client, device):
    client.post(f"{API}/push/subscribe", json=device.sub())
    chat(client, agent_id="lea", text='TOOL notify_user {"text": "Paket ist da", "title": "Post", "priority": "high"}')
    got = wait(lambda: [g for g in device.got if g.get("body") == "Paket ist da"])
    assert got[0]["title"] == "Post" and got[0]["priority"] == "high" and got[0]["from"] == "Lea"
    inbox = client.get(f"{API}/messages/inbox").json()
    m = next(x for x in inbox["items"] if x["id"] == got[0]["id"])
    assert m["from_name"] == "Lea" and m["from_palette"] and not m["read"] and inbox["unread"] >= 1
    assert all(x["to"] == "user" for x in inbox["items"])
    older = client.get(f"{API}/messages/inbox?before={m['id']}").json()["items"]
    assert all(x["id"] != m["id"] for x in older)


def test_no_emojis_but_an_icon(client, device):
    """Nachrichten-App und Pop-ups: Emojis fliegen raus, dafür kommt ein passendes Material-Icon."""
    from app.services.push import no_emoji
    assert no_emoji("☑ Gesicht waschen") == "Gesicht waschen" and no_emoji("Lakers 🏀 heute → 02:00 👍🏽") == "Lakers heute → 02:00"
    client.post(f"{API}/push/subscribe", json=device.sub())
    chat(client, agent_id="lea", text='TOOL notify_user {"text": "Alles klar ✅ 🎉", "title": "☑ Zähne putzen"}')
    got = wait(lambda: [g for g in device.got if g.get("title") == "Zähne putzen"])
    assert got[0]["body"] == "Alles klar"
    m = next(x for x in client.get(f"{API}/messages/inbox").json()["items"] if x["id"] == got[0]["id"])
    assert m["title"] == "Zähne putzen" and m["text"] == "Alles klar" and m["icon"] == "task_alt"
    client.post(f"{API}/messages/read-all")  # keine ungelesenen Reste für andere Tests


def test_gone_device_is_removed(client):
    d = Device(status=410)
    try:
        client.post(f"{API}/push/subscribe", json=d.sub())
        r = client.post(f"{API}/push/test").json()
        assert r["removed"] >= 1
        again = client.post(f"{API}/push/test").json()
        assert again["devices"] == r["devices"] and again.get("removed", 0) == 0
    finally:
        d.close()


def test_messages_app_and_own_address(client):
    page = client.get("/m/")
    assert page.status_code == 200 and "Nachrichten" in page.text and "/messages/app.js" in page.text
    assert client.get("/m", follow_redirects=False).headers["location"] == "/m/"
    # eigene Adresse: / ist die Nachrichten-App, die API bleibt, wo sie ist
    host = {"host": "messages.kulbarts.com"}
    root = client.get("/", headers=host)
    assert "/messages/app.js" in root.text
    assert "showNotification" in client.get("/sw.js", headers=host).text
    man = client.get("/manifest.webmanifest", headers=host).json()
    assert man["short_name"] == "Nachrichten" and man["start_url"] == "./"
    assert all(i["src"].startswith("/static/messages/icons/") for i in man["icons"])
    for i in man["icons"]:
        assert client.get(i["src"], headers=host).status_code == 200
    # IDA-Design: dieselben Grundlagen wie IDA (tokens/components) und IDAs Bausteine im Skript
    css = [h.split('"', 1)[0] for h in page.text.split('rel="stylesheet" href="')[1:]]
    assert any(c.endswith("/css/tokens.css") for c in css) and all(client.get(c, headers=host).status_code == 200 for c in css)
    assert client.get(f"{API}/messages/inbox", headers=host).status_code == 200
    asset = page.text.split('src="', 1)[1].split('"', 1)[0]
    assert client.get(asset, headers=host).status_code == 200
    # die normale App bleibt unverändert
    assert "/messages/app.js" not in client.get("/").text
