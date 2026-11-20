"""Stellas eigene Kette (Einstellungen → Stella → Eigene Kette) – Ersatz für Gemini Live mit derselben Schnittstelle wie LiveSession:

  Mikro -> Sprecherkennung (VAD) -> nach einer Pause: Cloudflare Workers AI macht Text daraus
        -> Claude in IDA (voice_brain, ein offener Prozess pro Gespräch, mit allen Tools) antwortet Stück für Stück
        -> jeder fertige Satz geht sofort an Piper und als 24-kHz-PCM an den Browser bzw. das Telefon.

Kosten entstehen nur für Gesagtes: Stille geht nirgends hin. Redet der Nutzer dazwischen, verstummt Stella sofort (der Rest
der Antwort wird verworfen) und Claude erfährt beim nächsten Satz, wo sie unterbrochen wurde.
"""
import asyncio
import collections
import logging
import time
from typing import Any

from . import live, speech
from .ida import Ida
from .vad import EnergyVad, config_from_settings

log = logging.getLogger("ida.voice.pipeline")
IN_RATE = 16000
PREROLL_MS = 400  # so viel vor dem erkannten Sprechbeginn kommt mit (sonst fehlt die erste Silbe)
MIN_UTTERANCE_MS = 300  # kürzer = Knacken, Husten …
MAX_UTTERANCE_S = 60
SYSTEM = "[System, nicht vorlesen] "
TTS_RULES = """
SPRACHAUSGABE (eigene Kette): Was du schreibst, liest eine Sprachausgabe vor, und was der Nutzer sagt, kommt aus einer
Spracherkennung (kann Hörfehler enthalten – deute sie sinnvoll, frag nur bei echter Unklarheit kurz nach).
- Schreib nur gesprochenen Fließtext: kein Markdown, keine Listen, keine Emojis, keine Links, keine Klammern, keine Abkürzungen.
- Kurze Sätze, jeder mit Punkt, Frage- oder Ausrufezeichen am Ende. Der erste Satz kommt sofort und ist kurz.
- Zahlen, Uhrzeiten und Daten so, wie man sie spricht („acht Uhr dreißig“, „am dritten Dezember“).
- Bevor du ein Tool aufrufst, das dauern kann, sag höchstens zwei, drei Wörter („Moment.“, „Ich schau nach.“).
- Nachrichten, die mit „[System“ beginnen, kommen vom System, nicht vom Nutzer.
- Zum Beenden ruf das Tool auflegen auf (nach deinem kurzen Tschüss).
""".strip()


class PipelineSession:
    def __init__(self, settings, *, instructions: str, ida: Ida, call_id: str, engine: dict, sink: live.Sink, kickoff: str,
                 client_vad: bool, farewell_hangup: bool = True, stt_transport: Any = None, tts_transport: Any = None):
        self.s = settings
        self.instructions = instructions + "\n\n" + TTS_RULES
        self.ida = ida
        self.call_id = call_id
        self.engine = engine
        self.sink = sink
        self.kickoff_text = kickoff
        self.client_vad = client_vad
        self.farewell_hangup = farewell_hangup
        self._stt_transport, self._tts_transport = stt_transport, tts_transport
        self.vad = None if client_vad else EnergyVad(config_from_settings(settings))
        self.model = f"{engine.get('stt')} → {engine.get('llm')} → piper:{engine.get('piper_voice')}"
        self.voice = str(engine.get("piper_voice") or "de_DE-thorsten-high")
        self.speed = float(engine.get("piper_speed") or 1.0)
        self.closed = False
        self.transcript: list[dict] = []
        self.outcome = ""
        self.heard_audio = False
        self.first_turn_done = asyncio.Event()
        self._done = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._pre: collections.deque[bytes] = collections.deque()
        self._pre_bytes = 0
        self._rec: bytearray | None = None  # Aufnahme des aktuellen Satzes
        self._finalize: asyncio.Task | None = None
        self._audio_q: asyncio.Queue[bytes] = asyncio.Queue()  # fertige Aufnahmen -> Erkennung
        self._inbox: asyncio.Queue[tuple[str, str]] = asyncio.Queue()  # (who, text) -> Claude
        self._gen = 0  # zählt Unterbrechungen: Sätze einer älteren Antwort werden nicht mehr gesprochen
        self._in_turn = False
        self._cut = ""  # was Stella gesagt hatte, als sie unterbrochen wurde
        self._turn_said = ""
        self._hung = False
        self._last_user = 0.0
        self._ida_waiting: asyncio.Task | None = None
        self._ida_again = False
        self._stt_errors = 0

    # ---------- setup ----------
    async def open(self) -> None:
        if not (self.s.cf_ai_account and self.s.cf_ai_token):
            raise speech.SpeechError("Für die eigene Kette fehlt der Cloudflare-Zugang (CLOUDFLARE_AI_ACCOUNT_ID und CLOUDFLARE_AI_TOKEN in .env)")
        if not self.s.piper_url:
            raise speech.SpeechError("Für die eigene Kette fehlt Piper (PIPER_URL)")
        info = await self.ida.brain_open(self.call_id, self.instructions)
        self.model = f"{self.engine.get('stt')} → {info.get('model')} → piper:{self.voice}"
        log.info("Eigene Kette %s: %s", self.call_id, self.model)
        self._spawn(self._stt_worker())
        self._spawn(self._turn_worker())

    async def kickoff(self) -> None:
        await self._inbox.put(("system", self.kickoff_text))

    # ---------- input ----------
    async def send_audio(self, pcm16k: bytes) -> None:
        if self.closed:
            return
        if self.vad is not None:  # kein Erkennen von außen: selbst entscheiden, wann gesprochen wird
            for ev in self.vad.feed(pcm16k):
                if ev == "start":
                    await self.activity_start()
                elif ev == "end":
                    await self.activity_end()
        if self._rec is not None:
            self._rec += pcm16k
            if len(self._rec) > MAX_UTTERANCE_S * IN_RATE * 2:
                await self._end_utterance()
            return
        self._pre.append(pcm16k)
        self._pre_bytes += len(pcm16k)
        while self._pre and self._pre_bytes - len(self._pre[0]) >= PREROLL_MS * IN_RATE * 2 // 1000:
            self._pre_bytes -= len(self._pre.popleft())

    async def activity_start(self) -> None:
        self._last_user = time.monotonic()
        if self._finalize and not self._finalize.done():  # weitergesprochen: derselbe Satz
            self._finalize.cancel()
            self._finalize = None
        if self._rec is None:
            self._rec = bytearray(b"".join(self._pre))
            self._pre.clear()
            self._pre_bytes = 0
        await self._interrupt()

    async def activity_end(self) -> None:
        self._last_user = time.monotonic()
        if self._rec is None:
            return
        if self._finalize and not self._finalize.done():
            self._finalize.cancel()

        async def later():
            await asyncio.sleep(self.s.voice_pipeline_pause_ms / 1000)
            await self._end_utterance()
        self._finalize = self._spawn(later())

    def user_spoke(self) -> None:
        self._last_user = time.monotonic()

    async def say(self, text: str) -> None:
        """Getippter Text aus dem Browser."""
        self._note("user", text)
        await self._interrupt()
        await self._inbox.put(("user", text))

    async def _end_utterance(self) -> None:
        rec, self._rec = self._rec, None
        if rec is None or len(rec) < MIN_UTTERANCE_MS * IN_RATE * 2 // 1000:
            return
        await self._audio_q.put(bytes(rec))

    async def _interrupt(self) -> None:
        """Der Nutzer redet: Stella hört sofort auf (nur wenn sie gerade spricht oder antwortet)."""
        if not (self._in_turn or self._playing()):
            return
        self._gen += 1
        if self._turn_said.strip():
            self._cut = self._turn_said.strip()
        await self.sink.interrupted()

    def _playing(self) -> bool:
        try:
            return float(getattr(self.sink, "play_end", 0.0) or 0.0) > time.monotonic()
        except (TypeError, ValueError):
            return False

    # ---------- Erkennung ----------
    async def _stt_worker(self) -> None:
        while not self.closed:
            pcm = await self._audio_q.get()
            t0 = time.monotonic()
            try:
                text = await speech.transcribe(self.s, str(self.engine.get("stt")), pcm, transport=self._stt_transport)
                self._stt_errors = 0
            except Exception as e:  # noqa: BLE001
                log.warning("Erkennung fehlgeschlagen: %s", e)
                self._stt_errors += 1
                if self._stt_errors in (1, 5):  # einmal sagen, nicht bei jedem Satz
                    await self._inbox.put(("system", "Die Spracherkennung hatte gerade einen Fehler, der Nutzer wurde nicht verstanden. "
                                                     "Sag ihm das in einem kurzen Satz und bitte ihn, es nochmal zu sagen."))
                continue
            log.info("voice stt ms=%d chars=%d", (time.monotonic() - t0) * 1000, len(text))
            if not text:
                continue
            self._note("user", text)
            await self.sink.text("user", text)
            await self._inbox.put(("user", text))

    # ---------- Claude ----------
    async def _turn_worker(self) -> None:
        while not self.closed:
            who, text = await self._inbox.get()
            if who == "user":  # was inzwischen noch gesagt wurde, kommt mit in denselben Zug
                more = []
                while not self._inbox.empty() and self._inbox._queue[0][0] == "user":  # type: ignore[attr-defined]
                    more.append(self._inbox.get_nowait()[1])
                text = " ".join([text, *more])
                if self._cut:
                    text = f"[System: Du wurdest unterbrochen, gesagt hattest du bis dahin nur: „{self._cut[-300:]}“]\n{text}"
                    self._cut = ""
            else:
                text = SYSTEM + text
            try:
                await self._turn(who, text)
            except Exception as e:  # noqa: BLE001
                log.warning("Zug fehlgeschlagen: %s", e)
                self._in_turn = False
                await self._speak("Da ist gerade etwas schiefgelaufen. Sag es bitte nochmal.", self._gen)

    async def _turn(self, who: str, text: str) -> None:
        gen = self._gen
        self._in_turn = True
        self._turn_said = ""
        buf, hangup, ok, error = "", "", True, ""
        t0, first = time.monotonic(), None
        async for ev in self.ida.brain_turn(self.call_id, text, "system" if who == "system" else "user"):
            t = ev.get("t")
            if t == "text":
                first = first or time.monotonic() - t0
                buf += str(ev.get("text") or "")
                done, buf = speech.split_sentences(buf)
                for sentence in done:
                    await self._speak(sentence, gen)
            elif t == "tool":
                name = str(ev.get("name") or "")
                await self.sink.tool(name, bool(ev.get("running")))
                if ev.get("running") and name == "ida_fragen":
                    self._ida_pending()
            elif t == "done":
                ok, error, hangup = bool(ev.get("ok")), str(ev.get("error") or ""), str(ev.get("hangup") or "")
        if buf.strip():
            await self._speak(buf, gen)
        log.info("voice turn first_text_ms=%s total_ms=%d ok=%s", int(first * 1000) if first else "-", (time.monotonic() - t0) * 1000, ok)
        said, self._turn_said = self._turn_said, ""
        self._in_turn = False
        self.first_turn_done.set()
        if not ok and not said.strip() and gen == self._gen:
            log.warning("Claude: %s", error)
            await self._speak("Ich komme gerade nicht an mein Gehirn. Versuch es gleich nochmal.", gen)
        if hangup:
            self._hang_up(hangup)
        elif self.farewell_hangup and gen == self._gen and live.FAREWELL.search(said):
            self._spawn(self._farewell(time.monotonic()))

    async def _speak(self, text: str, gen: int) -> None:
        text = speech.speakable(text)
        if not text or gen != self._gen or self.closed:
            return
        try:
            pcm = await speech.synthesize(self.s, text, self.voice, self.speed, transport=self._tts_transport)
        except Exception as e:  # noqa: BLE001 – dann wenigstens als Text
            log.warning("Sprachausgabe fehlgeschlagen: %s", e)
            pcm = b""
        if gen != self._gen or self.closed:
            return
        if pcm:
            self.heard_audio = True
            await self.sink.audio(pcm, speech.OUT_RATE)
        self._turn_said += text + " "
        self._note("stella", text + " ")
        await self.sink.text("stella", text + " ")

    # ---------- Ida im Hintergrund ----------
    def _ida_pending(self) -> None:
        self._ida_again = True
        if not (self._ida_waiting and not self._ida_waiting.done()):
            self._ida_waiting = self._spawn(self._await_ida())

    async def _await_ida(self) -> None:
        misses = 0
        for _ in range(45):  # höchstens rund 15 Minuten
            if self.closed or self._hung:
                return
            self._ida_again = False
            try:
                r = await self.ida.tool(self.call_id, "ida_warten", {})
            except Exception:  # noqa: BLE001
                r = {"error": "nicht erreichbar"}
            if "answer" in r:
                misses = 0
                answer = str(r.get("answer") or "")
                self._note("ida", answer)
                await self.sink.text("ida", answer)
                await self._quiet()
                await self._inbox.put(("system", live.LiveSession._ida_turn(self, answer, bool(r.get("ok")))))  # type: ignore[arg-type]
            elif r.get("gone"):
                if not self._ida_again:
                    return
            elif "error" in r:
                misses += 1
                if misses >= 3:
                    return
                await asyncio.sleep(3)

    async def _quiet(self) -> None:
        """Idas Antwort erst in einer Pause: Stella fertig, der Nutzer still (höchstens 45 s warten)."""
        start = time.monotonic()
        while not self.closed and time.monotonic() - start < live.QUIET_HARD_S:
            now = time.monotonic()
            if (not self._in_turn and not self._playing() and self._rec is None and self._inbox.empty()
                    and now - self._last_user >= live.USER_QUIET_S):
                return
            await asyncio.sleep(0.1)

    # ---------- Ende ----------
    async def _farewell(self, since: float) -> None:
        """Tschüss gesagt, aber nicht aufgelegt: auflegen, wenn danach niemand mehr etwas sagt."""
        while self._playing() and not self.closed:
            await asyncio.sleep(0.1)
        await asyncio.sleep(1.5)
        if self._last_user <= since and self._rec is None and not self._in_turn and not (self._ida_waiting and not self._ida_waiting.done()):
            self._hang_up("Nach der Verabschiedung beendet.")

    def _hang_up(self, outcome: str) -> None:
        if self._hung:
            return
        self._hung = True
        self.outcome = self.outcome or outcome
        self.sink.hang_up(self.outcome)

    def _note(self, who: str, text: str) -> None:
        if self.transcript and self.transcript[-1]["who"] == who:
            self.transcript[-1]["text"] += text
        else:
            self.transcript.append({"who": who, "text": text})

    def history(self) -> list[dict]:
        return live.LiveSession.history(self)  # type: ignore[arg-type]

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def run(self) -> None:
        await self._done.wait()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.first_turn_done.set()
        for t in list(self._tasks):
            t.cancel()
        self._done.set()
        await self.ida.brain_close(self.call_id)


def make_session(settings, info: dict, *, ida: Ida, call_id: str, instructions: str, tools: list[dict], sink, kickoff: str,
                 client_vad: bool, connect=None, voice: str | None = None, farewell_hangup: bool = True):
    """Gemini Live oder die eigene Kette – je nach Einstellungen → Stella (info["engine"] von IDA)."""
    engine = info.get("engine") or {}
    if engine.get("engine") == "pipeline" and not info.get("offline"):
        return PipelineSession(settings, instructions=instructions, ida=ida, call_id=call_id, engine=engine, sink=sink, kickoff=kickoff,
                               client_vad=client_vad, farewell_hangup=farewell_hangup)
    return live.LiveSession(settings, instructions=instructions, tools=tools, run_tool=lambda n, a: ida.tool(call_id, n, a), sink=sink,
                            kickoff=kickoff, client_vad=client_vad, farewell_hangup=farewell_hangup, connect=connect, voice=voice)
