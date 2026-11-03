"""Telnyx Call Control (wählen, annehmen, auflegen) + Prüfung der Webhook-Signatur. Logik aus dem alten Stella übernommen."""
import base64
import json
import logging
import time

import httpx

from .audio import CODEC_PCMU, preferred_telnyx_codec

log = logging.getLogger("ida.voice.telnyx")
CALL_ALREADY_ENDED = "90015"


class TelnyxError(Exception):
    pass


def e164(raw) -> str:
    """+491701234567 from „+49 170 …“, „00491…“, „sip:+491…@host“ or {"phone_number": …}; "" when there are no digits."""
    if isinstance(raw, dict):
        raw = raw.get("phone_number") or raw.get("number") or ""
    text = str(raw or "").strip()
    for prefix in ("sip:", "sips:", "tel:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.split("@", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return ""
    if not text.lstrip().startswith("+") and digits.startswith("00"):
        digits = digits[2:]
    if not text.lstrip().startswith("+") and digits.startswith("0") and not digits.startswith("00"):
        digits = "49" + digits[1:]  # German national format
    return "+" + digits if 8 <= len(digits) <= 15 else ""


def same_number(a, b) -> bool:
    x, y = e164(a), e164(b)
    return bool(x and y and x == y)


def verify_signature(payload: bytes, timestamp: str, signature_b64: str, public_key_b64: str, now: float | None = None) -> None:
    """Telnyx Ed25519: signature over „timestamp|body“, at most 5 minutes old. Raises TelnyxError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    if not timestamp or not signature_b64:
        raise TelnyxError("Signatur fehlt")
    try:
        ts = int(timestamp)
    except ValueError as e:
        raise TelnyxError("Zeitstempel ungültig") from e
    if abs((now or time.time()) - ts) > 300:
        raise TelnyxError("Webhook zu alt")
    try:
        key = base64.b64decode(public_key_b64)
        sig = base64.b64decode(signature_b64)
        pub = Ed25519PublicKey.from_public_bytes(key) if len(key) == 32 else load_der_public_key(key)
    except Exception as e:  # noqa: BLE001
        raise TelnyxError("Schlüssel/Signatur nicht lesbar") from e
    if not isinstance(pub, Ed25519PublicKey):
        raise TelnyxError("TELNYX_PUBLIC_KEY ist kein Ed25519-Schlüssel")
    try:
        pub.verify(sig, f"{timestamp}|".encode() + payload)
    except InvalidSignature as e:
        raise TelnyxError("Signatur passt nicht") from e


def client_state(obj: dict) -> str:
    return base64.b64encode(json.dumps(obj).encode()).decode()


def read_client_state(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except Exception:  # noqa: BLE001
        return {}


class Telnyx:
    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.s.telnyx_api_key and self.s.telnyx_connection_id and self.s.telnyx_from_number)

    async def _post(self, path: str, payload: dict, action: str) -> dict:
        if not self.configured:
            raise TelnyxError("Telnyx ist nicht eingerichtet (TELNYX_API_KEY, TELNYX_CONNECTION_ID, TELNYX_FROM_NUMBER)")
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
            r = await c.post(self.s.telnyx_api_base.rstrip("/") + path, json=payload,
                             headers={"Authorization": f"Bearer {self.s.telnyx_api_key}", "Accept": "application/json"})
        if r.status_code >= 400:
            if CALL_ALREADY_ENDED in r.text:
                raise TelnyxError("Anruf ist schon beendet")
            raise TelnyxError(f"Telnyx {action}: HTTP {r.status_code} {r.text[:300]}")
        try:
            data = r.json()
        except ValueError:
            return {}
        return data.get("data") or data

    def _codec(self, *numbers) -> str:
        c = preferred_telnyx_codec(self.s.telnyx_from_number, *numbers)
        return c if c in ("PCMU", "PCMA") else CODEC_PCMU

    @staticmethod
    def _stream(url: str, codec: str) -> dict:
        # inbound_track = only the other side's audio (both_tracks would echo Stella back into the model)
        return {"stream_url": url, "stream_track": "inbound_track", "stream_bidirectional_mode": "rtp",
                "stream_bidirectional_codec": codec, "stream_bidirectional_sampling_rate": 8000}

    async def dial(self, to: str, webhook_url: str, stream_url: str, state: str) -> dict:
        codec = self._codec(to)
        out = await self._post("/calls", {"to": to, "from": self.s.telnyx_from_number, "connection_id": self.s.telnyx_connection_id,
                                          "webhook_url": webhook_url, "webhook_url_method": "POST", "client_state": state,
                                          **self._stream(stream_url, codec)}, "dial")
        if not out.get("call_control_id"):
            raise TelnyxError("Telnyx hat keine call_control_id geliefert")
        return {**out, "codec": codec}

    async def answer(self, ccid: str, webhook_url: str, stream_url: str, state: str, caller: str) -> str:
        # the media stream is started with the answer – a second streaming_start races it (Telnyx 422 90046)
        codec = self._codec(caller)
        await self._post(f"/calls/{ccid}/actions/answer", {"client_state": state, "webhook_url": webhook_url, "webhook_url_method": "POST",
                                                          **self._stream(stream_url, codec)}, "answer")
        return codec

    async def hangup(self, ccid: str) -> None:
        try:
            await self._post(f"/calls/{ccid}/actions/hangup", {}, "hangup")
        except TelnyxError as e:
            log.info("hangup %s: %s", ccid, e)
