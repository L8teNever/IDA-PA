"""Phone calls via Telnyx: outbound (stella_call), inbound (only the owner's number is answered), and the audio bridge
Telnyx media stream (G.711 8 kHz) <-> Gemini Live (PCM 16/24 kHz).

Two modes, decided only by the number on the line:
  owner (STELLA_OWNER_NUMBER):  IDA context + tools.
  anybody else:                 inbound is never answered; outbound (Ida called someone) gets only the brief, no tools.
Phone tuning (own VAD against engine noise, echo gate, greeting lock, hang up only after the audio has played) comes
from the old Stella.
"""
import asyncio
import base64
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field

from . import prompt
from .audio import GEMINI_OUT_RATE, PcmToPcmu8k, Pcmu8kToPcm16k, codec_from_telnyx_payload, normalize_g711_codec
from .ida import Ida
from .live import LiveSession
from .telnyx import Telnyx, TelnyxError, client_state, e164, read_client_state, same_number
from .vad import EnergyVad, config_from_settings, pcm_rms

log = logging.getLogger("ida.voice.phone")
ACTIVE = ("dialing", "ringing", "live")
FRAME_MS, FRAME_BYTES = 20, 160
PLAYOUT_PAD_S, PLAYOUT_CAP_S, NO_AUDIO_WAIT_S = 0.35, 12.0, 8.0
DIAL_TIMEOUT_S = 120


class CallError(Exception):
    pass


@dataclass
class Call:
    id: str
    direction: str  # out | in
    number: str
    owner: bool
    brief: str = ""
    read_only: bool = False
    key: str = field(default_factory=lambda: secrets.token_urlsafe(18))  # media stream URL secret
    status: str = "new"
    ccid: str = ""
    codec: str = "PCMU"
    created: float = field(default_factory=time.time)
    media_at: float | None = None
    outcome: str = ""
    error: str = ""
    finished: bool = False
    bridge: "Bridge | None" = None

    def view(self) -> dict:
        return {"call_id": self.id, "richtung": "ausgehend" if self.direction == "out" else "eingehend", "nummer": self.number,
                "du": self.owner, "status": self.status, "ergebnis": self.outcome or None, "fehler": self.error or None}


class Phone:
    def __init__(self, settings, ida: Ida | None = None, telnyx: Telnyx | None = None, connect=None):
        self.s = settings
        self.ida = ida or Ida(settings)
        self.telnyx = telnyx or Telnyx(settings)
        self.connect = connect  # Gemini WebSocket factory (tests)
        self.calls: dict[str, Call] = {}

    # ---------- helpers ----------
    def owner_number(self) -> str:
        return e164(self.s.stella_owner_number)

    def busy(self) -> list[Call]:
        return [c for c in self.calls.values() if c.status in ACTIVE and not c.finished]

    def _public(self, path: str, ws: bool = False) -> str:
        base = self.s.voice_public_url.rstrip("/")
        if ws:
            base = "wss://" + base[8:] if base.startswith("https://") else "ws://" + base[7:] if base.startswith("http://") else base
        return base + path

    def _find(self, ccid: str, state: str | None) -> Call | None:
        cid = read_client_state(state).get("call")
        if cid and cid in self.calls:
            return self.calls[cid]
        return next((c for c in self.calls.values() if ccid and c.ccid == ccid), None)

    # ---------- outbound ----------
    async def place(self, to: str, brief: str, read_only: bool = False) -> Call:
        if not self.s.gemini_api_key:
            raise CallError("GEMINI_API_KEY fehlt")
        if not self.s.voice_public_url:
            raise CallError("VOICE_PUBLIC_URL fehlt (Telnyx muss den Dienst erreichen)")
        number = e164(to) if to else self.owner_number()
        if not number:
            raise CallError("Keine gültige Nummer (und STELLA_OWNER_NUMBER ist nicht gesetzt)" if not to else f"Ungültige Nummer: {to}")
        if self.busy():
            raise CallError(f"Stella telefoniert gerade ({self.busy()[0].id})")
        call = Call(id="c" + secrets.token_hex(6), direction="out", number=number, owner=same_number(number, self.owner_number()),
                    brief=brief.strip(), read_only=read_only, status="dialing")
        self.calls[call.id] = call
        try:
            out = await self.telnyx.dial(number, self._public("/telnyx/webhook"), self._public(f"/telnyx/media/{call.id}?k={call.key}", ws=True),
                                         client_state({"call": call.id}))
        except TelnyxError as e:
            call.status, call.error, call.finished = "failed", str(e), True
            await self.ida.event(call.id, "out", number, "failed", brief, str(e), call.owner)
            raise CallError(str(e)) from e
        call.ccid, call.codec = out.get("call_control_id", ""), out.get("codec", call.codec)
        await self.ida.event(call.id, "out", number, "dialing", brief, owner=call.owner)
        return call

    # ---------- webhooks ----------
    async def webhook(self, body: dict) -> None:
        data = body.get("data") or body
        p = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        et = str(data.get("event_type") or body.get("event_type") or "")
        ccid = str(p.get("call_control_id") or "")
        call = self._find(ccid, p.get("client_state"))
        direction = str(p.get("direction") or "").lower()
        if call is None and et == "call.initiated" and direction not in ("outgoing", "outbound"):
            await self._inbound(p)
            return
        if call is None:
            log.info("Telnyx %s für unbekannten Anruf %s", et, ccid)
            return
        if ccid and not call.ccid:
            call.ccid = ccid
        if et in ("call.answered", "streaming.started"):
            codec = codec_from_telnyx_payload(p)
            if codec:
                call.codec = normalize_g711_codec(codec)
            if call.status in ("dialing", "ringing"):
                call.status = "live"
        elif et == "streaming.failed":
            call.error = str(p.get("failure_reason") or p.get("reason") or "Media-Stream fehlgeschlagen")
            await self.telnyx.hangup(call.ccid)
        elif et == "call.hangup":
            cause = str(p.get("hangup_cause") or "")
            if call.bridge is not None:
                await call.bridge.end("Aufgelegt.")  # the media handler finishes the call
            elif not call.finished:
                status = "missed" if call.direction == "out" and call.media_at is None else "done"
                await self.finish(call, status, "" if status == "done" else f"nicht angenommen ({cause or 'unbekannt'})")

    async def _inbound(self, p: dict) -> None:
        ccid = str(p.get("call_control_id") or "")
        caller = e164(p.get("from") or p.get("caller_id_number") or "")
        owner = bool(caller) and same_number(caller, self.owner_number())
        if not ccid:
            return
        if not owner or not self.s.gemini_api_key or self.busy():
            # only the owner's number is answered – everybody else is not picked up at all
            reason = "fremde Nummer" if not owner else "besetzt" if self.busy() else "GEMINI_API_KEY fehlt"
            log.info("eingehend von %s abgelehnt: %s", caller or "unbekannt", reason)
            await self.telnyx.hangup(ccid)
            await self.ida.event("r" + secrets.token_hex(6), "in", caller, "rejected", error=reason)
            return
        call = Call(id="c" + secrets.token_hex(6), direction="in", number=caller, owner=True, status="ringing", ccid=ccid)
        self.calls[call.id] = call
        try:
            call.codec = await self.telnyx.answer(ccid, self._public("/telnyx/webhook"), self._public(f"/telnyx/media/{call.id}?k={call.key}", ws=True),
                                                  client_state({"call": call.id}), caller)
            call.status = "live"
        except TelnyxError as e:
            call.error = str(e)
            await self.finish(call, "failed", str(e))

    # ---------- media ----------
    def media_allowed(self, call_id: str, key: str) -> Call | None:
        c = self.calls.get(call_id)
        if c and not c.finished and c.bridge is None and hmac.compare_digest(c.key, key or ""):
            return c
        return None

    async def media(self, ws, call: Call) -> None:
        """Runs for the whole call: Telnyx WebSocket <-> Gemini Live."""
        call.media_at = time.time()
        call.status = "live"
        b = Bridge(self, call, ws)
        call.bridge = b
        status, error = "done", ""
        try:
            await b.run()
        except Exception as e:  # noqa: BLE001
            log.exception("Anruf %s", call.id)
            status, error = "failed", str(e)[:300]
        finally:
            await b.close()
            if not b.hung_up:
                await self.telnyx.hangup(call.ccid)
            await self.finish(call, status, error, transcript=b.session.transcript if b.session else [],
                              outcome=b.session.outcome if b.session else "")

    async def finish(self, call: Call, status: str, error: str = "", transcript: list[dict] | None = None, outcome: str = "") -> None:
        if call.finished:
            return
        call.finished, call.status = True, status
        call.error = call.error or error
        call.outcome = outcome or call.outcome
        await self.ida.finish(call.id, call.direction, call.number, transcript or [], call.outcome, status, call.error)

    async def reap(self) -> None:
        """Calls that hang: never answered, or longer than VOICE_MAX_CALL_MIN."""
        now = time.time()
        for c in list(self.calls.values()):
            if c.finished:
                if now - c.created > 3600:
                    self.calls.pop(c.id, None)
                continue
            if c.media_at is None and now - c.created > DIAL_TIMEOUT_S:
                await self.telnyx.hangup(c.ccid)
                await self.finish(c, "missed", "nicht angenommen (Zeitüberschreitung)")
            elif c.media_at and now - c.media_at > self.s.voice_max_call_min * 60 and c.bridge:
                await c.bridge.end("Maximale Gesprächsdauer erreicht.")


class Bridge:
    """Sink for LiveSession + the Telnyx side of one call."""

    def __init__(self, phone: Phone, call: Call, ws):
        self.phone, self.call, self.ws, self.s = phone, call, ws, phone.s
        self.session: LiveSession | None = None
        self.started = asyncio.Event()  # Telnyx sent "start"
        self.up = Pcmu8kToPcm16k(codec=call.codec)
        self.down = PcmToPcmu8k(default_rate=GEMINI_OUT_RATE, codec=call.codec)
        self.vad = EnergyVad(config_from_settings(self.s)) if self.s.voice_phone_vad else None
        self.play_end = 0.0
        self.sent_ms = 0
        self.answered: float | None = None
        self.barge_open = False
        self.turn_open = False
        self.hung_up = False
        self._closing = False
        self._hangup_task: asyncio.Task | None = None

    async def run(self) -> None:
        c = self.call
        info = await self.phone.ida.start(c.id, c.direction, c.number, c.owner, c.brief)
        owner = c.owner  # decided by the number alone – never by anything said in the call
        tools = (info.get("tools") or []) if owner else []
        kind = c.direction
        self.session = LiveSession(
            self.s, instructions=prompt.instructions(info.get("context") or "", owner=owner, kind=kind, brief=c.brief, read_only=c.read_only),
            tools=tools, run_tool=lambda n, a: self.phone.ida.tool(c.id, n, a), sink=self,
            kickoff=prompt.kickoff(owner=owner, kind=kind, brief=c.brief), client_vad=self.vad is not None, connect=self.phone.connect)
        await self.session.open()

        async def gemini():
            await self.session.run()
            await self.end()  # Gemini is gone: do not leave the caller on a silent line
        await asyncio.gather(self._pump(), gemini(), self._kickoff())

    async def _kickoff(self) -> None:
        await self.started.wait()
        if self.session and not self.session.closed:
            await self.session.kickoff()

    async def _pump(self) -> None:
        try:
            async for raw in self.ws.iter_text():
                if self._closing:
                    break
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                ev = msg.get("event")
                if ev in ("start", "media") and not self.started.is_set():
                    self.started.set()
                    self.answered = time.monotonic()
                if ev == "start":
                    codec = codec_from_telnyx_payload(msg.get("start") or msg)
                    if codec and normalize_g711_codec(codec) != self.up.codec:
                        self.up = Pcmu8kToPcm16k(codec=codec)
                        self.down = PcmToPcmu8k(default_rate=GEMINI_OUT_RATE, codec=codec)
                elif ev == "media":
                    media = msg.get("media") or {}
                    if (media.get("track") or "").lower() in ("outbound", "outbound_track"):
                        continue
                    pcm = self.up.convert_b64(media.get("payload") or "")
                    if pcm:
                        await self._inbound(pcm)
                elif ev in ("stop", "closed"):
                    break
        finally:
            if self.session:
                await self.session.close()

    # ----- caller -> Gemini -----
    def _greeting_locked(self) -> bool:
        """Line noise right after answering must not cut Stella's first sentence."""
        if self.barge_open or self.answered is None or self.session is None:
            return False
        if self.session.first_turn_done.is_set() and time.monotonic() - self.answered >= self.s.voice_greeting_barge_in_s:
            self.barge_open = True
            if self.vad:
                self.vad.reset()
            return False
        return True

    def _echo(self, rms: int) -> bool:
        """While Stella is still audible, a quiet onset is her own echo, not barge-in."""
        need = int(self.s.voice_vad_rms * self.s.voice_echo_rms_factor) if self.s.voice_echo_rms_factor > 0 else 0
        return need > 0 and self.play_end - time.monotonic() > 0 and rms < need

    async def _inbound(self, pcm: bytes) -> None:
        s = self.session
        if s is None or self._greeting_locked():
            return
        if self.vad is None:
            if pcm_rms(pcm) >= self.s.voice_vad_rms:
                s.user_spoke()
            await s.send_audio(pcm)
            return
        events = self.vad.feed(pcm)
        rms = pcm_rms(pcm)
        for ev in events:
            if ev == "start":
                if self._echo(rms):
                    self.vad.speaking = False
                    continue
                self.turn_open = True
                await s.activity_start()
        await s.send_audio(pcm)
        for ev in events:
            if ev == "end" and self.turn_open:
                self.turn_open = False
                await s.activity_end()

    # ----- Gemini -> caller (Sink) -----
    async def _send_frame(self, payload: str) -> None:
        if not self.started.is_set() or self._closing:
            return
        try:
            await self.ws.send_text(json.dumps({"event": "media", "media": {"payload": payload}}))
        except Exception:  # noqa: BLE001
            return
        n = max(1, len(base64.b64decode(payload)) // FRAME_BYTES)
        self.sent_ms += n * FRAME_MS
        self.play_end = max(self.play_end, time.monotonic()) + n * FRAME_MS / 1000

    async def audio(self, pcm: bytes, rate: int) -> None:
        for frame in self.down.convert_frames_b64(base64.b64encode(pcm).decode(), f"audio/pcm;rate={rate}"):
            await self._send_frame(frame)

    async def interrupted(self) -> None:
        if not self.barge_open:
            return
        self.down = PcmToPcmu8k(default_rate=GEMINI_OUT_RATE, codec=self.down.codec)
        self.play_end = 0.0
        try:
            await self.ws.send_text(json.dumps({"event": "clear"}))
        except Exception:  # noqa: BLE001
            pass

    async def text(self, who: str, piece: str) -> None:
        return None

    async def tool(self, name: str, running: bool) -> None:
        return None

    def hang_up(self, outcome: str) -> None:
        if self._hangup_task is None:
            self._hangup_task = asyncio.create_task(self._hang_up_after_audio())

    async def _hang_up_after_audio(self) -> None:
        """Telnyx plays 20 ms frames in real time: wait until everything queued has been heard, then hang up."""
        await self.started.wait()
        deadline = time.monotonic() + NO_AUDIO_WAIT_S
        while self.sent_ms <= 0 and time.monotonic() < deadline and not self._closing:
            await asyncio.sleep(0.05)
        for frame in self.down.flush_frames_b64():
            await self._send_frame(frame)
        await asyncio.sleep(min(max(0.0, self.play_end - time.monotonic()) + PLAYOUT_PAD_S, PLAYOUT_CAP_S))
        await self.end()

    async def end(self, outcome: str = "") -> None:
        if self.session and outcome and not self.session.outcome:
            self.session.outcome = outcome
        if not self.hung_up:
            self.hung_up = True
            await self.phone.telnyx.hangup(self.call.ccid)
        await self.close()

    async def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.started.set()  # releases a kickoff still waiting for the stream
        if self.session:
            await self.session.close()
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
