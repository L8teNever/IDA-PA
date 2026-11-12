# IDA Chats – Beeper-Chats im IDA-Stil (ffmpeg wandelt Sprachaufnahmen in Ogg/Opus). Chats und Nachrichten liegen in Beeper Desktop; unter /data merkt sich der Dienst nur gesehene Nachrichten (Zugang per MCP-Adresse in BEEPER_MCP_URL).
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f chats/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends tini ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app/chats
COPY chats/requirements.txt .
RUN pip install -r requirements.txt

COPY chats/ /app/chats/
RUN rm -rf /app/chats/tests
# Oberfläche + die gemeinsamen Design-Dateien, Schriften und das Markdown/Link-Modul von IDA (nichts sonst aus IDA)
COPY frontend/chats/ /app/frontend/chats/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/
COPY frontend/js/ui/dom.js /app/frontend/js/ui/dom.js

RUN useradd --create-home --uid 1000 ida && mkdir -p /data && chown -R ida:ida /app /data
USER ida
# chats.db: gemerkte Nachrichten (damit Gelöschtes lesbar bleibt)
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

# Nur EIN Prozess: der Live-Stream lebt im Speicher.
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
