"""Smart Boards: anlegen, von Ida befüllen lassen, neu laden, ändern, löschen."""
import time

from conftest import API


def _wait(client, bid: str) -> dict:
    for _ in range(100):
        b = client.get(f"{API}/boards/{bid}").json()
        if b["status"] != "running":
            return b
        time.sleep(0.1)
    raise AssertionError("Board bleibt auf „läuft“")


def test_board_lifecycle(client, fake_ida):
    assert client.post(f"{API}/boards", json={"prompt": "  "}).status_code == 400
    m = client.get(f"{API}/boards/models").json()
    assert m["model_names"] == {"m1": "Modell 1"} and "geheim" not in m

    made = client.post(f"{API}/boards", json={"prompt": "Alles zur Projektarbeit", "model": "m1"})
    assert made.status_code == 200, made.text
    bid = made.json()["id"]
    assert made.json()["title"] == "Alles zur Projektarbeit"
    b = _wait(client, bid)
    assert b["status"] == "idle" and b["content"].startswith("## Stand") and b["run_count"] == 1 and b["last_run_at"]
    first = fake_ida.received[-1]
    assert first["agent_id"] == "ida" and first["model"] == "m1" and "Alles zur Projektarbeit" in first["text"]
    assert "boardchat1" in "".join(fake_ida.deleted)

    assert client.post(f"{API}/boards/{bid}/refresh").json()["ok"]
    b2 = _wait(client, bid)
    assert b2["run_count"] == 2
    assert "<<<BOARD" in fake_ida.received[-1]["text"] and "## Stand" in fake_ida.received[-1]["text"]

    assert client.patch(f"{API}/boards/{bid}", json={"title": "PA", "model": None}).json()["title"] == "PA"
    assert [x["id"] for x in client.get(f"{API}/boards").json()["boards"]] == [bid]
    assert client.delete(f"{API}/boards/{bid}").status_code == 200
    assert client.get(f"{API}/boards/{bid}").status_code == 404


def test_boards_only_for_user(agent_client):
    assert agent_client("main").get(f"{API}/boards").status_code == 403


def test_board_inhalt_und_dateien(client, fake_ida):
    bid = client.post(f"{API}/boards", json={"prompt": "Dateien testen"}).json()["id"]
    _wait(client, bid)
    # Aufgabe abhaken = Inhalt von Hand ändern
    assert client.patch(f"{API}/boards/{bid}", json={"content": "## Aufgaben\n- [x] erledigt\n"}).json()["content"].startswith("## Aufgaben")
    # Bild hochladen, anzeigen, löschen
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
    up = client.post(f"{API}/boards/{bid}/files", files={"file": ("../../böse name?.png", png, "image/png")})
    assert up.status_code == 200, up.text
    f = up.json()["files"][0]
    assert f["image"] and "/" not in f["name"] and f["name"].endswith(".png")
    got = client.get(f["url"])
    assert got.status_code == 200 and got.content == png and got.headers["content-type"] == "image/png"
    assert [x["name"] for x in client.get(f"{API}/boards/{bid}").json()["files"]] == [f["name"]]
    # Ida erfährt beim Neu laden die Dateinamen
    client.post(f"{API}/boards/{bid}/refresh")
    _wait(client, bid)
    assert f["name"] in fake_ida.received[-1]["text"] and "Wähle den Aufbau frei" in fake_ida.received[-1]["text"]
    # Nicht-Bilder werden nie inline ausgeliefert
    client.post(f"{API}/boards/{bid}/files", files={"file": ("x.html", b"<script>1</script>", "text/html")})
    h = client.get(f"{API}/boards/{bid}/files/x.html")
    assert h.headers["content-disposition"].startswith("attachment") and h.headers["content-type"] == "application/octet-stream"
    assert client.delete(f"{API}/boards/{bid}/files/x.html").json()["files"][0]["name"] == f["name"]
    assert client.delete(f"{API}/boards/{bid}").status_code == 200
    assert client.get(f["url"]).status_code in (403, 404)
