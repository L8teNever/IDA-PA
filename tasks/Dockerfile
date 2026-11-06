# IDA Tasks – eigener Dienst mit eigener SQLite-Datenbank (Volume /data), eigener Port, eigene PWA.
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f tasks/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    FRONTEND_DIR=/app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/tasks
COPY tasks/requirements.txt .
RUN pip install -r requirements.txt

COPY tasks/ /app/tasks/
RUN rm -rf /app/tasks/tests
# Oberfläche + die gemeinsamen Design-Dateien und Schriften von IDA (nichts sonst aus IDA)
COPY frontend/tasks/ /app/frontend/tasks/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/

RUN useradd --create-home --uid 1000 tasks && mkdir -p /data && chown -R tasks:tasks /data /app
USER tasks
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

# Migrationen laufen beim Start (app/main.py). Nur EIN Prozess: SQLite hat einen Schreiber, der Live-Stream lebt im Speicher.
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
