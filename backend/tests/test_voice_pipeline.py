"""Stellas eigene Kette: Cloudflare (Erkennung) -> Claude in IDA (Gehirn mit Tools) -> Piper (Sprachausgabe)."""
import asyncio
import base64
import json

import httpx
import pytest

from .conftest import wait

API = "/api/v1"
SECRET = {"x-voice-secret": "test-voice-secret-123456"}


# ---------------- Einzelteile ----------------
def test_sentences_are_cut_where_one_ends():
    from app.voice.speech import split_sentences
    done, rest = split_sentences("Sehr wohl. Der Termin ist am 3. Mai um acht. Und dann")
    assert done == ["Sehr wohl.", "Der Termin ist am 3. Mai um acht."] and rest == "Und dann"
    assert split_sentences("Das ist z. B. gut! Ja? ") == (["Das ist z. B. gut!", "Ja?"], "")
    assert split_sentences("noch kein Ende") == ([], "noch kein Ende")


def test_heard_text_is_cleaned():
    from app.voice.speech import clean_heard, speakable
    assert clean_heard("  Hallo   Stella ") == "Hallo Stella"
    assert clean_heard("Untertitel der Amara.org-Community") == ""
    assert clean_heard("...") == ""
    assert speakable("**Termin** steht 🎉 siehe https://x.de [hier](https://y.de)") == "Termin steht siehe hier"


class S:
    cf_ai_account, cf_ai_token, piper_url = "acc1", "tok1", "http://piper"
    voice_pipeline_pause_ms = 50
    voice_vad_rms, voice_vad_min_speech_ms, voice_vad_silence_ms = 500, 60, 120


def test_transcribe_with_cloudflare():
    from app.voice import speech
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if "whisper-large" in req.url.path:
            body = json.loads(req.content)
            assert body["language"] == "de" and base64.b64decode(body["audio"])[:4] == b"RIFF"
            return httpx.Response(200, json={"success": True, "result": {"text": " Wie spät ist es? "}})
        if "nova-3" in req.url.path:
            assert req.headers["content-type"] == "audio/wav" and req.url.params["language"] == "de"
            return httpx.Response(200, json={"result": {"results": {"channels": [{"alternatives": [{"transcript": "Licht an"}]}]}}})
        return httpx.Response(401, json={"success": False, "errors": [{"message": "Authentication error"}]})
    t = httpx.MockTransport(handler)
    pcm = b"\x00\x01" * 8000
    assert asyncio.run(speech.transcribe(S, "@cf/openai/whisper-large-v3-turbo", pcm, transport=t)) == "Wie spät ist es?"
    assert seen[0].url.path == "/client/v4/accounts/acc1/ai/run/@cf/openai/whisper-large-v3-turbo"
    assert seen[0].headers["authorization"] == "Bearer tok1"
    assert asyncio.run(speech.transcribe(S, "@cf/deepgram/nova-3", pcm, transport=t)) == "Licht an"
    with pytest.raises(speech.SpeechError, match="Authentication error"):
        asyncio.run(speech.transcribe(S, "@cf/openai/whisper", pcm, transport=t))

    class NoCf(S):
        cf_ai_token = ""
    with pytest.raises(speech.SpeechError, match="Cloudflare-Zugang fehlt"):
        asyncio.run(speech.transcribe(NoCf, "@cf/openai/whisper", pcm))


def test_transcribe_with_google():
    from app.voice import speech
    seen = []

    class G(S):
        google_stt_key, google_stt_model = "gkey", "latest_short"

    def handler(req: httpx.Request):
        seen.append(req)
        body = json.loads(req.content)
        assert req.headers["x-goog-api-key"] == "gkey"
        assert body["config"]["languageCode"] == "de-DE" and body["config"]["sampleRateHertz"] == 16000
        assert base64.b64decode(body["audio"]["content"]) == pcm
        return httpx.Response(200, json={"results": [{"alternatives": [{"transcript": "Mach das Licht"}]}, {"alternatives": [{"transcript": " an."}]}]})
    pcm = b"\x00\x01" * 8000
    assert asyncio.run(speech.transcribe(G, "google", pcm, transport=httpx.MockTransport(handler))) == "Mach das Licht an."
    assert seen[0].url.host == "speech.googleapis.com"
    assert asyncio.run(speech.transcribe(G, "google", pcm, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))) == ""
    bad = httpx.MockTransport(lambda r: httpx.Response(403, json={"error": {"message": "API not enabled"}}))
    with pytest.raises(speech.SpeechError, match="API not enabled"):
        asyncio.run(speech.transcribe(G, "google", pcm, transport=bad))

    class NoKey(S):
        google_stt_key = ""
    with pytest.raises(speech.SpeechError, match="GOOGLE_STT_API_KEY"):
        asyncio.run(speech.transcribe(NoKey, "google", pcm))


def piper_transport(log=None):
    from app.voice.speech import wav_bytes

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        if log is not None:
            log.append(body)
        return httpx.Response(200, content=wav_bytes(b"\x10\x00" * 2205, 22050), headers={"content-type": "audio/wav"})
    return httpx.MockTransport(handler)


def test_synthesize_gives_24khz():
    from app.voice import speech
    log = []
    pcm = asyncio.run(speech.synthesize(S, "Hallo.", "de_DE-thorsten-high", 1.1, transport=piper_transport(log)))
    assert abs(len(pcm) - 2400 * 2) <= 4  # 0,1 s bei 24 kHz
    assert log == [{"text": "Hallo.", "voice": "de_DE-thorsten-high", "speed": 1.1}]


# ---------------- die Kette im voice-Container (Ersatz für IDA, Cloudflare und Piper) ----------------
class FakeIda:
    def __init__(self, answers):
        self.answers = answers  # Text -> Liste von Ereignissen
        self.turns, self.closed, self.tools = [], False, []

    async def brain_open(self, call_id, instructions):
        assert "SPRACHAUSGABE" in instructions
        return {"ok": True, "model": "claude-haiku-5-5"}

    async def brain_turn(self, call_id, text, who="user"):
        self.turns.append((who, text))
        for key, evs in self.answers.items():
            if key in text:
                for ev in evs:
                    await asyncio.sleep(0.01)
                    yield ev
                return
        yield {"t": "text", "text": "Okay."}
        yield {"t": "done", "ok": True}

    async def brain_close(self, call_id):
        self.closed = True

    async def tool(self, call_id, name, args):
        self.tools.append(name)
        return {"gone": True}


class Sink:
    def __init__(self):
        self.chunks, self.texts, self.tools, self.hung, self.interrupts = [], [], [], None, 0
        self.play_end = 0.0

    async def audio(self, pcm, rate):
        assert rate == 24000
        self.chunks.append(len(pcm))

    async def interrupted(self):
        self.interrupts += 1

    async def text(self, who, piece):
        self.texts.append((who, piece))

    async def tool(self, name, running):
        self.tools.append((name, running))

    def hang_up(self, outcome):
        self.hung = outcome


def _session(ida, sink, heard):
    from app.voice.pipeline import PipelineSession

    def stt(req):
        return httpx.Response(200, json={"success": True, "result": {"text": heard.pop(0) if heard else ""}})
    return PipelineSession(S, instructions="Du bist Stella.", ida=ida, call_id="p1", sink=sink, kickoff="Begrüß ihn.", client_vad=True,
                           engine={"engine": "pipeline", "stt": "@cf/openai/whisper-large-v3-turbo", "llm": "claude-haiku-5-5",
                                   "piper_voice": "de_DE-thorsten-high", "piper_speed": 1.0},
                           stt_transport=httpx.MockTransport(stt), tts_transport=piper_transport())


async def _until(cond, timeout=5):
    for _ in range(int(timeout / 0.02)):
        if cond():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timeout")


def test_pipeline_hears_thinks_and_speaks():
    async def go():
        ida = FakeIda({
            "Begrüß": [{"t": "text", "text": "Da bist"}, {"t": "text", "text": " du ja. Was"}, {"t": "text", "text": " gibt's?"}, {"t": "done", "ok": True}],
            "Termin": [{"t": "text", "text": "Moment."}, {"t": "tool", "name": "calendar_events", "running": True},
                       {"t": "tool", "name": "calendar_events", "running": False}, {"t": "text", "text": "Morgen um acht."}, {"t": "done", "ok": True}],
            "Tschüss": [{"t": "text", "text": "Bis bald!"}, {"t": "done", "ok": True, "hangup": "Erledigt."}],
        })
        sink = Sink()
        heard = ["Wann ist mein Termin?", "Tschüss"]
        s = _session(ida, sink, heard)
        await s.open()
        await s.kickoff()
        await _until(lambda: s.first_turn_done.is_set())
        assert [t for w, t in sink.texts if w == "stella"] == ["Da bist du ja. ", "Was gibt's? "]  # Satz für Satz gesprochen
        assert len(sink.chunks) == 2 and ida.turns[0] == ("system", "[System, nicht vorlesen] Begrüß ihn.")
        # ein Satz mit kurzer Pause in der Mitte bleibt ein Satz, kurzes Knacken zählt nicht
        await s.activity_start()
        await s.send_audio(b"\x00\x01" * 3200)
        await s.activity_end()
        await s.activity_start()
        await s.send_audio(b"\x00\x01" * 3200)
        await s.activity_end()
        await _until(lambda: ("user", "Wann ist mein Termin?") in sink.texts)
        await _until(lambda: any("acht" in t for _, t in sink.texts))
        assert ("calendar_events", True) in sink.tools and ("calendar_events", False) in sink.tools
        assert ida.turns[1] == ("user", "Wann ist mein Termin?") and len(heard) == 1  # einmal erkannt, nicht zweimal
        await s.activity_start()
        await s.send_audio(b"\x00\x01" * 160)  # 10 ms: kein Satz
        await s.activity_end()
        await asyncio.sleep(0.2)
        assert len(heard) == 1
        await s.activity_start()
        await s.send_audio(b"\x00\x01" * 8000)
        await s.activity_end()
        await _until(lambda: sink.hung)
        assert sink.hung == "Erledigt." and s.outcome == "Erledigt."
        assert [x["who"] for x in s.transcript] == ["stella", "user", "stella", "user", "stella"]
        await s.close()
        assert ida.closed
    asyncio.run(go())


def test_pipeline_barge_in_stops_stella():
    async def go():
        gate = asyncio.Event()

        class SlowIda(FakeIda):
            async def brain_turn(self, call_id, text, who="user"):
                self.turns.append((who, text))
                if "erzähl" in text:
                    yield {"t": "text", "text": "Es war einmal ein Satz. "}
                    await gate.wait()
                    yield {"t": "text", "text": "Und noch einer, den keiner hören soll. "}
                    yield {"t": "done", "ok": True}
                else:
                    yield {"t": "text", "text": "Klar."}
                    yield {"t": "done", "ok": True}
        ida = SlowIda({})
        sink = Sink()
        s = _session(ida, sink, ["erzähl was", "Stopp, andere Frage"])
        await s.open()
        await s.activity_start()
        await s.send_audio(b"\x00\x01" * 8000)
        await s.activity_end()
        await _until(lambda: any("einmal" in t for _, t in sink.texts))
        await s.activity_start()  # der Nutzer redet dazwischen
        assert sink.interrupts == 1
        await s.send_audio(b"\x00\x01" * 8000)
        await s.activity_end()
        gate.set()
        await _until(lambda: len(ida.turns) == 2)
        await _until(lambda: any("Klar" in t for _, t in sink.texts))
        assert not any("keiner" in t for _, t in sink.texts)  # der Rest der alten Antwort wird nicht gesprochen
        assert "unterbrochen" in ida.turns[1][1] and "Es war einmal ein Satz." in ida.turns[1][1] and "Stopp, andere Frage" in ida.turns[1][1]
        await s.close()
    asyncio.run(go())


def test_pipeline_needs_cloudflare_and_piper():
    from app.voice import speech

    class NoCf(S):
        cf_ai_account = ""
    from app.voice.pipeline import PipelineSession
    s = PipelineSession(NoCf, instructions="", ida=FakeIda({}), call_id="x", engine={}, sink=Sink(), kickoff="", client_vad=True)
    with pytest.raises(speech.SpeechError, match="Cloudflare"):
        asyncio.run(s.open())

    class NoGoogle(S):
        google_stt_key = ""
    s = PipelineSession(NoGoogle, instructions="", ida=FakeIda({}), call_id="x", engine={"stt": "google"}, sink=Sink(), kickoff="", client_vad=True)
    with pytest.raises(speech.SpeechError, match="GOOGLE_STT_API_KEY"):
        asyncio.run(s.open())


def test_make_session_picks_the_engine():
    from app.voice.live import LiveSession
    from app.voice.pipeline import PipelineSession, make_session
    kw = dict(ida=FakeIda({}), call_id="x", instructions="", tools=[], sink=Sink(), kickoff="", client_vad=True)
    assert isinstance(make_session(S, {"engine": {"engine": "pipeline"}}, **kw), PipelineSession)
    assert isinstance(make_session(S, {"engine": {"engine": "gemini"}}, **kw), LiveSession)
    assert isinstance(make_session(S, {"engine": {"engine": "pipeline"}, "offline": True}, **kw), LiveSession)  # ohne IDA kein Gehirn


# ---------------- IDA: Einstellungen, Gehirn und Tools ----------------
def test_engine_settings(client):
    d = client.get(f"{API}/voice/settings").json()
    e = d["engine"]
    assert e["engine"] == "gemini" and e["llm"] == "claude-haiku-5-5" and e["stt"] == "@cf/openai/whisper-large-v3-turbo"
    assert e["piper_voice"] == "de_DE-thorsten-high" and {m["id"] for m in e["engines"]} == {"gemini", "pipeline"}
    assert any(m["id"] == "claude-sonnet-5-5" for m in e["llm_models"]) and e["status"]["cloudflare"] is False and e["status"]["piper"] is False and "google" in e["status"]
    r = client.patch(f"{API}/voice/settings", json={"engine": "pipeline", "llm": "claude-sonnet-5-5", "stt": "@cf/deepgram/nova-3",
                                                    "piper_voice": "de_DE-kerstin-low", "piper_speed": 1.2}).json()
    assert r["engine"]["engine"] == "pipeline" and r["engine"]["llm"] == "claude-sonnet-5-5" and r["engine"]["piper_speed"] == 1.2
    for bad in ({"engine": "x"}, {"llm": "agy:gemini-3-pro"}, {"llm": "gpt-9"}, {"stt": "@cf/foo"}, {"piper_voice": "../etc"}):
        assert client.patch(f"{API}/voice/settings", json=bad).status_code == 400, bad
    assert client.patch(f"{API}/voice/settings", json={"voice": "Kore"}).json()["voice"] == "Kore"  # Gemini-Stimme wie bisher
    assert client.get(f"{API}/voice/settings").json()["engine"]["stt"] == "@cf/deepgram/nova-3"
    client.patch(f"{API}/voice/settings", json={"engine": "gemini", "llm": "claude-haiku-5-5", "stt": "@cf/openai/whisper-large-v3-turbo",
                                                "piper_voice": "de_DE-thorsten-high", "piper_speed": 1.0, "voice": "Aoede"})


def test_brain_with_tools_and_hang_up(client):
    client.patch(f"{API}/voice/settings", json={"engine": "pipeline"})
    try:
        start = client.post(f"{API}/voice/internal/start", headers=SECRET, json={"call_id": "pbrain1", "direction": "web", "owner": True}).json()
        assert start["engine"]["engine"] == "pipeline"
        assert client.post(f"{API}/voice/internal/brain/open", json={"call_id": "pbrain1"}).status_code == 403
        o = client.post(f"{API}/voice/internal/brain/open", headers=SECRET, json={"call_id": "pbrain1", "instructions": "Du bist Stella."}).json()
        assert o["ok"] and o["model"] == "claude-haiku-5-5"

        def turn(text, who="user"):
            with client.stream("POST", f"{API}/voice/internal/brain/turn", headers=SECRET, json={"call_id": "pbrain1", "text": text, "who": who}) as r:
                return [json.loads(line) for line in r.iter_lines() if line]
        evs = turn("Hallo Stella")
        text = "".join(e["text"] for e in evs if e["t"] == "text")
        assert text == "Verstanden: Hallo Stella. Noch ein Satz am 3. Mai." and evs[-1] == {"t": "done", "ok": True}
        tools = "".join(e["text"] for e in turn("TOOLS") if e["t"] == "text")
        assert tools.startswith("Tools: auflegen,") and "ida_fragen" in tools and "memory_save" in tools
        evs = turn("AUFLEGEN")  # derselbe Prozess, dritter Zug
        assert {"t": "tool", "name": "auflegen", "running": True} in evs and evs[-1] == {"t": "done", "ok": True, "hangup": "Alles erledigt."}
        from app.services import voice_bridge
        assert [x["who"] for x in voice_bridge._sessions["pbrain1"].talk] == ["user", "stella"] * 3
        # MCP-Schlüssel gilt nur fürs laufende Gespräch
        token = voice_bridge._sessions["pbrain1"].brain_token
        assert client.post(f"/mcp/voice/{token}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).json()["result"] == {}
        assert client.post("/mcp/voice/falsch", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404
        client.post(f"{API}/voice/internal/finish", headers=SECRET, json={"call_id": "pbrain1", "transcript": []})
        assert client.post(f"/mcp/voice/{token}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404
        from app.services import voice_brain
        assert "pbrain1" not in voice_brain._brains
        evs = turn("Noch da?")
        assert evs[-1]["t"] == "done" and evs[-1]["ok"] is False
    finally:
        client.patch(f"{API}/voice/settings", json={"engine": "gemini"})


def test_brain_for_a_stranger_has_only_hang_up(client):
    client.post(f"{API}/voice/internal/start", headers=SECRET, json={"call_id": "pbrain2", "direction": "out", "number": "+491111", "owner": False})
    client.post(f"{API}/voice/internal/brain/open", headers=SECRET, json={"call_id": "pbrain2", "instructions": "Auftrag."})
    with client.stream("POST", f"{API}/voice/internal/brain/turn", headers=SECRET, json={"call_id": "pbrain2", "text": "TOOLS"}) as r:
        text = "".join(json.loads(x).get("text", "") for x in r.iter_lines() if x)
    assert text == "Tools: auflegen."
    client.post(f"{API}/voice/internal/finish", headers=SECRET, json={"call_id": "pbrain2", "transcript": []})
    wait(lambda: True)
