"""IDA Server: dieselbe App im Modus „server“ – Websites (nginx-Konfiguration, Dateien, GitHub), Veröffentlichen (Tunnel, DNS,
Zero Trust gegen eine nachgebaute Cloudflare-API), Leiste/Titel je Modus, Messwerte vom Dashboard."""
import asyncio
import json
import subprocess

import httpx
import pytest

from app import cloudflare_api
from app.config import get_settings
from app.docker_api import Docker
from app.store import Store

from .conftest import ctr


class FakeCF:
    """Cloudflare-API im Speicher: ein Tunnel, eine Zone, DNS, Access-Apps."""

    def __init__(self):
        self.ingress = [{"hostname": "alt.example.com", "service": "http://10.7.0.1:8080"}, {"service": "http_status:404"}]
        self.dns: list[dict] = []
        self.apps: list[dict] = []
        self.calls: list[tuple] = []

    def ok(self, result, info=None):
        return httpx.Response(200, json={"success": True, "result": result, **({"result_info": info} if info else {})})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path.replace("/client/v4", ""), req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append((m, p))
        if p == "/accounts":
            return self.ok([{"id": "acc"}])
        if p == "/accounts/acc/cfd_tunnel":
            return self.ok([{"id": "t1", "name": "main", "status": "healthy", "remote_config": True}])
        if p == "/accounts/acc/cfd_tunnel/t1/configurations":
            if m == "PUT":
                self.ingress = body["config"]["ingress"]
            return self.ok({"config": {"ingress": self.ingress, "warp-routing": {"enabled": False}}})
        if p == "/zones":
            return self.ok([{"id": "z1", "name": "example.com"}])
        if p == "/zones/z1/dns_records":
            if m == "POST":
                rec = {**body, "id": f"r{len(self.dns)}"}
                self.dns.append(rec)
                return self.ok(rec)
            name, typ = req.url.params.get("name"), req.url.params.get("type")
            return self.ok([r for r in self.dns if r["name"] == name and (not typ or r["type"] == typ)])
        if p.startswith("/zones/z1/dns_records/"):
            rid = p.rsplit("/", 1)[1]
            if m == "DELETE":
                self.dns = [r for r in self.dns if r["id"] != rid]
            return self.ok({"id": rid})
        if p == "/accounts/acc/access/apps":
            if m == "POST":
                app = {**body, "id": f"a{len(self.apps)}", "policies": []}
                self.apps.append(app)
                return self.ok(app)
            return self.ok(self.apps, {"total_pages": 1})
        if p.startswith("/accounts/acc/access/apps/") and p.endswith("/policies"):
            aid = p.split("/")[5]
            next(a for a in self.apps if a["id"] == aid)["policies"].append(body)
            return self.ok(body)
        if p.startswith("/accounts/acc/access/apps/") and m == "DELETE":
            aid = p.rsplit("/", 1)[1]
            self.apps = [a for a in self.apps if a["id"] != aid]
            return self.ok({"id": aid})
        return httpx.Response(404, json={"success": False, "errors": [{"code": 7003, "message": f"unbekannt {m} {p}"}]})


@pytest.fixture
def fake_cf(monkeypatch):
    cf = FakeCF()
    real = httpx.AsyncClient
    monkeypatch.setattr(cloudflare_api.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(cf), **kw))
    cloudflare_api.reset()
    yield cf
    cloudflare_api.reset()


@pytest.fixture
async def server(settings, fake_docker, tmp_path, monkeypatch):
    """Die App im Modus server (ohne Hintergrund-Dienste), Websites in tmp."""
    monkeypatch.setenv("APP_MODE", "server")
    monkeypatch.setenv("SITES_DIR", str(tmp_path / "sites"))
    get_settings.cache_clear()
    from app.main import create_app
    docker = Docker(httpx.AsyncClient(base_url="http://docker", transport=httpx.MockTransport(fake_docker.handler)))
    store = Store(tmp_path / "data" / "s.db", sample_seconds=15)
    app = create_app(docker=docker, store=store, background=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server") as c:
        c.app, c.docker = app, fake_docker
        yield c
    await docker.client.aclose()
    store.close()
    get_settings.cache_clear()


async def wait_job(c, jid):
    for _ in range(200):
        j = (await c.get(f"/api/v1/jobs/{jid}")).json()
        if j["done"]:
            return j
        await asyncio.sleep(0.02)
    raise AssertionError("Vorgang wurde nicht fertig")


async def test_modes_have_their_own_tools(client, server):
    d = (await client.get("/api/v1/app")).json()
    assert d["mode"] == "dashboard" and d["title"] == "IDA Dashboard" and d["nav"] == ["overview", "stacks", "graph", "publish", "ports", "files", "monitor", "storage"]
    s = (await server.get("/api/v1/app")).json()
    assert s["mode"] == "server" and s["title"] == "IDA Server" and s["nav"] == ["sites", "links", "stacks", "updates", "files"]
    assert (await client.get("/api/v1/sites")).status_code == 404  # Websites gibt es nur im Server
    assert (await server.get("/api/v1/publish")).status_code == 404  # Veröffentlichen gibt es nur im Dashboard
    assert (await server.get("/api/v1/stacks")).status_code == 200 and (await server.get("/api/v1/fs/list", params={"path": "/"})).status_code != 404
    # Messwerte hat nur das Dashboard; ohne DASHBOARD_URL sagt der Server das
    r = await server.get("/api/v1/monitor/now")
    assert r.status_code == 503 and "Dashboard" in r.json()["detail"]


def test_nginx_conf_root_path_spa():
    from app.tools.sites import nginx_conf
    conf = nginx_conf([
        {"name": "home", "hostname": "a.example.com", "path": "", "spa": True, "root": "dist"},
        {"name": "blog", "hostname": "a.example.com", "path": "/blog", "spa": False, "root": ""},
        {"name": "solo", "hostname": "b.example.com", "path": "/x", "spa": True, "root": ""},
    ])
    assert "listen 80 default_server;" in conf and conf.count("server_name a.example.com;") == 1
    assert "root /srv/www/home/dist;" in conf and "try_files $uri $uri/ /index.html;" in conf
    assert "location ^~ /blog/ {" in conf and "alias /srv/www/blog/;" in conf and "try_files $uri $uri/ =404;" in conf
    assert conf.index("location ^~ /blog/") < conf.index("root /srv/www/home/dist;")  # Pfade vor der Wurzel
    assert "try_files $uri $uri/ /x/index.html;" in conf and "location / { return 404; }" in conf  # nur Pfad, kein Root
    assert "location ~ /\\.(?!well-known) { return 404; }" in conf  # .git nie ausliefern


async def test_site_with_own_files_and_nginx_reload(server, monkeypatch, tmp_path):
    from app.tools import stacks
    nginx = ctr("n", "ida-sites-1", "ida", service="sites", labels={"ida.sites": "nginx"}, image="nginx:alpine")
    nginx["Ports"] = [{"PrivatePort": 80, "PublicPort": 8087, "IP": "10.7.0.1", "Type": "tcp"}]
    server.docker.containers = [nginx]
    ran = []

    async def fake_exec(args, timeout):
        ran.append(args[3:])
        return 0, "ok", False

    monkeypatch.setattr(stacks, "run_exec", fake_exec)
    r = await server.post("/api/v1/sites", json={"name": "Mein Portfolio", "hostname": "Seite.Example.com", "source": "files", "spa": True})
    assert r.status_code == 200, r.text
    assert (await wait_job(server, r.json()["job"]["id"]))["ok"]
    d = (await server.get("/api/v1/sites")).json()
    s = d["sites"][0]
    assert s["name"] == "mein-portfolio" and s["hostname"] == "seite.example.com" and s["status"] == "ok" and s["url"] == "https://seite.example.com"
    assert (tmp_path / "sites" / "www" / "mein-portfolio" / "index.html").exists()
    conf = (tmp_path / "sites" / "conf" / "default.conf").read_text()
    assert "server_name seite.example.com;" in conf
    assert ["nginx", "-t"] in ran and ["nginx", "-s", "reload"] in ran
    assert d["nginx"]["state"] == "running" and d["upstream"] == "http://10.7.0.1:8087" and not d["cloudflare"]
    # gleiche Adresse geht nicht doppelt, kaputte Felder werden abgelehnt
    assert (await server.post("/api/v1/sites", json={"name": "x", "hostname": "seite.example.com"})).status_code == 409
    assert (await server.post("/api/v1/sites", json={"name": "y", "hostname": "nö"})).status_code == 400
    assert (await server.post("/api/v1/sites", json={"name": "z", "hostname": "z.example.com", "root": "../etc"})).status_code == 400
    # kaputte Konfiguration: nginx -t schlägt fehl → alte bleibt
    async def bad_exec(args, timeout):
        return (1, "nginx: [emerg] kaputt", False) if args[3:] == ["nginx", "-t"] else (0, "", False)

    monkeypatch.setattr(stacks, "run_exec", bad_exec)
    r = await server.put("/api/v1/sites/mein-portfolio", json={"spa": False})
    assert r.status_code == 500 and "kaputt" in r.json()["detail"]
    assert (tmp_path / "sites" / "conf" / "default.conf").read_text() == conf
    # entfernen, Dateien auf Wunsch weg
    monkeypatch.setattr(stacks, "run_exec", fake_exec)
    r = await server.post("/api/v1/sites/mein-portfolio/remove", json={"delete_files": True})
    assert (await wait_job(server, r.json()["job"]["id"]))["ok"]
    assert (await server.get("/api/v1/sites")).json()["sites"] == [] and not (tmp_path / "sites" / "www" / "mein-portfolio").exists()


async def test_github_deploy_keeps_token_out_of_git(tmp_path, settings, monkeypatch):
    from app.tools import sites
    monkeypatch.setenv("SITES_DIR", str(tmp_path / "sites"))
    get_settings.cache_clear()
    src = tmp_path / "src"
    src.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=src, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "start")
    (src / "dist").mkdir()
    (src / "dist" / "index.html").write_text("eins")
    run("add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "erste Seite")
    site = {"name": "web", "repo": str(src), "branch": "main", "root": "dist", "token": ""}
    commit = await sites.deploy(site)
    assert commit.endswith("erste Seite")
    out = tmp_path / "sites" / "www" / "web"
    assert (out / "dist" / "index.html").read_text() == "eins"
    (src / "dist" / "index.html").write_text("zwei")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "neu")
    (out / "dist" / "muell.txt").write_text("weg damit")
    assert (await sites.deploy(site)).endswith("neu")
    assert (out / "dist" / "index.html").read_text() == "zwei" and not (out / "dist" / "muell.txt").exists()
    assert sites.auth_url("https://github.com/a/b.git", "geheim") == "https://x-access-token:geheim@github.com/a/b.git"
    assert sites.hide("https://x-access-token:geheim@github.com", "geheim") == "https://x-access-token:***@github.com"
    get_settings.cache_clear()


async def test_publish_route_dns_and_zero_trust(client, fake_cf):
    settings = get_settings()
    settings.cloudflare_api_token = "tok"
    settings.cloudflare_api_url = "https://cf.test/client/v4"
    web = ctr("w", "web", "shop")
    web["Ports"] = [{"PrivatePort": 80, "PublicPort": 8080, "IP": "0.0.0.0", "Type": "tcp"}]
    client.docker.containers = [web]
    d = (await client.get("/api/v1/publish")).json()
    assert d["host_ip"] == "10.7.0.1" and d["tunnels"][0]["id"] == "t1"
    assert d["routes"][0]["hostname"] == "alt.example.com" and d["routes"][0]["stack"] == "shop" and d["routes"][0]["access"]["state"] == "public"
    assert d["targets"][0]["service"] == "http://10.7.0.1:8080"
    # neue Adresse mit Zero Trust
    r = (await client.post("/api/v1/publish", json={"tunnel_id": "t1", "hostname": "neu.example.com", "service": "http://10.7.0.1:8080",
                                                     "zero_trust": True, "emails": ["simon@example.com", "@kulbarts.com"]})).json()
    assert r["ok"], r
    assert {"hostname": "neu.example.com", "service": "http://10.7.0.1:8080"} in fake_cf.ingress and fake_cf.ingress[-1] == {"service": "http_status:404"}
    assert fake_cf.dns[0]["content"] == "t1.cfargotunnel.com" and fake_cf.dns[0]["proxied"]
    pol = fake_cf.apps[0]["policies"][0]
    assert fake_cf.apps[0]["domain"] == "neu.example.com" and pol["decision"] == "allow"
    assert pol["include"] == [{"email": {"email": "simon@example.com"}}, {"email_domain": {"domain": "kulbarts.com"}}]
    d = (await client.get("/api/v1/publish")).json()
    assert next(x for x in d["routes"] if x["hostname"] == "neu.example.com")["access"]["state"] == "access"
    # gleiche Adresse mit anderem Ziel → erst nachfragen
    r = await client.post("/api/v1/publish", json={"tunnel_id": "t1", "hostname": "neu.example.com", "service": "http://10.7.0.1:9000"})
    assert r.status_code == 409
    # Zero Trust aus, dann Adresse samt DNS weg
    assert (await client.post("/api/v1/publish/access", json={"hostname": "neu.example.com", "enabled": False})).json()["ok"]
    assert fake_cf.apps == []
    r = (await client.post("/api/v1/publish/remove", json={"tunnel_id": "t1", "hostname": "neu.example.com"})).json()
    assert r["ok"] and fake_cf.dns == [] and not any(x.get("hostname") == "neu.example.com" for x in fake_cf.ingress)
    # Eingaben werden geprüft
    assert (await client.post("/api/v1/publish", json={"tunnel_id": "t1", "hostname": "x y", "service": "http://a:1"})).status_code == 400
    assert (await client.post("/api/v1/publish", json={"tunnel_id": "t1", "hostname": "a.example.com", "service": "rm -rf"})).status_code == 400


async def test_publish_shows_and_removes_duplicates(client, fake_cf):
    settings = get_settings()
    settings.cloudflare_api_token = "tok"
    settings.cloudflare_api_url = "https://cf.test/client/v4"
    fake_cf.ingress = [{"hostname": "Bus.example.com", "service": "http://10.7.0.1:1"}, {"hostname": "a.example.com", "service": "http://10.7.0.1:2"},
                       {"hostname": "bus.example.com", "service": "http://10.7.0.1:1"}, {"hostname": "a.example.com", "service": "http://10.7.0.1:3"},
                       {"service": "http_status:404"}]
    d = (await client.get("/api/v1/publish")).json()
    dup = [(r["hostname"], r["index"], r["shadowed_by"]) for r in d["routes"] if r.get("shadowed_by")]
    assert dup == [("bus.example.com", 2, "http://10.7.0.1:1"), ("a.example.com", 3, "http://10.7.0.1:2")]
    # Position passt nicht (Tunnel inzwischen geändert) → nichts passiert
    r = await client.post("/api/v1/publish/remove-rule", json={"tunnel_id": "t1", "index": 2, "hostname": "a.example.com", "service": "http://10.7.0.1:3"})
    assert r.status_code == 409 and len(fake_cf.ingress) == 5
    assert (await client.post("/api/v1/publish/remove-rule", json={"tunnel_id": "t1", "index": 3, "hostname": "a.example.com", "service": "http://10.7.0.1:3"})).json()["ok"]
    assert fake_cf.ingress == [{"hostname": "Bus.example.com", "service": "http://10.7.0.1:1"}, {"hostname": "a.example.com", "service": "http://10.7.0.1:2"},
                               {"hostname": "bus.example.com", "service": "http://10.7.0.1:1"}, {"service": "http_status:404"}]
    # neue Regel für bus.example.com ersetzt beide Schreibweisen
    r = (await client.post("/api/v1/publish", json={"tunnel_id": "t1", "hostname": "bus.example.com", "service": "http://10.7.0.1:1", "replace": True})).json()
    assert r["ok"] and [x.get("hostname") for x in fake_cf.ingress].count("bus.example.com") == 1 and "Bus.example.com" not in [x.get("hostname") for x in fake_cf.ingress]


async def test_site_published_through_tunnel(server, fake_cf, monkeypatch):
    from app.tools import stacks
    settings = get_settings()
    settings.cloudflare_api_token = "tok"
    settings.cloudflare_api_url = "https://cf.test/client/v4"
    nginx = ctr("n", "ida-sites-1", "ida", service="sites", labels={"ida.sites": "nginx"}, image="nginx:alpine")
    nginx["Ports"] = [{"PrivatePort": 80, "PublicPort": 8087, "IP": "0.0.0.0", "Type": "tcp"}]
    server.docker.containers = [nginx]

    async def fake_exec(args, timeout):
        return 0, "", False

    monkeypatch.setattr(stacks, "run_exec", fake_exec)
    assert (await server.post("/api/v1/sites", json={"name": "a", "hostname": "a.example.com"})).status_code == 400  # Tunnel fehlt
    r = await server.post("/api/v1/sites", json={"name": "a", "hostname": "a.example.com", "tunnel_id": "t1", "zero_trust": True, "emails": ["s@example.com"]})
    j = await wait_job(server, r.json()["job"]["id"])
    assert j["ok"], j["output"]
    assert {"hostname": "a.example.com", "service": "http://10.7.0.1:8087"} in fake_cf.ingress  # Server-IP aus den vorhandenen Regeln
    assert fake_cf.dns[0]["name"] == "a.example.com" and fake_cf.apps[0]["domain"] == "a.example.com"
    # zweite Seite unter einem Pfad desselben Hosts: keine neue Tunnel-Regel nötig
    r = await server.post("/api/v1/sites", json={"name": "b", "hostname": "a.example.com", "path": "/b", "tunnel_id": "t1"})
    j = await wait_job(server, r.json()["job"]["id"])
    assert j["ok"] and any("zeigt schon auf die Websites" in x for x in j["output"])
    # Pfad auf einem Host, der woanders hinzeigt: eigene Regel nur für den Pfad
    r = await server.post("/api/v1/sites", json={"name": "c", "hostname": "alt.example.com", "path": "/c", "tunnel_id": "t1"})
    assert (await wait_job(server, r.json()["job"]["id"]))["ok"]
    rule = next(x for x in fake_cf.ingress if x.get("hostname") == "alt.example.com" and x.get("path"))
    assert rule["path"] == "^/c(/.*)?$" and fake_cf.ingress.index(rule) < next(i for i, x in enumerate(fake_cf.ingress) if x.get("hostname") == "alt.example.com" and not x.get("path"))
    # entfernen räumt nur auf, was die Seite angelegt hat
    r = await server.post("/api/v1/sites/c/remove", json={})
    assert (await wait_job(server, r.json()["job"]["id"]))["ok"]
    assert {"hostname": "alt.example.com", "service": "http://10.7.0.1:8080"} in fake_cf.ingress and not any(x.get("path") for x in fake_cf.ingress)
    r = await server.post("/api/v1/sites/a/remove", json={})
    assert (await wait_job(server, r.json()["job"]["id"]))["ok"]
    assert any(x.get("hostname") == "a.example.com" for x in fake_cf.ingress)  # b nutzt den Host noch
    assert fake_cf.apps == []  # die Access-App von a ist weg
