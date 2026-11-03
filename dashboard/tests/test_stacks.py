"""Stacks: Erkennung, Aktionen, Dateien bearbeiten, Logs."""
import asyncio

from app.tools import stacks as stacks_mod

from .conftest import ctr, lines


def setup_stacks(c, tmp_path, settings):
    root = settings.stack_dirs[0]
    shop = root / "shop"
    shop.mkdir()
    (shop / "compose.yaml").write_text("services:\n  web:\n    image: nginx\n  db:\n    image: postgres\n")
    (shop / ".env").write_text("A=1\n")
    off = root / "offline"
    off.mkdir()
    (off / "docker-compose.yml").write_text("name: Spar_Modus\nservices:\n  x:\n    image: alpine\n")
    (root / "kein-stack").mkdir()
    c.docker.containers = [
        ctr("a", "web", "shop", wd=str(shop), service="web"),
        ctr("b", "db", "shop", wd=str(shop), service="db", state="exited", status="Exited (0) 1 hour ago"),
        ctr("c", "einzeln", status="Up 3 hours (unhealthy)"),
        ctr("d", "fern", "fremd", wd="/nicht/da"),
    ]
    return shop


async def test_discovery(client, tmp_path, settings):
    setup_stacks(client, tmp_path, settings)
    r = await client.get("/api/v1/stacks")
    assert r.status_code == 200
    s = {x["name"]: x for x in r.json()}
    assert set(s) == {"shop", "einzeln", "fremd", "spar_modus"}
    assert s["shop"]["kind"] == "compose" and s["shop"]["state"] == "partial" and s["shop"]["running"] == 1 and s["shop"]["total"] == 2
    assert s["shop"]["files_ok"] is True
    assert s["einzeln"]["kind"] == "container" and s["einzeln"]["state"] == "problem"
    assert s["fremd"]["files_ok"] is False
    assert s["spar_modus"]["state"] == "stopped" and s["spar_modus"]["containers"] == []
    web = next(x for x in s["shop"]["containers"] if x["service"] == "web")
    assert web["ports"] == ["8080→80"]


async def test_stack_detail_files(client, tmp_path, settings):
    shop = setup_stacks(client, tmp_path, settings)
    r = await client.get("/api/v1/stacks/shop")
    files = {f["name"]: f for f in r.json()["files"]}
    assert files["compose.yaml"]["kind"] == "compose" and files[".env"]["kind"] == "env"
    r = await client.get("/api/v1/stacks/shop/file", params={"path": str(shop / ".env")})
    assert r.json()["content"] == "A=1\n"
    # bearbeiten: .env
    r = await client.put("/api/v1/stacks/shop/file", json={"path": str(shop / ".env"), "content": "A=2\n"})
    assert r.status_code == 200
    assert (shop / ".env").read_text() == "A=2\n"
    assert list((settings.data_dir / "backups" / "shop").glob(".env.*"))  # alter Stand gesichert
    # Compose mit kaputtem YAML wird abgelehnt
    r = await client.put("/api/v1/stacks/shop/file", json={"path": str(shop / "compose.yaml"), "content": "services: [\n"})
    assert r.status_code == 400 and "YAML" in r.json()["detail"]
    r = await client.put("/api/v1/stacks/shop/file", json={"path": str(shop / "compose.yaml"), "content": "foo: 1\n"})
    assert r.status_code == 400
    # fremde Dateien: nein
    for p in (str(tmp_path / "x.txt"), str(shop / ".." / "offline" / "docker-compose.yml"), "/etc/passwd"):
        r = await client.get("/api/v1/stacks/shop/file", params={"path": p})
        assert r.status_code == 403, p
    r = await client.get("/api/v1/stacks/fremd/file", params={"path": "/nicht/da/compose.yaml"})
    assert r.status_code == 400


async def test_compose_action(client, tmp_path, settings, monkeypatch):
    shop = setup_stacks(client, tmp_path, settings)
    calls = []

    async def fake_run(job, args, cwd=None, env=None):
        calls.append(args)
        job.write("ok")
        return True
    monkeypatch.setattr(stacks_mod, "run_cmd", fake_run)
    r = await client.post("/api/v1/stacks/shop/action", json={"action": "update"})
    assert r.status_code == 200
    jid = r.json()["job"]["id"]
    live = await client.get(f"/api/v1/jobs/{jid}/live")
    ev = lines(live.text)
    assert ev[-1]["done"]["ok"] is True
    # die .env im Projektordner liest Compose von selbst; --env-file nur für ausdrücklich gesetzte Dateien
    base = ["docker", "compose", "-p", "shop", "--project-directory", str(shop), "-f", str(shop / "compose.yaml")]
    assert calls == [base + ["pull"], base + ["up", "-d", "--remove-orphans"]]
    r = await client.post("/api/v1/stacks/shop/action", json={"action": "kaputt"})
    assert r.status_code == 400


async def test_self_stack_skips_dashboard(client, tmp_path, settings, monkeypatch):
    shop = setup_stacks(client, tmp_path, settings)
    client.docker.containers.append(ctr("e", "dashboard", "shop", wd=str(shop), service="dashboard", labels={"ida.dashboard": "self"}))
    (shop / "compose.yaml").write_text("services:\n  web:\n    image: nginx\n  db:\n    image: postgres\n  dashboard:\n    image: x\n")
    calls = []

    async def fake_run(job, args, cwd=None, env=None):
        calls.append(args)
        return True
    monkeypatch.setattr(stacks_mod, "run_cmd", fake_run)
    r = await client.post("/api/v1/stacks/shop/action", json={"action": "restart"})
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    assert calls[0][-3:] == ["restart", "web", "db"]
    assert (await client.post("/api/v1/stacks/shop/action", json={"action": "down"})).status_code == 400
    r = await client.post("/api/v1/containers/eeee/action", json={"action": "stop"})
    assert r.status_code == 400


async def test_api_actions_without_files(client, tmp_path, settings):
    setup_stacks(client, tmp_path, settings)
    r = await client.post("/api/v1/stacks/fremd/action", json={"action": "update"})
    assert r.status_code == 400
    r = await client.post("/api/v1/stacks/einzeln/action", json={"action": "restart"})
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    assert client.docker.actions == [("c" * 8, "restart")]
    r = await client.post("/api/v1/containers/aaaa/action", json={"action": "stop"})
    assert r.status_code == 200 and client.docker.actions[-1] == ("a" * 8, "stop")
    assert (await client.post("/api/v1/containers/zzzz/action", json={"action": "stop"})).status_code == 404


async def test_logs_demuxed(client, tmp_path, settings):
    setup_stacks(client, tmp_path, settings)
    r = await client.get("/api/v1/containers/aaaa/logs")
    assert r.json() == {"name": "web", "text": "hallo\nfehler\n"}


async def test_writes_need_json(client, tmp_path, settings):
    setup_stacks(client, tmp_path, settings)
    r = await client.post("/api/v1/stacks/shop/action", content="action=stop", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    assert client.docker.actions == []
