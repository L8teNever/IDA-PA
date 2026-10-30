# IDA Kalender – eigener Dienst mit eigener SQLite-Datenbank (Volume /data), eigener Port, eigene PWA.
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f kalender/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/kalender
COPY kalender/requirements.txt .
RUN pip install -r requirements.txt

COPY kalender/ /app/kalender/
# Oberfläche + die gemeinsamen Design-Dateien und Schriften von IDA (kein Zugriff auf irgendetwas sonst aus IDA)
COPY frontend/kalender/ /app/frontend/kalender/
RUN rm -rf /app/frontend/kalender/test
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/

RUN useradd --create-home --uid 1000 kalender && mkdir -p /data && chown -R kalender:kalender /data /app
USER kalender
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

# Migrationen laufen beim Start (siehe app/main.py). Nur EIN Prozess: SQLite hat einen Schreiber.
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
