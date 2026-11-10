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
