# IDA Docs – Dokumente schreiben (Markdown, DIN A4). Eigener Dienst; die Dateien liegen im Volume /data (.idadocs + .md), optional Backup nach Google Drive.
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f dokumente/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/dokumente
COPY dokumente/requirements.txt .
RUN pip install -r requirements.txt

COPY dokumente/ /app/dokumente/
RUN rm -rf /app/dokumente/tests
COPY frontend/docs/ /app/frontend/docs/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/

RUN useradd --create-home --uid 1000 docs && mkdir -p /data && chown -R docs:docs /data /app
USER docs
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
