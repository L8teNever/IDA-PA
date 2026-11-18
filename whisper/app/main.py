"""IDA Whisper: wandelt Sprachnachrichten lokal in Text um (docs/WHISPER.md).
Nur im Stack erreichbar (kein Port nach außen); IDA Chats schickt hier die Audiodatei her."""
import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request

from . import model
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("whisper")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.autoload:
        threading.Thread(target=model.load, daemon=True).start()
    yield


def create_app(autoload: bool = True) -> FastAPI:
    app = FastAPI(title="IDA Whisper", version="1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.autoload = autoload

    @app.get("/api/v1/health", include_in_schema=False)
    async def health():
        return {"ok": True, **model.status()}

    @app.post("/api/v1/transkribieren")
    async def transkribieren(request: Request, language: str = Query("", max_length=8, pattern=r"^[a-z]{0,8}$")):
        """Body = die Audiodatei selbst (roh). Antwort: {text, language, duration, took}."""
        st = get_settings()
        limit = st.whisper_max_mb * 1024 * 1024
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"Datei zu groß (höchstens {st.whisper_max_mb} MB)")
        data = await request.body()
        if not data:
            raise HTTPException(400, "Keine Audiodatei")
        if len(data) > limit:
            raise HTTPException(413, f"Datei zu groß (höchstens {st.whisper_max_mb} MB)")
        s = model.status()
        if s["status"] != "bereit":
            raise HTTPException(503, f"Modell {s['status']}" + (f": {s['error']}" if s["error"] else ""))
        try:
            return await asyncio.to_thread(model.transcribe, data, language)
        except Exception as e:  # noqa: BLE001 – kaputte/unlesbare Datei
            log.warning("Umwandeln fehlgeschlagen: %s", e)
            raise HTTPException(422, "Die Audiodatei ließ sich nicht lesen") from e

    return app


app = create_app()
