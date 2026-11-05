# IDA Dashboard – eigene App für den Server (Docker-Stacks, Leistung, Speicher), eigener Port, eigene PWA.
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f dashboard/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend \
    DATA_DIR=/data

# Docker-CLI + Compose-Plugin (für Stacks: up/pull/stop …), aus dem offiziellen Docker-Repo; git für die Websites des IDA Servers
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tini \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin git \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /app/dashboard
COPY dashboard/requirements.txt .
RUN pip install -r requirements.txt

COPY dashboard/app /app/dashboard/app
# Oberfläche + die gemeinsamen Design-Dateien und Schriften von IDA
COPY frontend/dashboard/ /app/frontend/dashboard/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/

# läuft als root: braucht den Docker-Socket und schreibt Compose-/.env-Dateien in den Stack-Ordnern (wie Dockge)
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
