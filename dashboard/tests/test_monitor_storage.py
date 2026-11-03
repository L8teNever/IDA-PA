"""Leistung (Messung, Speicherung, Verdichtung, Abfragen), Speicher und Oberfläche."""
from app.store import AGG, Store

from .conftest import ctr


async def test_sampler_and_series(client, settings):
    c = client
    c.docker.containers = [ctr("a", "web", "shop"), ctr("b", "db", "shop"), ctr("c", "aus", state="exited")]
    sm = c.app.state.sampler
    await sm.tick()                      # erste Messung: noch keine Differenz -> nichts gespeichert
    assert c.store.oldest() is None
    # Server-Zähler weiterdrehen
    proc = settings.host_proc
    open(f"{proc}/stat", "w").write("cpu  150 0 150 900 0 0 0 0 0 0\ncpu0 1 0 1 1 0 0 0 0 0 0\ncpu1 1 0 1 1 0 0 0 0 0 0\n")
    open(f"{proc}/1/net/dev", "w").write("h1\nh2\n  eth0: 4000 0 0 0 0 0 0 0 8000 0 0 0 0 0 0 0\n   wg0: 9 0 0 0 0 0 0 0 9 0 0 0 0 0 0 0\n")
    await sm.tick()
    now = (await c.get("/api/v1/monitor/now")).json()
    h = now["host"]
    assert h["cpu"] == 50.0                     # 100 belegt von 200 Ticks
    assert h["mem"] == 600 * 1024 and h["mem_total"] == 1000 * 1024
    assert h["cores"] == 2 and h["load1"] == 0.5
    assert h["net_rx"] > 0 and now["ifaces"] == ["eth0"]   # wg0/lo/veth zählen nicht
    names = {x["name"] for x in now["containers"]}
    assert names == {"web", "db"}
    web = next(x for x in now["containers"] if x["name"] == "web")
    assert web["mem"] == 40_000_000 and web["cpu"] is not None and web["stack"] == "shop"

    r = (await c.get("/api/v1/monitor/host", params={"start": now["ts"] - 60, "end": now["ts"] + 1})).json()
    assert r["cpu"] == [50.0] and r["res"] == 0
    u = (await c.get("/api/v1/monitor/usage", params={"start": now["ts"] - 60, "end": now["ts"] + 1})).json()
    assert {x["name"] for x in u} == {"web", "db"}
    s = (await c.get("/api/v1/monitor/series", params={"metric": "mem", "group": "stack", "start": now["ts"] - 60, "end": now["ts"] + 1})).json()
    assert s["series"] == {"shop": [80_000_000]}       # Stack = Summe seiner Container
    assert (await c.get("/api/v1/monitor/series", params={"metric": "x"})).status_code == 400


def test_store_compact_and_long_ranges(tmp_path):
    st = Store(tmp_path / "s.db", sample_seconds=15, raw_keep_hours=1, keep_days=30)
    base = 1_700_000_000 // AGG * AGG
    for i in range(80):  # 20 Minuten Einzelwerte
        ts = base + i * 15
        st.add(ts, {"cpu": 10 + (i % 2) * 10, "mem": 100, "mem_total": 1000, "net_rx": 100, "net_tx": 0, "disk_used": 5, "disk_total": 10},
               [{"name": "a", "stack": "s", "cpu": 1, "mem": 5, "net_rx": 10, "net_tx": 0}])
    st.compact(now=base + 3 * 3600)   # weit später: Einzelwerte weg, Mittel bleiben
    rows = st.db.execute("SELECT res, COUNT(*) FROM host GROUP BY res").fetchall()
    assert rows == [(1, 2)]
    r = st.host_series(base, base + 1200, now=base + 3 * 3600)
    assert r["res"] == 1 and r["cpu"] == [15.0, 15.0]
    assert r["total_rx"] == 80 * 100 * 15              # Datenmenge bleibt über die Verdichtung erhalten
    u = st.ctr_usage(base, base + 1200, now=base + 3 * 3600)
    assert u[0]["rx"] == 80 * 10 * 15
    # Verdichtung ist wiederholbar
    st.compact(now=base + 3 * 3600)
    assert st.db.execute("SELECT COUNT(*) FROM host").fetchone()[0] == 2
    st.close()


async def test_storage(client, tmp_path, settings):
    c = client
    c.docker.containers = [ctr("a", "web", "shop"), ctr("b", "db", "shop")]
    shop = settings.stack_dirs[0] / "shop"
    shop.mkdir()
    (shop / "welt.dat").write_bytes(b"x" * 100_000)
    r = await c.post("/api/v1/storage/refresh", json={})
    assert r.status_code == 200
    import asyncio
    for _ in range(50):
        if c.app.state.storage.dirs_at:
            break
        await asyncio.sleep(0.02)
    d = (await c.get("/api/v1/storage")).json()
    assert d["disk"]["disk_total"] > 0
    assert d["docker"]["images"] == 700 and d["docker"]["images_unused"] == 500 and d["docker"]["volumes_unused"] == 70
    shop_s = next(s for s in d["stacks"] if s["name"] == "shop")
    assert shop_s["images"] == 200 and shop_s["volumes"] == 3000 and shop_s["rw"] == 2000 and shop_s["files"] >= 100_000
    r = await c.post("/api/v1/storage/prune", json={"what": "images"})
    assert r.json() == {"reclaimed": 500, "count": 1}


async def test_frontend_and_app(client):
    r = await client.get("/stacks/shop")
    assert r.status_code == 200 and "IDA Dashboard" in r.text and '"/static/' not in r.text
    sw = await client.get("/sw.js")
    assert "__VERSION__" not in sw.text and "__SHELL__" not in sw.text
    assert (await client.get("/manifest.webmanifest")).json()["name"] == "IDA Dashboard"
    a = (await client.get("/api/v1/app")).json()
    assert [t["id"] for t in a["tools"]] == ["stacks", "monitor", "storage"]
    assert (await client.get("/api/v1/unbekannt")).status_code == 404
