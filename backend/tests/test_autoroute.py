from app.services import autoroute

from .conftest import chat

API = "/api/v1"


def test_classify_rules():
    assert autoroute.classify("Hi")[:2] == ("chat", 1)
    assert autoroute.classify("Fix bitte den Bug:\n```py\ndef x(): pass\n```")[0] == "code"
    assert autoroute.classify("Plane die Architektur für das neue Backend mit allen Diensten")[:2] == ("general", 3)
    assert autoroute.classify("egal", code_thread=True)[0] == "code"
    assert autoroute.profile("claude-haiku-5-5").tier == 1
    assert autoroute.profile("claude-opus-5-5").tier == 3
    p = autoroute.profile("agy:gemini-3-flash-high", "Gemini 3 Flash (High)")
    assert (p.provider, p.tier, p.level) == ("antigravity", 1, 3)


def test_auto_picks_model_and_shows_it(client):
    """Model "auto": the server picks model + strength, says so in a route event and stores it with the answer."""
    ev = chat(client, text="Hi", model="auto")
    route = next(e["route"] for e in ev if "route" in e)
    assert route["model"] == "claude-haiku-5-5" and route["effort"] == "low" and route["kind"] == "chat"
    assert "|claude-haiku-5-5|low]" in "".join(e.get("text", "") for e in ev)
    cid = ev[0]["chat"]["id"]
    c = client.get(f"{API}/chats/{cid}").json()
    assert c["model"] == "auto"
    assert c["messages"][-1]["meta"]["route"]["model"] == "claude-haiku-5-5"
    # next message in the same chat routes again (harder question -> stronger model)
    ev = chat(client, chat_id=cid, text="Plane bitte gründlich die Architektur für ein komplett neues Backend " * 3, model="auto")
    assert next(e["route"] for e in ev if "route" in e)["model"] == "claude-opus-5-5"
    client.delete(f"{API}/chats/{cid}")


def test_auto_settings(client):
    s = client.get(f"{API}/autoroute").json()
    assert s["provider_mode"] == "per_chat" and "claude-sonnet-5-5" in s["allowed"]
    r = client.put(f"{API}/autoroute", json={"provider_mode": "claude", "models": ["claude-sonnet-5-5"]}).json()
    assert r["provider_mode"] == "claude" and r["allowed"] == ["claude-sonnet-5-5"]
    ev = chat(client, text="Hi", model="auto")
    assert next(e["route"] for e in ev if "route" in e)["model"] == "claude-sonnet-5-5"  # the only allowed one
    assert client.put(f"{API}/autoroute", json={"provider_mode": "quatsch"}).status_code == 422
    client.put(f"{API}/autoroute", json={"provider_mode": "per_chat", "models": None})
    for c in client.get(f"{API}/chats").json():
        client.delete(f"{API}/chats/{c['id']}")
