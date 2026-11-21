"""Bausteine der eigenen Kette: Sprache -> Text (Cloudflare Workers AI oder Google) und Text -> Sprache (Piper im Stack)."""
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
GOOGLE_STT = "google"
GOOGLE_API = "https://speech.googleapis.com/v1/speech:recognize"
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
    """PCM 16 kHz mono -> Text über Cloudflare Workers AI oder Google. Leer, wenn nichts (Sinnvolles) gesagt wurde."""
    if model == GOOGLE_STT:
        return await _transcribe_google(settings, pcm16k, language, transport)
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


async def _transcribe_google(settings, pcm16k: bytes, language: str, transport) -> str:
    key = settings.google_stt_key
    if not key:
        raise SpeechError("Google-Zugang fehlt (GOOGLE_STT_API_KEY in .env)")
    lang = language if "-" in language else f"{language}-{language.upper()}"
    body = {"config": {"encoding": "LINEAR16", "sampleRateHertz": 16000, "languageCode": lang,
                       "model": settings.google_stt_model or "latest_short", "enableAutomaticPunctuation": True},
            "audio": {"content": base64.b64encode(pcm16k).decode()}}
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        r = await c.post(GOOGLE_API, headers={"x-goog-api-key": key}, json=body)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        err = data.get("error") if isinstance(data, dict) else None
        why = err.get("message") if isinstance(err, dict) else r.text[:200]
        raise SpeechError(f"Google {r.status_code}: {why}")
    parts = [(res.get("alternatives") or [{}])[0].get("transcript", "") for res in data.get("results") or []]
    return clean_heard(" ".join(p.strip() for p in parts if p))


SPEAK_CLEAN = [(re.compile(r"\[(.*?)\]\(.*?\)"), r"\1"), (re.compile(r"https?://\S+"), ""), (re.compile(r"[*_`#>|~]+"), ""),
               (re.compile("[\U0001F000-\U0001FAFF☀-➿️]"), ""), (re.compile(r"\s+"), " ")]


def speakable(text: str) -> str:
    for pat, rep in SPEAK_CLEAN:
        text = pat.sub(rep, text)
    return text.strip()


def hq_resample(pcm: bytes, rate: int, to: int = OUT_RATE) -> bytes:
    """Saubere Umrechnung (FFT, ohne Aliasing) für kurze, gespeicherte Ansagen; Stille vorn/hinten weg, weiche Ränder."""
    import numpy as np
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if x.size == 0:
        return pcm
    loud = np.nonzero(np.abs(x) > 300)[0]  # Piper setzt Stille davor und danach: kostet bei Floskeln Zeit
    if loud.size:
        pad = int(rate * 0.03)
        x = x[max(0, loud[0] - pad): loud[-1] + pad]
    if rate != to:
        n = int(round(x.size * to / rate))
        spec = np.fft.rfft(x)
        spec_to = np.zeros(n // 2 + 1, dtype=complex)
        k = min(spec.size, spec_to.size)
        spec_to[:k] = spec[:k]
        out = np.fft.irfft(spec_to, n) * (n / x.size)
        x = out
    fade = min(int(to * 0.008), x.size // 2)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade)
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    return np.clip(np.round(x), -32768, 32767).astype("<i2").tobytes()


async def synthesize(settings, text: str, voice: str, speed: float = 1.0, transport: httpx.AsyncBaseTransport | None = None,
                     hq: bool = False) -> bytes:
    """Text -> PCM 24 kHz mono über den Piper-Dienst. hq: saubere Umrechnung und ohne Stille (für gespeicherte Ansagen)."""
    if not settings.piper_url:
        raise SpeechError("Piper ist nicht eingerichtet (PIPER_URL)")
    async with httpx.AsyncClient(timeout=120, transport=transport) as c:
        r = await c.post(settings.piper_url.rstrip("/") + "/api/v1/tts", json={"text": text, "voice": voice, "speed": speed})
    if r.status_code != 200:
        raise SpeechError(f"Piper {r.status_code}: {r.text[:200]}")
    pcm, rate = read_wav(r.content)
    return hq_resample(pcm, rate) if hq else resample(pcm, rate)


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
