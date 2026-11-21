# IDA Health – Geräte mit ida-connect: Messwerte im Verlauf, Shell, Dateien, Docker, Updates. Siehe docs/HEALTH.md
# Build-Kontext ist das Repo-Root:  docker build -f health/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend \
    DATA_DIR=/data

# WARP (cloudflare-warp) für Cloudflare Mesh: damit tritt Health selbst dem Mesh bei (Einstellungen → Cloudflare Mesh).
# Ohne WARP bauen: --build-arg WARP=0. Zum Laufen braucht es im Compose cap_add NET_ADMIN + /dev/net/tun (sonst bleibt WARP aus).
ARG WARP=1
RUN apt-get update && apt-get install -y --no-install-recommends tini ca-certificates \
 && if [ "$WARP" = 1 ]; then \
      apt-get install -y --no-install-recommends curl gpg iproute2 dbus \
      && curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
      && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(. /etc/os-release && echo $VERSION_CODENAME) main" \
           > /etc/apt/sources.list.d/cloudflare-client.list \
      && apt-get update && apt-get install -y --no-install-recommends cloudflare-warp \
      && command -v warp-svc && command -v warp-cli; \
    fi \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app/health
COPY health/requirements.txt .
RUN pip install -r requirements.txt

COPY health/app /app/health/app
COPY health/entrypoint.sh /usr/local/bin/health-entrypoint
COPY frontend/health/ /app/frontend/health/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/
COPY frontend/launcher/icons/ /app/frontend/launcher/icons/
COPY frontend/dashboard/js/api.js frontend/dashboard/js/chart.js frontend/dashboard/js/ui.js /app/frontend/dashboard/js/
COPY frontend/dashboard/css/dashboard.css /app/frontend/dashboard/css/
COPY frontend/vendor/xterm/ /app/frontend/vendor/xterm/

VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--", "health-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--ws-max-size", "16777216"]
