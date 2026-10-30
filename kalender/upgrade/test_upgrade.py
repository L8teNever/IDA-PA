"""Upgrade-Test: das AKTUELL veröffentlichte Image bekommt Daten, danach startet das NEUE Image auf demselben Volume.

Prüft: Migrationen laufen, nichts geht verloren, der alte Abo-Link und die Anhänge gehen noch, der Abgleich liefert den vollen Stand,
und ein „alter Client“ (ohne neue Header/Felder) wird weiter angenommen. Braucht Docker (läuft in GitHub Actions, nicht lokal im Normalfall):

  OLD_IMAGE=ghcr.io/<owner>/ida-kalender:latest NEW_IMAGE=ida-kalender:new pytest kalender/upgrade
"""
import os
import socket
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

OLD = os.environ.get("OLD_IMAGE", "ghcr.io/l8tenever/ida-kalender:latest")
NEW = os.environ.get("NEW_IMAGE", "ida-kalender:new")
TOKEN = "upgrade-test-token-123456"
BE = ZoneInfo("Europe/Berlin")

pytestmark = pytest.mark.skipif(subprocess.run(["docker", "version"], capture_output=True).returncode != 0, reason="Docker nicht verfügbar")


def sh(*a, check=True):
    r = subprocess.run(a, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(a)}\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Box:
    def __init__(self, image, volume, port):
        self.image, self.volume, self.port, self.name = image, volume, port, f"kal-up-{uuid.uuid4().hex[:6]}"
        self.url = f"http://127.0.0.1:{port}"

    def start(self):
        sh("docker", "run", "-d", "--name", self.name, "-v", f"{self.volume}:/data", "-p", f"127.0.0.1:{self.port}:8000",
           "-e", f"KALENDER_IDA_TOKEN={TOKEN}", "-e", "TIMEZONE=Europe/Berlin", self.image)
        for _ in range(120):
            try:
                if httpx.get(f"{self.url}/api/v1/health", timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("Container startet nicht:\n" + sh("docker", "logs", self.name, check=False))

    def stop(self):
        sh("docker", "rm", "-f", self.name, check=False)

    def client(self, **kw):
        return httpx.Client(base_url=self.url, timeout=30, **kw)


def at(days, hour=10):
    return (datetime.now(BE) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).replace(tzinfo=None).isoformat(timespec="minutes")


def snapshot(c: httpx.Client) -> dict:
    """Alles, was nach dem Upgrade unverändert da sein muss."""
    a, b = (datetime.now(BE) - timedelta(days=30)).date().isoformat(), (datetime.now(BE) + timedelta(days=400)).date().isoformat()
    evs = c.get("/api/v1/events", params={"from": a, "to": b}).json()["events"]
    return {
        "calendars": sorted((x["name"], x["color"], x["agent_access"]) for x in c.get("/api/v1/calendars").json()["calendars"]),
        "events": sorted((e["title"], e["start"], e["end"], e["all_day"], e["location"], tuple(e["alarms"]), e["recurring"]) for e in evs if not e.get("holiday")),
        "trash": sorted(e["title"] for e in c.get("/api/v1/trash").json()["events"]),
        "settings": {k: v for k, v in c.get("/api/v1/settings").json().items()},
        "changes": len(c.get("/api/v1/changes", params={"limit": 500}).json()["changes"]),
    }


@pytest.fixture(scope="module")
def upgraded():
    vol = f"kal-up-{uuid.uuid4().hex[:8]}"
    sh("docker", "volume", "create", vol)
    old = Box(OLD, vol, free_port()).start()
    try:
        c = old.client()
        cal = c.post("/api/v1/calendars", json={"name": "Schule", "color": "gruen", "default_alarms": [10]}).json()
        single = c.post("/api/v1/events", json={"title": "Einzeltermin", "start": at(3), "end": at(3, 11), "location": "Ort", "alarms": [30], "calendar_id": cal["id"]}).json()["event"]
        series = c.post("/api/v1/events", json={"title": "Serie", "start": at(1, 8), "rrule": "FREQ=WEEKLY;COUNT=8"}).json()["event"]
        c.patch(f"/api/v1/events/{series['event_id']}_{at(8, 8).replace('-', '').replace(':', '')}", params={"scope": "this"}, json={"title": "Serie – Ausnahme"})
        c.post("/api/v1/events", json={"title": "Ganztägig", "start": at(5)[:10], "end": at(7)[:10], "all_day": True})
        gone = c.post("/api/v1/events", json={"title": "Im Papierkorb", "start": at(4)}).json()["event"]
        c.delete(f"/api/v1/events/{gone['id']}")
        c.put("/api/v1/settings", json={"theme": "light", "hide_weekends": True})
        feed = c.get("/api/v1/feed").json()
        c.post(f"/api/v1/events/{single['id']}/files", files={"file": ("notiz.txt", b"Hallo Upgrade", "text/plain")})
        before = snapshot(c)
        before_files = c.get(f"/api/v1/events/{single['id']}/files").json()["files"]
        c.close()
    finally:
        old.stop()
    new = Box(NEW, vol, free_port()).start()
    yield {"new": new, "before": before, "feed": feed, "single": single, "files": before_files, "cal": cal}
    new.stop()
    sh("docker", "volume", "rm", "-f", vol, check=False)


def test_everything_is_still_there(upgraded):
    c = upgraded["new"].client()
    assert snapshot(c) == upgraded["before"]


def test_old_feed_link_and_attachment_still_work(upgraded):
    new = upgraded["new"]
    token = upgraded["feed"]["token"]
    r = httpx.get(f"{new.url}/ics/{token}.ics")
    assert r.status_code == 200 and "Einzeltermin" in r.text
    c = new.client()
    files = c.get(f"/api/v1/events/{upgraded['single']['id']}/files").json()["files"]
    assert files == upgraded["files"]
    got = c.get(f"/api/v1/files/{upgraded['single']['event_id']}/notiz.txt")
    assert got.status_code == 200 and got.content == b"Hallo Upgrade"


def test_sync_gives_the_full_state_after_the_upgrade(upgraded):
    c = upgraded["new"].client()
    meta = c.get("/api/v1/meta").json()
    assert meta["api"] >= 2
    r = c.get("/api/v1/sync").json()
    assert r["full"] and r["cursor"] > 0
    titles = {u["title"] for u in r["upserts"]}
    assert {"Einzeltermin", "Serie", "Serie – Ausnahme", "Ganztägig"} <= titles
    # und danach nur noch Änderungen
    again = c.get("/api/v1/sync", params={"since": r["cursor"], "meta_at": r["meta_at"]}).json()
    assert not again["full"] and again["upserts"] == []


def test_an_old_client_without_new_headers_is_still_accepted(upgraded):
    """Die Oberfläche auf dem Handy kann noch die alte Version sein: kein Idempotency-Key, kein sequence, kein uid."""
    c = upgraded["new"].client()
    r = c.post("/api/v1/events", json={"title": "Vom alten Client", "start": at(9)})
    assert r.status_code == 200 and "existing" not in r.json()
    ev = r.json()["event"]
    assert c.patch(f"/api/v1/events/{ev['id']}", json={"location": "x"}).status_code == 200
    assert c.delete(f"/api/v1/events/{ev['id']}").status_code == 200
    assert c.post(f"/api/v1/events/{ev['id']}/restore").status_code == 200


def test_the_new_image_keeps_working_after_another_restart(upgraded):
    new = upgraded["new"]
    sh("docker", "restart", new.name)
    for _ in range(60):
        try:
            if httpx.get(f"{new.url}/api/v1/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    assert snapshot(new.client())["calendars"] == upgraded["before"]["calendars"]
