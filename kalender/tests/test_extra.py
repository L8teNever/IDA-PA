"""Anhänge, QR-Code, „Meintest du“, Web Push (Abo-Verwaltung) und „Ida fragen“."""
from .conftest import API, mk


def test_attachments_upload_list_download_delete(client, agent):
    ev = mk(client, "Mit Anhang", "2033-01-10T10:00")["event"]
    r = client.post(f"{API}/events/{ev['id']}/files", files={"file": ("Plan ../böse.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert "/" not in name and ".." not in name
    again = client.post(f"{API}/events/{ev['id']}/files", files={"file": ("Plan ../böse.pdf", b"zwei", "application/pdf")}).json()
    assert len(again["files"]) == 2 and again["name"] != name                       # gleicher Name → „(2)“
    assert [f["name"] for f in client.get(f"{API}/events/{ev['id']}/files").json()["files"]] == [name, again["name"]]
    assert client.get(f"{API}/files/{ev['event_id']}/{name}").content == b"%PDF-1.4 test"
    assert client.delete(f"{API}/files/{ev['event_id']}/{name}").json()["files"] == [{"name": again["name"], "size": 4}]
    assert client.get(f"{API}/files/{ev['event_id']}/{name}").status_code == 404
    assert client.get(f"{API}/files/..%2F..%2Fetc/passwd").status_code == 404


def test_attachments_respect_agent_rights(client, agent):
    cal = client.post(f"{API}/calendars", json={"name": "Nur lesen", "color": "gruen", "agent_access": "read"}).json()
    ev = mk(client, "Geheim-ish", "2033-01-11T10:00", calendar_id=cal["id"])["event"]
    ida = agent()
    assert ida.get(f"{API}/events/{ev['id']}/files").status_code == 200
    assert ida.post(f"{API}/events/{ev['id']}/files", files={"file": ("x.txt", b"x")}).status_code == 403


def test_feed_qr_is_svg(client, agent):
    r = client.get(f"{API}/feed/qr.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"<svg" in r.content
    assert agent().get(f"{API}/feed/qr.svg").status_code == 403


def test_search_did_you_mean(client):
    mk(client, "Kieferorthopäde", "2033-02-01T09:00")
    r = client.get(f"{API}/search", params={"q": "kieferortopäde"}).json()
    assert r["total"] == 0 and r["did_you_mean"] == "Kieferorthopäde"
    assert client.get(f"{API}/search", params={"q": "qqqqqqqq"}).json()["did_you_mean"] is None


def test_push_keys_and_devices(client, agent):
    s = client.get(f"{API}/push").json()
    assert len(s["public_key"]) > 60 and s["devices"] == []
    assert client.get(f"{API}/push").json()["public_key"] == s["public_key"]          # bleibt gleich
    sub = {"endpoint": "https://push.example.invalid/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert client.post(f"{API}/push/subscribe", json={"subscription": sub, "label": "Handy"}).json()["devices"] == 1
    assert client.post(f"{API}/push/subscribe", json={"subscription": sub, "label": "Handy"}).json()["devices"] == 1  # kein Doppel
    assert client.post(f"{API}/push/subscribe", json={"subscription": {"endpoint": "http://x"}}).status_code == 400
    assert agent().post(f"{API}/push/subscribe", json={"subscription": sub}).status_code == 403
    assert "vapid_private" not in client.get(f"{API}/settings").text                      # Schlüssel bleiben drin
    assert client.post(f"{API}/push/unsubscribe", json={"endpoint": sub["endpoint"]}).json()["devices"] == 0


def test_ask_without_ida_connection(client):
    r = client.post(f"{API}/ask", json={"text": "Wann war ich zuletzt beim Zahnarzt?"})
    assert r.status_code == 400 and "Ida" in r.json()["detail"]
    assert client.get(f"{API}/meta").json()["ask"] is False
