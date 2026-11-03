"""Ersatz für Gemini Live (in-process statt WebSocket): ein kleines Drehbuch je Gespräch.

1. setup -> setupComplete (zeichnet das Setup auf)
2. Begrüßung (kickoff) -> gesprochener Text + Audio + turnComplete
3. erstes Audio des Nutzers -> Transkript „Merk dir …“ und Tool-Aufruf (script["tool"])
4. toolResponse -> zeichnet sie auf; braucht das Tool ein Ja, kommt ein zweiter Aufruf mit bestaetigt=true
5. zuletzt „Erledigt, tschüss!“ + auflegen
"""
import asyncio
import base64
import json


class FakeGeminiWS:
    def __init__(self, owner):
        self.owner = owner
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.heard = False
        self.asked_yes = False
        owner.sockets.append(self)

    async def send(self, raw: str):
        m = json.loads(raw)
        self.sent.append(m)
        o = self.owner
        if "setup" in m:
            o.setups.append(m["setup"])
            if m["setup"]["model"].removeprefix("models/") in o.fail_models:
                await self.q.put({"error": {"message": "unknown model"}})
            else:
                await self.q.put({"setupComplete": {}})
        elif "clientContent" in m:
            await self.q.put({"serverContent": {"outputTranscription": {"text": "Na, was gibt's?"},
                                                "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                                                                         "data": base64.b64encode(b"\x01\x00" * 480).decode()}}]}}})
            await self.q.put({"serverContent": {"turnComplete": True}})
        elif "realtimeInput" in m and "audio" in m["realtimeInput"] and not self.heard:
            self.heard = True
            name, args = o.tool
            await self.q.put({"serverContent": {"inputTranscription": {"text": "Merk dir, ich mag Pizza."}}})
            await self.q.put({"toolCall": {"functionCalls": [{"id": "t1", "name": name, "args": args}]}})
        elif "toolResponse" in m:
            r = m["toolResponse"]["functionResponses"][0]
            o.responses.append(r)
            if r["name"] == "auflegen":
                return
            text = json.dumps(r["response"], ensure_ascii=False)
            if "BESTAETIGUNG_NOETIG" in text and not self.asked_yes:
                self.asked_yes = True
                name, args = o.tool
                await self.q.put({"toolCall": {"functionCalls": [{"id": "t2", "name": name, "args": {**args, "bestaetigt": True}}]}})
                return
            await self.q.put({"serverContent": {"outputTranscription": {"text": "Erledigt, tschüss!"}}})
            await self.q.put({"toolCall": {"functionCalls": [{"id": "t3", "name": "auflegen", "args": {"ergebnis": "Pizza gemerkt"}}]}})

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        ev = await self.q.get()
        if ev is None:
            raise StopAsyncIteration
        return json.dumps(ev)

    async def close(self):
        self.closed = True
        await self.q.put(None)


class FakeGemini:
    def __init__(self):
        self.reset()

    def reset(self, tool=("memory_save", {"text": "Der Nutzer mag Pizza."})):
        self.setups: list[dict] = []
        self.responses: list[dict] = []
        self.sockets: list[FakeGeminiWS] = []
        self.fail_models: set[str] = set()
        self.tool = tool

    async def connect(self, url: str):
        self.urls = getattr(self, "urls", []) + [url]
        return FakeGeminiWS(self)
