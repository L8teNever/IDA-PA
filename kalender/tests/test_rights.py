"""Rechte: Agents und agent_access, Token, Feed-Link."""
import httpx

from .conftest import API, MCP_TOKEN, TOKEN, mk


def cal_by_name(client, name):
    return next(c for c in client.get(f"{API}/calendars").json()["calendars"] if c["name"] == name)


def test_agent_access_read_blocks_writing_and_none_hides(client, agent):
    ida = agent("ida")
    private = cal_by_name(client, "Privat")
    ev = mk(client, "Nur lesbar", "2028-09-04T10:00")["event"]
    assert client.patch(f"{API}/calendars/{private['id']}", json={"agent_access": "read"}).status_code == 200
    try:
        # lesen geht, schreiben nicht
        assert [e["title"] for e in ida.get(f"{API}/events", params={"from": "2028-09-04", "to": "2028-09-04"}).json()["events"] if not e.get("holiday")] == ["Nur lesbar"]
        r = ida.post(f"{API}/events", json={"title": "x", "start": "2028-09-04T12:00", "calendar_id": private["id"]})
        assert r.status_code == 403 and "nur lesbar" in r.json()["detail"]
        assert ida.patch(f"{API}/events/{ev['id']}", json={"title": "geändert"}).status_code == 403
        assert ida.delete(f"{API}/events/{ev['id']}").status_code == 403
        # die Oberfläche ist nicht betroffen
        assert client.patch(f"{API}/events/{ev['id']}", json={"title": "Nur lesbar"}).status_code == 200
        # „none“: der Kalender ist für Agents unsichtbar
        client.patch(f"{API}/calendars/{private['id']}", json={"agent_access": "none"})
        assert private["id"] not in [c["id"] for c in ida.get(f"{API}/calendars").json()["calendars"]]
        assert [e for e in ida.get(f"{API}/events", params={"from": "2028-09-04", "to": "2028-09-04"}).json()["events"] if not e.get("holiday")] == []
        assert ida.get(f"{API}/events/{ev['id']}").status_code == 404
        assert ida.get(f"{API}/search", params={"q": "lesbar"}).json()["total"] == 0
    finally:
        client.patch(f"{API}/calendars/{private['id']}", json={"agent_access": "write"})
        ida.close()


def test_agents_cannot_change_their_own_rights_or_delete_calendars(client, agent):
    ida = agent("ida")
    private = cal_by_name(client, "Privat")
    assert ida.patch(f"{API}/calendars/{private['id']}", json={"agent_access": "write"}).status_code == 403
    assert ida.delete(f"{API}/calendars/{private['id']}").status_code == 403
    assert ida.put(f"{API}/settings", json={"theme": "light"}).status_code == 403
    assert ida.delete(f"{API}/trash").status_code == 403
    assert ida.post(f"{API}/backups").status_code == 403
    # Kalender anlegen und umbenennen dürfen sie
    c = ida.post(f"{API}/calendars", json={"name": "Von Ida", "color": "gruen"})
    assert c.status_code == 200 and c.json()["agent_access"] == "write"
    assert ida.patch(f"{API}/calendars/{c.json()['id']}", json={"name": "Von Ida 2"}).status_code == 200
    client.delete(f"{API}/calendars/{c.json()['id']}")
    ida.close()


def test_agent_undo_only_own_changes(client, agent):
    ida, lea = agent("ida"), agent("lea")
    r = ida.post(f"{API}/events", json={"title": "Idas Termin", "start": "2028-10-02T10:00"}).json()
    assert lea.post(f"{API}/changes/{r['change_id']}/undo").status_code == 403
    assert ida.post(f"{API}/undo").status_code == 200   # die letzte eigene
    assert ida.get(f"{API}/events/{r['event']['id']}").status_code == 404
    ida.close(); lea.close()


def test_tokens(server):
    bad = httpx.Client(base_url=server, headers={"Authorization": "Bearer falsch"})
    assert bad.get(f"{API}/calendars").status_code == 401
    # der MCP-Token gilt nur unter /mcp, nicht für die normale API
    mcp = httpx.Client(base_url=server, headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    assert mcp.get(f"{API}/calendars").status_code == 401
    ok = httpx.Client(base_url=server, headers={"Authorization": f"Bearer {TOKEN}", "X-Kalender-By": "Boese; DROP"})
    assert ok.get(f"{API}/calendars").status_code == 400
    ok2 = httpx.Client(base_url=server, headers={"Authorization": f"Bearer {TOKEN}", "X-Kalender-By": "user"})
    assert ok2.get(f"{API}/calendars").status_code == 200
    # ohne Token: die Oberfläche
    assert httpx.get(f"{server}{API}/calendars").status_code == 200
    # nicht-Bearer
    assert httpx.get(f"{server}{API}/calendars", headers={"Authorization": "Basic abc"}).status_code == 401


def test_feed_link(client, server):
    info = client.get(f"{API}/feed").json()
    tok = info["token"]
    mk(client, "Im Abo", "2028-11-06T10:00")
    r = httpx.get(f"{server}/ics/{tok}.ics")
    assert r.status_code == 200 and "BEGIN:VCALENDAR" in r.text and "Im Abo" in r.text
    assert httpx.get(f"{server}/ics/falscher-token.ics").status_code == 404
    new = client.post(f"{API}/feed/rotate").json()["token"]
    assert new != tok and httpx.get(f"{server}/ics/{tok}.ics").status_code == 404 and httpx.get(f"{server}/ics/{new}.ics").status_code == 200
