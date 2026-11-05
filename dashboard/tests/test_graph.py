"""Graph im Dashboard: Stacks → Container → Adressen ← Zero-Trust-Apps, Adressen → Tunnel."""
from app import cloudflare_api, tunnels

from .conftest import ctr


async def test_graph_nodes_and_edges(client, monkeypatch):
    web = ctr("w", "web", "shop")
    db = ctr("d", "db", "shop", state="exited", status="Exited (0)")
    tunnel = ctr("t", "cloudflared", "cf", service="cloudflared", image="cloudflare/cloudflared:latest")
    tunnel["NetworkSettings"] = {"Networks": {"cf_default": {"IPAddress": "172.19.0.3"}}}
    dash = ctr("x", "dashboard", "ida", service="dashboard", labels={"ida.dashboard": "self"})
    client.docker.containers = [web, db, tunnel, dash]

    async def fake_config(url):
        return {"config": {"ingress": [{"hostname": "shop.example.com", "service": "http://web:80"}, {"hostname": "fremd.example.com", "service": "http://10.0.0.9:1"}]}}

    apps = [{"id": "a1", "name": "Shop-Login", "type": "self_hosted", "domains": ["shop.example.com", "shop.example.com/admin"],
             "policies": [{"name": "Simon", "decision": "allow"}]},
            {"id": "a2", "name": "Woanders", "type": "self_hosted", "domains": ["nas.example.com"], "policies": [{"name": "Alle", "decision": "bypass"}]}]

    async def fake_apps():
        return apps

    async def fake_tunnel_routes():
        return [{"host": "shop.example.com", "service": "http://web:80", "path": None, "tunnel": "main"}]

    monkeypatch.setattr(tunnels, "fetch_config", fake_config)
    monkeypatch.setattr(cloudflare_api, "enabled", lambda: True)
    monkeypatch.setattr(cloudflare_api, "access_apps", fake_apps)
    monkeypatch.setattr(cloudflare_api, "tunnel_routes", fake_tunnel_routes)
    tunnels.reset()
    d = (await client.get("/api/v1/graph")).json()
    n = {x["id"]: x for x in d["nodes"]}
    e = {(x["source"], x["target"]) for x in d["edges"]}
    assert n["s:shop"]["kind"] == "stack" and n[f"c:{db['Id']}"]["state"] == "exited"
    assert ("s:shop", f"c:{web['Id']}") in e and ("s:shop", f"c:{db['Id']}") in e
    assert ("c:" + web["Id"], "d:shop.example.com") in e  # Container → Adresse
    assert n["d:shop.example.com"]["access"] == "access" and ("a:a1", "d:shop.example.com") in e  # App → Adresse
    assert n["d:fremd.example.com"]["access"] == "public" and not any(t == "d:fremd.example.com" and s.startswith("c:") for s, t in e)
    assert ("d:shop.example.com", "t:main") in e and n["t:main"]["kind"] == "tunnel"
    # App-Domain ohne Tunnel-Route: eigener Punkt, Pfad hängt am Host
    assert n["d:shop.example.com/admin"]["orphan"] and ("d:shop.example.com", "d:shop.example.com/admin") in e
    assert ("a:a1", "d:shop.example.com/admin") in e
    assert n["a:a2"]["bypass"] and n["d:nas.example.com"]["access"] == "public"
    assert d["counts"]["stack"] == 3 and d["counts"]["access"] == 2
    tunnels.reset()


async def test_graph_without_cloudflare(client):
    client.docker.containers = [ctr("w", "web", "shop")]
    tunnels.reset()
    d = (await client.get("/api/v1/graph")).json()
    assert {x["kind"] for x in d["nodes"]} == {"stack", "container"} and not d["cloudflare"]
    tunnels.reset()
