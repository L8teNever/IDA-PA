"""Ports: /proc/net lesen (IPv4/IPv6), Schnittstellen einordnen, mit Docker und Tunnel zusammenführen, freie Ports."""
from app import tunnels
from app.config import get_settings
from app.tools import ports

from .conftest import ctr

HEAD = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def line(local, st, inode):
    return f"   0: {local} 00000000:0000 {st} 00000000:00000000 00:00000000 00000000     0        0 {inode} 1 0 0 10 0\n"


def test_parse_net_ipv4_ipv6_and_scope():
    tcp = HEAD + line("0100007F:1F90", "0A", 11) + line("0100070A:20FD", "0A", 12) + line("00000000:0016", "0A", 13) + line("0100007F:9C40", "01", 14)
    got = ports.parse_net(tcp, "tcp")
    assert [(x["ip"], x["port"]) for x in got] == [("127.0.0.1", 8080), ("10.7.0.1", 8445), ("0.0.0.0", 22)]  # ESTABLISHED fällt raus
    tcp6 = HEAD + line("00000000000000000000000000000000:0050", "0A", 21) + line("00000000000000000000000001000000:0277", "0A", 22)
    assert [(x["ip"], x["port"]) for x in ports.parse_net(tcp6, "tcp")] == [("::", 80), ("::1", 631)]
    assert ports.parse_net(HEAD + line("00000000:0035", "07", 31), "udp")[0]["port"] == 53
    assert [ports.scope(a) for a in ("0.0.0.0", "::", "127.0.0.1", "10.7.0.1", "172.20.0.2", "138.201.31.179")] == ["all", "all", "local", "private", "private", "public"]


async def test_ports_endpoint(client, monkeypatch, tmp_path):
    net = __import__("pathlib").Path(get_settings().host_proc) / "1" / "net"
    (net / "tcp").write_text(HEAD + line("0100070A:20FD", "0A", 12) + line("00000000:0016", "0A", 13) + line("0100007F:1538", "0A", 14))
    (net / "tcp6").write_text(HEAD + line("00000000000000000000000000000000:0016", "0A", 15))
    (net / "udp").write_text(HEAD)
    web = ctr("w", "web", "shop")
    web["Ports"] = [{"IP": "10.7.0.1", "PrivatePort": 8000, "PublicPort": 8445, "Type": "tcp"}]
    old = ctr("o", "alt", "shop", state="exited")
    client.docker.containers = [web, old]

    async def fake_routes(docker, cs):
        return [{"host": "dash.example.com", "service": "http://10.7.0.1:8445"}, {"host": "intern.example.com", "service": "http://web:8000"}]

    monkeypatch.setattr(tunnels, "routes", fake_routes)
    d = (await client.get("/api/v1/ports")).json()
    rows = {r["port"]: r for r in d["ports"]}
    assert sorted(rows) == [22, 5432, 8445]
    assert rows[22]["addresses"] == ["0.0.0.0", "::"] and rows[22]["scope"] == "all" and rows[22]["kind"] == "host"  # IPv4+IPv6 = eine Zeile
    assert rows[5432]["scope"] == "local"
    r = rows[8445]
    assert r["kind"] == "docker" and r["docker"][0]["stack"] == "shop" and r["docker"][0]["private"] == 8000 and r["scope"] == "private"
    assert [t["url"] for t in r["routes"]] == ["https://dash.example.com"]  # http://web:8000 ist ein Container-Ziel, kein Host-Port
    # freie Ports: belegt sind 8445 (lauscht) und was ein gestoppter Container gebunden hat
    old["HostConfig"] = {"PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8446"}]}}
    f = (await client.get("/api/v1/ports/free", params={"start": 8444, "end": 8450, "count": 3})).json()
    assert f["free"] == [8444, 8447, 8448]
    assert (await client.get("/api/v1/ports/free", params={"start": 10, "end": 5})).status_code == 400
