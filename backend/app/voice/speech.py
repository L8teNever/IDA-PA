"""Bausteine der eigenen Kette: Sprache -> Text (Cloudflare Workers AI) und Text -> Sprache (Piper im Stack)."""
from __future__ import annotations

import base64
import io
import re
import wave

try:
    import audioop
except ImportError:  # Python 3.13+: audioop-lts
    import audioop_lts as audioop  # type: ignore

import httpx

OUT_RATE = 24000  # was der Browser und das Telefon von Stella erwarten (wie Gemini)
CF_API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
# Whisper erfindet bei Rauschen gern Abspann-Sätze aus dem Fernsehen
HALLUCINATION = re.compile(r"(untertitel|amara\.org|vielen dank f[üu]r(?:s| das) zu(?:schauen|sehen|hören)|copyright|swr \d{4}|zdf|"
                           r"thanks for watching|bis zum nächsten mal\.?$)", re.IGNORECASE)


class SpeechError(RuntimeError):
    pass


def wav_bytes(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[bytes, int]:
    with wave.open(io.BytesIO(data)) as w:
        pcm, rate, ch = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
    if ch == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    return pcm, rate


def resample(pcm: bytes, rate: int, to: int = OUT_RATE) -> bytes:
    if rate == to or not pcm:
        return pcm
    return audioop.ratecv(pcm, 2, 1, rate, to, None)[0]


def _cf_text(body: dict) -> str:
    r = body.get("result") if isinstance(body.get("result"), dict) else body
    if isinstance(r.get("text"), str):
        return r["text"]
    try:  # Deepgram
        return r["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError, TypeError):
        return ""


def clean_heard(text: str) -> str:
    text = " ".join((text or "").split())
    if not re.search(r"\w", text) or HALLUCINATION.search(text):
        return ""
    return text


async def transcribe(settings, model: str, pcm16k: bytes, language: str = "de", transport: httpx.AsyncBaseTransport | None = None) -> str:
    """PCM 16 kHz mono -> Text über Cloudflare Workers AI. Leer, wenn nichts (Sinnvolles) gesagt wurde."""
    account, token = settings.cf_ai_account, settings.cf_ai_token
    if not (account and token):
        raise SpeechError("Cloudflare-Zugang fehlt (CLOUDFLARE_AI_ACCOUNT_ID und CLOUDFLARE_AI_TOKEN in .env)")
    url = CF_API.format(account=account, model=model)
    wav = wav_bytes(pcm16k, 16000)
    headers = {"authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        if model == "@cf/openai/whisper-large-v3-turbo":
            r = await c.post(url, headers=headers, json={"audio": base64.b64encode(wav).decode(), "language": language, "vad_filter": True})
        elif model == "@cf/deepgram/nova-3":
            r = await c.post(url, headers={**headers, "content-type": "audio/wav"}, content=wav,
                             params={"language": language, "smart_format": "true", "punctuate": "true"})
        else:  # @cf/openai/whisper: rohe Datei als Body
            r = await c.post(url, headers={**headers, "content-type": "application/octet-stream"}, content=wav)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or body.get("success") is False:
        errs = body.get("errors") if isinstance(body, dict) else None
        why = "; ".join(str(e.get("message") or e) for e in errs) if isinstance(errs, list) and errs else r.text[:200]
        raise SpeechError(f"Cloudflare {r.status_code}: {why}")
    return clean_heard(_cf_text(body))


SPEAK_CLEAN = [(re.compile(r"\[(.*?)\]\(.*?\)"), r"\1"), (re.compile(r"https?://\S+"), ""), (re.compile(r"[*_`#>|~]+"), ""),
               (re.compile("[\U0001F000-\U0001FAFF☀-➿️]"), ""), (re.compile(r"\s+"), " ")]


def speakable(text: str) -> str:
    for pat, rep in SPEAK_CLEAN:
        text = pat.sub(rep, text)
    return text.strip()


async def synthesize(settings, text: str, voice: str, speed: float = 1.0, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    """Text -> PCM 24 kHz mono über den Piper-Dienst."""
    if not settings.piper_url:
        raise SpeechError("Piper ist nicht eingerichtet (PIPER_URL)")
    async with httpx.AsyncClient(timeout=120, transport=transport) as c:
        r = await c.post(settings.piper_url.rstrip("/") + "/api/v1/tts", json={"text": text, "voice": voice, "speed": speed})
    if r.status_code != 200:
        raise SpeechError(f"Piper {r.status_code}: {r.text[:200]}")
    pcm, rate = read_wav(r.content)
    return resample(pcm, rate)


# Satzende: Satzzeichen + Leerraum. Nicht nach Zahlen („am 3. Mai“) und gängigen Abkürzungen.
_END = re.compile(r"[.!?…:;]+[\"“”»)]*\s+")
_NO_SPLIT = re.compile(r"(\b\d{1,4}|\b[^\W\d_]|\b(?:z\.\s?B|ca|bzw|usw|Nr|Dr|Hr|Fr|St|evtl|ggf|inkl|vgl|u\.\s?a|d\.\s?h|etc))[.]+[\"“”»)]*\s+$", re.IGNORECASE)


def split_sentences(buf: str, min_len: int = 2) -> tuple[list[str], str]:
    """Fertige Sätze vorne aus dem Puffer -> (Sätze, Rest). Der Rest wartet auf mehr Text."""
    out: list[str] = []
    start = 0
    for m in _END.finditer(buf):
        piece = buf[start:m.end()]
        if _NO_SPLIT.search(piece) or len(piece.strip()) < min_len:
            continue
        out.append(piece.strip())
        start = m.end()
    return out, buf[start:]
