# IDA Whisper – wandelt Sprachnachrichten für IDA Chats lokal in Text um (Whisper large-v3-turbo über faster-whisper, nur Prozessor, keine Grafikkarte).
# Das Modell (~1,6 GB) lädt beim ersten Start nach /data/hf und bleibt im Volume.   docker build -f whisper/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/whisper
# faster-whisper bringt die Audio-Dekodierung (PyAV mit ffmpeg-Bibliotheken, auch Ogg/Opus) und die Stille-Erkennung mit
COPY whisper/requirements.txt ./
RUN pip install -r requirements.txt

COPY whisper/ /app/whisper/
RUN rm -rf /app/whisper/tests

RUN useradd --create-home --uid 1000 ida && mkdir -p /data/hf && chown -R ida:ida /app /data
USER ida
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
