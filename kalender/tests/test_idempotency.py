"""Idempotenz (Idempotency-Key) und Wiederholung beim Anlegen mit gleicher uid: der Offline-Abgleich darf beliebig oft wiederholen."""
import uuid

from .conftest import API, mk


def key():
    return {"Idempotency-Key": uuid.uuid4().hex}


def count(client, title, day="2035-03-05"):
    return len([e for e in client.get(f"{API}/events", params={"from": day, "to": day}).json()["events"] if e["title"] == title])


def test_post_twice_with_same_key_creates_one_event(client):
    h = key()
    body = {"title": "Einmal", "start": "2035-03-05T10:00"}
    a = client.post(f"{API}/events", json=body, headers=h)
    b = client.post(f"{API}/events", json=body, headers=h)
    assert a.status_code == b.status_code == 200
    assert a.json()["event"]["id"] == b.json()["event"]["id"]
    assert b.headers["idempotent-replay"] == "1" and "idempotent-replay" not in a.headers
    assert count(client, "Einmal") == 1


def test_patch_twice_raises_sequence_only_once(client):
    e = mk(client, "Patch einmal", "2035-03-06T10:00")["event"]
    h = key()
    a = client.patch(f"{API}/events/{e['id']}", json={"title": "Patch zwei"}, headers=h).json()
    b = client.patch(f"{API}/events/{e['id']}", json={"title": "Patch zwei"}, headers=h).json()
    assert a["event"]["sequence"] == b["event"]["sequence"] == 1
    assert client.get(f"{API}/events/{e['id']}").json()["event"]["sequence"] == 1


def test_delete_twice_gives_same_answer_instead_of_404(client):
    e = mk(client, "Delete einmal", "2035-03-07T10:00")["event"]
    h = key()
    a = client.delete(f"{API}/events/{e['id']}", headers=h)
    b = client.delete(f"{API}/events/{e['id']}", headers=h)
    assert a.status_code == b.status_code == 200 and a.json() == b.json()
    assert client.delete(f"{API}/events/{e['id']}", headers=key()).status_code == 404   # ein NEUER Versuch ist wirklich 404


def test_errors_are_remembered_too(client):
    h = key()
    a = client.patch(f"{API}/events/gibtsnicht", json={"title": "x"}, headers=h)
    b = client.patch(f"{API}/events/gibtsnicht", json={"title": "x"}, headers=h)
    assert a.status_code == b.status_code == 404 and a.json() == b.json() and b.headers["idempotent-replay"] == "1"
    bad = {"title": "", "start": "2035-03-05T10:00"}
    h2 = key()
    assert client.post(f"{API}/events", json=bad, headers=h2).status_code == 400
    assert client.post(f"{API}/events", json=bad, headers=h2).status_code == 400
    assert count(client, "") == 0


def test_failed_request_leaves_no_half_changes(client):
    """Schlägt eine Anfrage fehl, wird nichts davon gespeichert – auch nicht teilweise."""
    before = len(client.get(f"{API}/changes", params={"limit": 500}).json()["changes"])
    h = key()
    client.post(f"{API}/events", json={"title": "Halb", "start": "2035-03-05T10:00", "alarms": ["kaputt"]}, headers=h)
    assert len(client.get(f"{API}/changes", params={"limit": 500}).json()["changes"]) == before
    assert count(client, "Halb") == 0


def test_same_key_other_request_is_rejected(client):
    h = key()
    client.post(f"{API}/events", json={"title": "Schlüssel A", "start": "2035-03-08T10:00"}, headers=h)
    r = client.post(f"{API}/events", json={"title": "Schlüssel B", "start": "2035-03-08T11:00"}, headers=h)
    assert r.status_code == 200 and r.json()["event"]["title"] == "Schlüssel A"   # gleiche Anfrage (Pfad+Methode) = gleiche Antwort
    e = mk(client, "Anderer Pfad", "2035-03-08T12:00")["event"]
    assert client.patch(f"{API}/events/{e['id']}", json={"title": "x"}, headers=h).status_code == 422


def test_without_key_nothing_changes(client):
    body = {"title": "Ohne Schlüssel", "start": "2035-03-09T10:00"}
    client.post(f"{API}/events", json=body)
    client.post(f"{API}/events", json=body)
    assert count(client, "Ohne Schlüssel", "2035-03-09") == 2


def test_live_event_is_announced_once_for_a_replay(client, agent):
    ida = agent("ida")
    h = {**key(), "X-Kalender-By": "ida"}
    for _ in range(2):
        ida.post(f"{API}/events", json={"title": "Einmal angekündigt", "start": "2035-03-10T10:00"}, headers=h)
    ch = [c for c in client.get(f"{API}/changes", params={"by": "ida", "limit": 50}).json()["changes"] if c["title"] == "Einmal angekündigt"]
    assert len(ch) == 1
    ida.close()


# ---------- Paket 10: gleiche uid, DELETE mit sequence ----------
def test_post_with_existing_uid_returns_the_same_event(client):
    uid = f"{uuid.uuid4()}@ida-kalender"
    a = client.post(f"{API}/events", json={"title": "Mit uid", "start": "2035-04-01T10:00", "uid": uid}).json()
    b = client.post(f"{API}/events", json={"title": "Mit uid anders", "start": "2035-04-02T10:00", "uid": uid}).json()
    assert "existing" not in a and b["existing"] is True and b["event"]["id"] == a["event"]["id"] and b["event"]["title"] == "Mit uid"
    assert count(client, "Mit uid", "2035-04-01") == 1 and count(client, "Mit uid anders", "2035-04-02") == 0
    assert b["change_id"] is None


def test_existing_uid_in_trash_is_reported_not_restored(client):
    uid = f"{uuid.uuid4()}@ida-kalender"
    a = client.post(f"{API}/events", json={"title": "Im Korb", "start": "2035-04-03T10:00", "uid": uid}).json()["event"]
    client.delete(f"{API}/events/{a['id']}")
    b = client.post(f"{API}/events", json={"title": "Im Korb", "start": "2035-04-03T10:00", "uid": uid}).json()
    assert b["existing"] and b["deleted"] is True
    assert count(client, "Im Korb", "2035-04-03") == 0


def test_delete_with_old_sequence_conflicts(client):
    e = mk(client, "Delete mit Sequenz", "2035-04-04T10:00")["event"]
    client.patch(f"{API}/events/{e['id']}", json={"location": "Neu"})
    r = client.delete(f"{API}/events/{e['id']}", params={"sequence": 0})
    assert r.status_code == 409 and r.json()["server"]["location"] == "Neu" and r.json()["server"]["sequence"] == 1
    assert count(client, "Delete mit Sequenz", "2035-04-04") == 1
    assert client.delete(f"{API}/events/{e['id']}", params={"sequence": 1}).status_code == 200
    assert count(client, "Delete mit Sequenz", "2035-04-04") == 0


def test_old_requests_are_cleaned_up(client):
    h = key()
    client.post(f"{API}/events", json={"title": "Aufräumen", "start": "2035-04-05T10:00"}, headers=h)
    assert client.post(f"{API}/_test/maintenance").status_code == 200   # frische Einträge bleiben
    again = client.post(f"{API}/events", json={"title": "Aufräumen", "start": "2035-04-05T10:00"}, headers=h)
    assert again.headers.get("idempotent-replay") == "1"


def test_upload_with_the_same_key_creates_one_file(client):
    ev = mk(client, "Anhang-Idem", "2033-03-01T10:00")["event"]
    h = {"Idempotency-Key": "upload-key-0001"}
    one = client.post(f"{API}/events/{ev['id']}/files", files={"file": ("Plan.pdf", b"eins", "application/pdf")}, headers=h)
    two = client.post(f"{API}/events/{ev['id']}/files", files={"file": ("Plan.pdf", b"eins", "application/pdf")}, headers=h)
    assert one.status_code == two.status_code == 200 and two.headers.get("idempotent-replay") == "1"
    assert one.json() == two.json() and len(client.get(f"{API}/events/{ev['id']}/files").json()["files"]) == 1
