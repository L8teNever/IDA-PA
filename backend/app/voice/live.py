"""One Gemini Live conversation (BidiGenerateContent over WebSocket), independent of where the audio comes from.

The transport (phone via Telnyx or the browser) feeds 16 kHz PCM in and gets Gemini's 24 kHz PCM back through a Sink.
Tool calls go to IDA (ida.Ida.tool) in their own tasks, so audio keeps flowing while a tool runs.
Setup details (models, thinking config, VAD) are taken from the old Stella, where they were tested against the real API.
"""
import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlencode

from . import prompt

log = logging.getLogger("ida.voice.live")
SETUP_TIMEOUT_S = 8.0
OUT_RATE = 24000
_TYPES = {"object": "OBJECT", "string": "STRING", "integer": "INTEGER", "number": "NUMBER", "boolean": "BOOLEAN", "array": "ARRAY"}
_NAME = re.compile(r"[^A-Za-z0-9_]+")
FAREWELL = re.compile(r"(tsch+ü+ss|tschuss|auf wiederh[öo]ren|auf wiedersehen|\bbye\b|ciao|bis (?:bald|später|dann|zum nächsten mal)|"
                      r"mach(?:'s| es) gut|pass auf dich auf|wir hören voneinander|ich bin dann mal weg)", re.IGNORECASE)
# Stella kündigt etwas an oder behauptet, es sei erledigt („ich gebe das an Ida weiter“, „ich speichere das“) – Gemini
# sagt das oft, ohne ein Tool aufzurufen. Dann passiert nichts. Ohne Tool-Aufruf in diesem Zug wird sie darauf hingewiesen.
PROMISE = re.compile(
    r"(\bgebe? (?:ich )?(?:das|es|dies|den auftrag)\b.{0,40}\bweiter|\ban ida (?:weiter|ab)|\bida (?:kümmert|wird|macht|erledigt|schaut|sucht|übernimmt|"
    r"meldet|ist dran|hat (?:es|das)\b)|\b(?:sie|ida) (?:schaut|kümmert|meldet|sucht|macht|erledigt)\b|\bübergeb\w* (?:ich )?(?:das|es)|"
    r"\b(?:sag\w*|geb\w*) (?:ich )?dir bescheid|\b(?:leg|speicher|schreib|trag|schick|send|erstell|änder|notier)\w* ich\b|\bmelde mich\b|\bhab(?:e)? (?:ich )?ida (?:gebeten|beauftragt|gesagt|bescheid)|\bich (?:speicher|erstell|leg\w* (?:das|es|die|den|eine?n?)\b"
    r"|trag\w* (?:das|es)|schreib\w* (?:das|es|dir)|notier|merk\w* (?:mir|das)|schick|send|änder|aktualisier|such|prüf|schau\w* (?:mal |kurz |gleich )?nach"
    r"|kümmer|frag\w* (?:mal |kurz |gleich )?ida)|\bwird erledigt\b|\bist erledigt\b|\bhab(?:e)? (?:ich )?(?:es|das|sie|ihn|die \w+) (?:\w+ )?"
    r"(?:gespeichert|erstellt|angelegt|eingetragen|notiert|geschickt|gesendet|geändert|weitergegeben|abgelegt|aufgeschrieben)\b)",
    re.IGNORECASE)
# … außer der Zug endet mit einer Rückfrage um Erlaubnis („Soll ich das tun?“) – dann wartet sie zu Recht auf das Ja
ASKS_YES = re.compile(r"(?:(?:\bsoll\w*\b|möchtest du|willst du|darf ich|einverstanden|passt (?:das|so)|ist das (?:so )?(?:ok|okay|gut|richtig|besser))"
                      r"[^.!?]*\?|(?:okay|ok|oder|richtig|besser(?: jetzt| so)?)\?)\s*$", re.IGNORECASE)
PROMISE_WAIT_S = 2.5  # so lange darf der Tool-Aufruf nach dem Satz noch kommen
MAX_NUDGES = 4
HISTORY_LINES = 24  # so viele Zeilen der Mitschrift bekommt Ida bei ida_fragen mit
HISTORY_CHARS = 5000
QUIET_S = 0.7  # Idas Antwort kommt in die nächste Sprechpause (Stellas Satz ist zu Ende, der Nutzer still) …
QUIET_MAX_S = 8.0  # … aber nie später als das
SILENT_AFTER_IDA_S = 3.5  # so lange darf es nach ida_fragen still bleiben, dann wird Stella angestoßen


class Sink(Protocol):
    async def audio(self, pcm: bytes, rate: int) -> None: ...
    async def interrupted(self) -> None: ...
    async def text(self, who: str, piece: str) -> None: ...
    async def tool(self, name: str, running: bool) -> None: ...
    def hang_up(self, outcome: str) -> None: ...


def to_gemini_schema(schema: Any, depth: int = 0) -> dict:
    """JSON Schema (MCP) -> Gemini function-declaration schema."""
    if not isinstance(schema, dict) or depth > 6:
        return {"type": "OBJECT", "properties": {}} if depth == 0 else {"type": "STRING"}
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if isinstance(t, str):
        out["type"] = _TYPES.get(t.lower(), "STRING")
    if isinstance(schema.get("description"), str) and schema["description"].strip():
        out["description"] = schema["description"].strip()[:500]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        out["enum"] = [str(x) for x in schema["enum"][:30]]
    if isinstance(schema.get("properties"), dict):
        out["properties"] = {str(k)[:64]: to_gemini_schema(v if isinstance(v, dict) else {}, depth + 1)
                             for k, v in list(schema["properties"].items())[:32]}
    if isinstance(schema.get("required"), list) and schema["required"]:
        props = out.get("properties", {})
        out["required"] = [str(x) for x in schema["required"] if str(x) in props]
        if not out["required"]:
            del out["required"]
    if isinstance(schema.get("items"), dict):
        out["items"] = to_gemini_schema(schema["items"], depth + 1)
    if "type" not in out:
        out["type"] = "OBJECT" if "properties" in out else "STRING"
    if out["type"] == "OBJECT":
        out.setdefault("properties", {})
    if out["type"] == "ARRAY" and "items" not in out:
        out["items"] = {"type": "STRING"}
    return out


def declarations(tools: list[dict]) -> list[dict]:
    decl = [prompt.hang_up_tool()]
    seen = {"auflegen"}
    for t in tools:
        name = _NAME.sub("_", str(t.get("name") or ""))[:64]
        if not name or name in seen:
            continue
        seen.add(name)
        decl.append({"name": name, "description": str(t.get("description") or "")[:900], "parameters": to_gemini_schema(t.get("parameters"))})
    return decl


def models(settings) -> list[str]:
    out: list[str] = []
    for m in [settings.gemini_live_model, *settings.gemini_live_fallbacks.split(",")]:
        m = m.strip().removeprefix("models/")
        if m and m not in out:
            out.append(m)
    return out


def generation_config(settings, model: str, voice: str | None = None) -> dict:
    cfg: dict = {"responseModalities": ["AUDIO"],
                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice or "Aoede"}}}}
    budget = int(settings.gemini_thinking_budget)
    if budget >= 0:
        # 3.1 Live takes thinkingLevel; 3.8 and 2.5 take thinkingBudget – both at once is rejected (close 1007)
        if model.startswith("gemini-3") and not model.startswith("gemini-3.8"):
            cfg["thinkingConfig"] = {"thinkingLevel": "minimal" if budget == 0 else "low"}
        else:
            cfg["thinkingConfig"] = {"thinkingBudget": budget}
    return cfg


def realtime_config(client_vad: bool) -> dict:
    if client_vad:  # phone: our own VAD sends activityStart/activityEnd (only accepted with automatic detection off)
        return {"automaticActivityDetection": {"disabled": True}, "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY"}
    return {"automaticActivityDetection": {"disabled": False, "prefixPaddingMs": 40, "silenceDurationMs": 500},
            "activityHandling": "START_OF_ACTIVITY_INTERRUPTS"}


async def _ws_connect(url: str):
    from websockets.asyncio.client import connect
    return await connect(url, max_size=None, ping_interval=20)


ToolRunner = Callable[[str, dict], Awaitable[dict]]


class LiveSession:
    def __init__(self, settings, *, instructions: str, tools: list[dict], run_tool: ToolRunner, sink: Sink, kickoff: str,
                 client_vad: bool, farewell_hangup: bool = True, connect: Callable[[str], Awaitable[Any]] | None = None,
                 voice: str | None = None):
        self.s = settings
        self.voice = voice or "Aoede"
        self.instructions = instructions
        self.decl = declarations(tools)
        self.run_tool = run_tool
        self.sink = sink
        self.kickoff_text = kickoff
        self.client_vad = client_vad
        self.farewell_hangup = farewell_hangup
        self._connect = connect or _ws_connect
        self.ws = None
        self.model = ""
        self.closed = False
        self.transcript: list[dict] = []
        self.outcome = ""
        self.heard_audio = False
        self.first_turn_done = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._farewell: asyncio.Task | None = None
        self._hung = False
        self._ida_waiting: asyncio.Task | None = None  # Ida arbeitet im Hintergrund: holt ihre Antworten ab
        self._ida_again = False  # neuer Auftrag, während das Abholen gerade endet
        self._last_voice = 0.0  # wann zuletzt jemand gesprochen hat
        self._in_turn = False  # Stella ist mitten in einem Zug
        self._tools_open = 0  # Tool-Aufrufe, auf deren Antwort Gemini gerade wartet
        self.has_tools = any(d["name"] != "auflegen" for d in self.decl)
        self._turn_said = ""  # was Stella in diesem Zug gesagt hat (für die Ankündigungs-Prüfung)
        self._tool_seq = 0  # zählt Tool-Aufrufe
        self._user_seq = 0  # zählt, wann der Nutzer zuletzt etwas gesagt hat
        self._turn_tools = 0  # Tool-Zähler zu Beginn des Zugs
        self.nudges = 0

    # ---------- setup ----------
    def setup_payload(self, model: str) -> dict:
        return {"setup": {
            "model": f"models/{model}",
            "generationConfig": generation_config(self.s, model, voice=self.voice),
            "systemInstruction": {"parts": [{"text": self.instructions}]},
            "tools": [{"functionDeclarations": self.decl}],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "realtimeInputConfig": realtime_config(self.client_vad),
            "contextWindowCompression": {"slidingWindow": {}},  # longer conversations than the plain session limit
        }}

    async def open(self) -> None:
        if not (self.s.gemini_api_key or "").strip():
            raise RuntimeError("GEMINI_API_KEY fehlt")
        url = self.s.gemini_live_url.rstrip("?") + "?" + urlencode({"key": self.s.gemini_api_key})
        last: Exception | None = None
        for model in models(self.s):  # fallbacks only for the setup handshake, never mid-call
            try:
                self.ws = await self._connect(url)
                await self.ws.send(json.dumps(self.setup_payload(model)))
                await asyncio.wait_for(self._await_setup(), SETUP_TIMEOUT_S)
                self.model = model
                log.info("Gemini Live: Modell %s, Stimme %s", model, self.voice)
                if model != models(self.s)[0]:
                    log.warning("Gemini Live: Ausweichmodell %s", model)
                return
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("Gemini Live setup %s fehlgeschlagen: %s", model, e)
                if self.ws is not None:
                    try:
                        await self.ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self.ws = None
        raise RuntimeError(f"Gemini Live nicht erreichbar: {last}")

    async def _await_setup(self) -> None:
        async for raw in self.ws:
            ev = _decode(raw)
            if ev is None:
                continue
            if ev.get("error"):
                raise RuntimeError(f"Setup-Fehler: {ev['error']}")
            if "setupComplete" in ev:
                return
        raise RuntimeError("Gemini hat vor setupComplete geschlossen")

    # ---------- input ----------
    async def _send(self, msg: dict) -> None:
        if self.closed or self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001 – the pump notices the closed socket
            pass

    async def kickoff(self) -> None:
        await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": self.kickoff_text}]}], "turnComplete": True}})

    async def send_audio(self, pcm16k: bytes) -> None:
        await self._send({"realtimeInput": {"audio": {"data": base64.b64encode(pcm16k).decode("ascii"), "mimeType": "audio/pcm;rate=16000"}}})

    async def activity_start(self) -> None:
        self._last_voice = time.monotonic()
        self._cancel_farewell()
        await self._send({"realtimeInput": {"activityStart": {}}})

    async def activity_end(self) -> None:
        await self._send({"realtimeInput": {"activityEnd": {}}})

    def user_spoke(self) -> None:
        self._last_voice = time.monotonic()
        self._cancel_farewell()

    async def say(self, text: str) -> None:
        """Text from outside into the conversation (e.g. typed in the browser)."""
        self._note("user", text)
        await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": text}]}], "turnComplete": True}})

    # ---------- output ----------
    async def run(self) -> None:
        try:
            async for raw in self.ws:
                if self.closed:
                    break
                ev = _decode(raw)
                if ev is not None:
                    await self._event(ev)
        except Exception as e:  # noqa: BLE001
            if not self.closed:
                log.info("Gemini Live beendet: %s", e)
        finally:
            await self.close()

    async def _event(self, ev: dict) -> None:
        if ev.get("error"):
            log.error("Gemini Live Fehler: %s", ev["error"])
            return
        if "goAway" in ev:
            log.info("Gemini Live goAway: %s", ev["goAway"])
        sc = ev.get("serverContent") or {}
        if sc.get("interrupted"):
            self._turn_said = ""
            self._in_turn = False
            await self.sink.interrupted()
        for part in (sc.get("modelTurn") or {}).get("parts") or []:
            inline = part.get("inlineData") or {}
            if inline.get("data") and "audio" in (inline.get("mimeType") or "").lower():
                self.heard_audio = self._in_turn = True
                self._last_voice = time.monotonic()
                m = re.search(r"rate=(\d+)", inline.get("mimeType") or "")
                await self.sink.audio(base64.b64decode(inline["data"]), int(m.group(1)) if m else OUT_RATE)
            # part.text is often the model's scratch work, not what it said – the spoken text is outputTranscription
        heard = (sc.get("inputTranscription") or {}).get("text") or ""
        if heard:
            self._last_voice = time.monotonic()
            self._user_seq += 1
            self._note("user", heard)
            await self.sink.text("user", heard)
        said = (sc.get("outputTranscription") or {}).get("text") or ""
        if said:
            self.heard_audio = self._in_turn = True
            self._last_voice = time.monotonic()
            if not self._turn_said:
                self._turn_tools = self._tool_seq
            self._turn_said += said
            self._note("stella", said)
            await self.sink.text("stella", said)
            if self.farewell_hangup and FAREWELL.search(said):
                self._arm_farewell()
        if self.heard_audio and (sc.get("turnComplete") or sc.get("generationComplete")):
            self.first_turn_done.set()
        if sc.get("turnComplete"):
            self._in_turn = False
            said_turn, self._turn_said = self._turn_said, ""
            if said_turn and self.has_tools and self._tool_seq == self._turn_tools and self.nudges < MAX_NUDGES and unkept_promise(said_turn):
                self._spawn(self._check_promise(said_turn, self._tool_seq, self._user_seq))
        for fc in (ev.get("toolCall") or {}).get("functionCalls") or []:
            self._tool_seq += 1
            self._spawn(self._tool(fc))
        for cid in (ev.get("toolCallCancellation") or {}).get("ids") or []:
            log.info("Gemini hat Tool-Aufruf %s abgebrochen", cid)  # z. B. der Nutzer redet weiter; ida_fragen trifft das nicht, Ida läuft im Hintergrund

    async def _check_promise(self, said: str, tools: int, user: int) -> None:
        """Stella hat etwas angekündigt, aber kein Tool aufgerufen: kurz warten (der Aufruf kann noch kommen), dann erinnern."""
        await asyncio.sleep(PROMISE_WAIT_S)
        if self.closed or self._hung or self._tool_seq != tools or self._user_seq != user or self.nudges >= MAX_NUDGES:
            return
        if self._ida_waiting and not self._ida_waiting.done():
            return  # Ida arbeitet schon daran
        self.nudges += 1
        self._cancel_farewell()
        log.info("voice: Ankündigung ohne Tool – Stella wird erinnert (%d)", self.nudges)
        quote = " ".join(said.split())[-240:]
        await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": (
            f"[System, nicht vorlesen] Du hast gerade gesagt: „{quote}“ – aber kein Tool aufgerufen, es ist also noch NICHTS passiert. "
            "Hol das jetzt sofort nach: ruf das passende Tool auf oder ida_fragen mit dem kompletten Auftrag (Ida sieht den Gesprächsverlauf). "
            "Sag nicht, dass etwas erledigt ist oder Ida sich kümmert, bevor ein Tool es bestätigt hat. Entschuldige dich nicht lang, "
            "sag höchstens kurz „Moment“.")}]}], "turnComplete": True}})

    def history(self) -> list[dict]:
        """Die letzten Zeilen der Mitschrift (für Ida, damit sie Bezüge wie „von vorhin“ versteht)."""
        out, size = [], 0
        for x in reversed(self.transcript):
            if x["who"] not in ("user", "stella", "ida"):
                continue
            text = " ".join(str(x.get("text") or "").split())[:1200]
            if not text:
                continue
            size += len(text)
            if out and (len(out) >= HISTORY_LINES or size > HISTORY_CHARS):
                break
            out.append({"who": x["who"], "text": text})
        return out[::-1]

    def _note(self, who: str, text: str) -> None:
        if self.transcript and self.transcript[-1]["who"] == who:
            self.transcript[-1]["text"] += text
        else:
            self.transcript.append({"who": who, "text": text})

    async def _tool(self, fc: dict) -> None:
        name = str(fc.get("name") or "")
        args = fc.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if name == "auflegen":
            self.outcome = str((args or {}).get("ergebnis") or "Gespräch beendet.")
            await self._respond(fc, name, {"result": "ok, wird aufgelegt"})
            self._hang_up(self.outcome)
            return
        if name == "ida_fragen":  # im Chat soll zu sehen sein, dass Stella Ida gefragt hat
            question = str((args or {}).get("frage") or "") if isinstance(args, dict) else ""
            args = {**args, "verlauf": self.history()} if isinstance(args, dict) else {"verlauf": self.history()}
            self._note("ida_frage", question)
            await self.sink.text("ida_frage", question)
        await self.sink.tool(name, True)
        self._tools_open += 1
        t0 = time.monotonic()
        try:
            out = await self.run_tool(name, args if isinstance(args, dict) else {})
        except Exception as e:  # noqa: BLE001
            log.exception("Tool %s", name)
            out = {"error": f"Das Tool ist gerade nicht erreichbar ({e.__class__.__name__})."}
        log.info("voice tool=%s ms=%d ok=%s", name, (time.monotonic() - t0) * 1000, "error" not in out)
        await self.sink.tool(name, False)
        pending = bool(out.pop("ida_pending", False)) if isinstance(out, dict) else False
        await self._respond(fc, name, out)
        self._tools_open -= 1
        if pending:
            self._spawn(self._keep_talking(self._last_voice))
            self._ida_again = True
            if not (self._ida_waiting and not self._ida_waiting.done()):
                self._ida_waiting = self._spawn(self._await_ida())

    async def _await_ida(self) -> None:
        """Holt Idas Antworten ab und gibt sie Stella mitten ins Gespräch, damit sie sie gleich sagt."""
        misses = 0
        for _ in range(45):  # höchstens rund 15 Minuten
            if self.closed or self._hung:
                return
            self._ida_again = False
            try:
                r = await self.run_tool("ida_warten", {})
            except Exception:  # noqa: BLE001
                r = {"error": "nicht erreichbar"}
            if "answer" in r:
                misses = 0
                answer = str(r.get("answer") or "")
                self._note("ida", answer)
                await self.sink.text("ida", answer)
                await self._quiet()
                head = "[Ida hat jetzt geantwortet]" if r.get("ok") else "[Ida konnte die Frage nicht beantworten]"
                await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": f"{head} {answer}\n\nDamit ist sie mit diesem Auftrag "
                                  "fertig, was du vorher über ihren Stand gesagt hast, ist überholt. Unterbrich kurz („Ida ist fertig: …“). Lies Idas Text "
                                  "nicht vor: Überleg, was davon für den Nutzer jetzt wirklich zählt (Ergebnis zuerst, nur Wichtiges wie Fristen, Konflikte "
                                  "oder eine offene Frage an ihn), und sag das nach deinen üblichen Regeln in ein, zwei kurzen Sätzen. Dann mach genau da "
                                  "weiter, wo ihr gerade wart."}]}],
                                  "turnComplete": True}})
            elif r.get("gone"):
                if not self._ida_again:
                    return
            elif "error" in r:
                misses += 1
                if misses >= 3:
                    return
                await asyncio.sleep(3)

    async def _keep_talking(self, since: float) -> None:
        """Nach ida_fragen soll Stella weiterreden, nicht still auf Ida warten: bleibt es stumm, kurz anstoßen."""
        await asyncio.sleep(SILENT_AFTER_IDA_S)
        if self.closed or self._hung or self._in_turn or self._tools_open or self._last_voice != since:
            return
        log.info("voice: nach ida_fragen still – Stella wird angestoßen")
        await self._send({"clientContent": {"turns": [{"role": "user", "parts": [{"text": (
            "[System, nicht vorlesen] Ida arbeitet im Hintergrund, ihre Antwort kommt von selbst. Warte nicht still: sag kurz, "
            "dass Ida dran ist, und mach mit dem Nutzer normal weiter.")}]}], "turnComplete": True}})

    async def _quiet(self) -> None:
        """Wartet auf eine Sprechpause, damit die Antwort niemandem ins Wort fällt."""
        end = time.monotonic() + QUIET_MAX_S
        while not self.closed and time.monotonic() < end:
            if not self._in_turn and not self._tools_open and time.monotonic() - self._last_voice >= QUIET_S:
                return
            await asyncio.sleep(0.1)

    async def _respond(self, fc: dict, name: str, response: dict) -> None:
        await self._send({"toolResponse": {"functionResponses": [{"id": fc.get("id"), "name": name, "response": response}]}})

    def _hang_up(self, outcome: str) -> None:
        if self._hung:
            return
        self._hung = True
        self.outcome = self.outcome or outcome
        self.sink.hang_up(self.outcome)

    # Gemini sometimes says goodbye without calling auflegen: hang up if the other side stays quiet afterwards
    def _arm_farewell(self) -> None:
        if (self._farewell and not self._farewell.done()) or (self._ida_waiting and not self._ida_waiting.done()):
            return  # Ida arbeitet noch: kein Auflegen nach einem „bis dann“

        async def fire():
            await asyncio.sleep(1.5)
            self._hang_up("Nach der Verabschiedung beendet.")
        self._farewell = self._spawn(fire())

    def _cancel_farewell(self) -> None:
        if self._farewell and not self._farewell.done():
            self._farewell.cancel()
        self._farewell = None

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.first_turn_done.set()
        for t in list(self._tasks):
            t.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass


def unkept_promise(said: str) -> bool:
    """Kündigt der Satz eine Aktion an (oder behauptet sie), ohne um Erlaubnis zu fragen?"""
    said = " ".join((said or "").split())
    return bool(PROMISE.search(said)) and not ASKS_YES.search(said)


def _decode(raw) -> dict | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        ev = json.loads(raw)
    except ValueError:
        return None
    return ev if isinstance(ev, dict) else None
