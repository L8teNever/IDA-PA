"""Tests: echte App mit einem Fake-Docker (httpx.MockTransport) und SQLite im tmp-Ordner."""
import json
import struct
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.docker_api import Docker
from app.store import Store

P = "com.docker.compose."


def ctr(cid, name, project=None, state="running", status="Up 2 hours", wd=None, files=None, service=None, labels=None, image="nginx:latest"):
    lab = dict(labels or {})
    if project:
        lab.update({P + "project": project, P + "service": service or name, P + "project.working_dir": wd or f"/opt/stacks/{project}",
                    P + "project.config_files": files or f"{wd or f'/opt/stacks/{project}'}/compose.yaml"})
    return {"Id": cid * 8, "Names": ["/" + name], "Image": image, "ImageID": "sha256:img-" + image, "State": state, "Status": status,
            "Labels": lab, "Created": 1700000000, "Ports": [{"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}] if name == "web" else [],
            "SizeRw": 1000, "Mounts": [{"Type": "volume", "Name": f"{project}_data"}] if project else []}


class FakeDocker:
    def __init__(self):
        self.containers: list[dict] = []
        self.actions: list[tuple] = []
        self.images: dict[str, dict] = {}
        self.cpu = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p = req.url.path
        if p == "/containers/json":
            return httpx.Response(200, json=self.containers)
        if p.startswith("/containers/") and p.endswith("/stats"):
            self.cpu += 1
            n = self.cpu
            return httpx.Response(200, json={"cpu_stats": {"cpu_usage": {"total_usage": n * 1_000_000}, "system_cpu_usage": n * 100_000_000, "online_cpus": 8},
                                              "memory_stats": {"usage": 50_000_000, "limit": 1_000_000_000, "stats": {"inactive_file": 10_000_000}},
                                              "networks": {"eth0": {"rx_bytes": n * 1000, "tx_bytes": n * 500}},
                                              "blkio_stats": {"io_service_bytes_recursive": [{"op": "read", "value": n * 10}, {"op": "write", "value": n * 20}]}})
        if p.startswith("/containers/") and p.endswith("/json"):
            cid = p.split("/")[2]
            c = next((x for x in self.containers if x["Id"].startswith(cid)), None)
            if not c:
                return httpx.Response(404, json={"message": "No such container"})
            return httpx.Response(200, json={"Id": c["Id"], "Name": c["Names"][0], "Image": c["ImageID"], "State": {"Running": c["State"] == "running"},
                                              "Config": {"Tty": False, "Image": c.get("ConfigImage") or c["Image"], "Env": c.get("Env") or [], "Labels": c.get("Labels") or {}},
                                              "HostConfig": c.get("HostConfig") or {}})
        if p.startswith("/networks/") and p.endswith("/connect"):
            self.actions.append(("connect", p.split("/")[2]))
            return httpx.Response(200)
        if p.startswith("/containers/") and p.endswith("/logs"):
            frames = b"".join(struct.pack(">BxxxI", s, len(t)) + t for s, t in ((1, b"hallo\n"), (2, b"fehler\n")))
            return httpx.Response(200, content=frames, headers={"content-type": "application/vnd.docker.raw-stream"})
        if p.startswith("/containers/") and req.method == "POST":
            self.actions.append((p.split("/")[2], p.split("/")[3]))
            return httpx.Response(204)
        if p == "/system/df":
            return httpx.Response(200, json={
                "Images": [{"Id": "sha256:img-nginx:latest", "RepoTags": ["nginx:latest"], "Size": 200, "SharedSize": 0, "Containers": 2},
                           {"Id": "sha256:old", "RepoTags": ["old:1"], "Size": 500, "SharedSize": 0, "Containers": 0}],
                "Containers": self.containers,
                "Volumes": [{"Name": "shop_data", "Labels": {P + "project": "shop"}, "UsageData": {"Size": 3000}},
                            {"Name": "lost", "Labels": {}, "UsageData": {"Size": 70}}],
                "BuildCache": [{"Size": 40, "Shared": False}]})
        if p.startswith("/images/") and req.method == "DELETE":
            self.actions.append(("rmi", p.split("/", 2)[2]))
            return httpx.Response(200, json=[])
        if p.startswith("/images/") and p.endswith("/json"):
            ref = p[len("/images/"):-len("/json")]
            if ref in self.images:
                return httpx.Response(200, json=self.images[ref])
            return httpx.Response(404, json={"message": "No such image"})
        if p == "/images/prune":
            return httpx.Response(200, json={"SpaceReclaimed": 500, "ImagesDeleted": [{"Deleted": "sha256:old"}]})
        return httpx.Response(404, json={"message": f"unbekannt {p}"})


@pytest.fixture
def fake_docker():
    return FakeDocker()


@pytest.fixture
def settings(tmp_path, monkeypatch):
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    proc = tmp_path / "proc"
    (proc / "1" / "net").mkdir(parents=True)
    (proc / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 50 0 50 400 0 0 0 0 0 0\ncpu1 50 0 50 400 0 0 0 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal:  1000 kB\nMemFree: 100 kB\nMemAvailable:  400 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/100 1234\n")
    (proc / "uptime").write_text("3600.0 7000.0\n")
    (proc / "1" / "net" / "dev").write_text("h1\nh2\n    lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\n  eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n"
                                             "veth1: 5 0 0 0 0 0 0 0 5 0 0 0 0 0 0 0\n   wg0: 77 0 0 0 0 0 0 0 77 0 0 0 0 0 0 0\n")
    monkeypatch.setenv("STACKS_DIRS", str(stacks))
    monkeypatch.setenv("FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend"))
    monkeypatch.delenv("IDA_PUBLIC_URL", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)  # nie die echte Cloudflare-API im Test
    for var in ("DASHBOARD_URL", "APP_MODE", "SITES_DIR", "SITES_UPSTREAM", "TUNNEL_HOST_IP", "SERVER_PUBLIC_URL", "LINKS_UPSTREAM", "LINKS_DOMAINS", "WIKI_URL", "WIKI_IDA_TOKEN"):  # nichts aus der echten Umgebung
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LINKS_DB", str(tmp_path / "links" / "links.db"))
    monkeypatch.setenv("HOST_PROC", str(proc))
    monkeypatch.setenv("HOST_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
async def client(settings, fake_docker, tmp_path):
    from app.main import create_app
    from app.tools import monitor, storage

    docker = Docker(httpx.AsyncClient(base_url="http://docker", transport=httpx.MockTransport(fake_docker.handler)))
    store = Store(tmp_path / "data" / "test.db", sample_seconds=15)
    app = create_app(docker=docker, store=store, background=False)
    app.state.sampler = monitor.Sampler(docker, store)
    app.state.storage = storage.State()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://dash") as c:
        c.app, c.docker, c.store = app, fake_docker, store
        yield c
    await docker.client.aclose()
    store.close()


def lines(text: str) -> list[dict]:
    return [json.loads(x) for x in text.splitlines() if x]
