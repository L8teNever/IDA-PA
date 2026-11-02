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


def test_ratings_teach_auto(client):
    """Ratings under Auto answers: one alone moves nothing, several "zu schwach" make the kind one step stronger."""
    ids, cid = [], None
    for _ in range(3):
        ev = chat(client, text="Hi", model="auto", **({"chat_id": cid} if cid else {}))
        cid = ev[0]["chat"]["id"]
        ids.append(next(e["done"]["message_id"] for e in ev if "done" in e))
    r = client.post(f"{API}/autoroute/rate", json={"message_id": ids[0], "rating": "weak"}).json()
    assert r["route"]["rating"] == "weak"
    ev = chat(client, chat_id=cid, text="Hi", model="auto")
    assert next(e["route"] for e in ev if "route" in e)["model"] == "claude-haiku-5-5"  # one rating: no change yet
    for mid in ids[1:]:
        client.post(f"{API}/autoroute/rate", json={"message_id": mid, "rating": "weak"})
    ev = chat(client, chat_id=cid, text="Hi", model="auto")
    route = next(e["route"] for e in ev if "route" in e)
    assert route["model"] == "claude-sonnet-5-5" and "gelernt" in route["reason"]
    s = client.get(f"{API}/autoroute").json()
    assert s["stats"][0]["kind"] == "chat" and s["stats"][0]["weak"] == 3 and s["stats"][0]["shift"] == 1
    # rated message shows the rating; a non-Auto answer cannot be rated
    msgs = client.get(f"{API}/chats/{cid}").json()["messages"]
    assert next(m for m in msgs if m["id"] == ids[0])["meta"]["route"]["rating"] == "weak"
    plain = chat(client, text="Hallo")
    pid = next(e["done"]["message_id"] for e in plain if "done" in e)
    assert client.post(f"{API}/autoroute/rate", json={"message_id": pid, "rating": "ok"}).status_code == 400
    assert client.delete(f"{API}/autoroute/learned").json()["stats"] == []
    for c in client.get(f"{API}/chats").json():
        client.delete(f"{API}/chats/{c['id']}")


def test_similar_questions_decide(monkeypatch):
    """With embeddings: closely similar rated questions set the strength (beats the rules)."""
    import array
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db import Base
    from app.models import RouteFeedback
    from app.services.memory import embeddings

    vecs = {"Wie spät ist es?": [1.0, 0.0], "Wie spät ist es jetzt?": [0.99, 0.14], "Erklär Quantenphysik": [0.0, 1.0]}

    async def fake_embed(text):
        v = vecs.get(text)
        return ("fake", array.array("f", v)) if v else None

    monkeypatch.setattr(autoroute, "_embed", fake_embed)

    async def go():
        eng = create_async_engine("sqlite+aiosqlite://")
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(eng)() as db:
            assert await autoroute.similar_difficulty(db, "Wie spät ist es jetzt?") is None  # nothing rated yet
            for i in range(2):  # Sonnet was "zu stark" for the time question twice -> ideal = 1
                db.add(RouteFeedback(message_id=f"m{i}", kind="general", difficulty=2, model="claude-sonnet-5-5", rating="strong",
                                     snippet="Wie spät ist es?", embedding=embeddings.to_bytes(array.array("f", [1.0, 0.0])), embed_model="fake"))
            await db.flush()
            assert await autoroute.similar_difficulty(db, "Wie spät ist es jetzt?") == (1, 2)
            assert await autoroute.similar_difficulty(db, "Erklär Quantenphysik") is None  # not similar
        # a named better model wins over the rating direction
        assert autoroute._ideal(RouteFeedback(model="claude-haiku-5-5", rating="ok", better_model="claude-opus-5-5", difficulty=1)) == 3
        assert autoroute._ideal(RouteFeedback(model="claude-sonnet-5-5", rating="weak", difficulty=2)) == 3
        await eng.dispose()

    asyncio.run(go())
