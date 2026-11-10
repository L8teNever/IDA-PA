# IDA Launcher – Startseite mit allen Apps (wie der Cloudflare-Access-Launcher), automatisch aus Cloudflare Access und den Tunnel-Adressen.
# Build-Kontext ist das Repo-Root:  docker build -f launcher/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend \
    DATA_DIR=/data

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/launcher
COPY launcher/requirements.txt .
RUN pip install -r requirements.txt

COPY launcher/app /app/launcher/app
COPY frontend/launcher/ /app/frontend/launcher/
COPY frontend/css/tokens.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/
COPY frontend/icons/ /app/frontend/icons/

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
