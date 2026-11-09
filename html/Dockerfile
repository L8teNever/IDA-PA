# IDA HTML (früher CLEO) – Code-Editor mit Live-Vorschau im Browser. Rein statisch: Projekte liegen im Browser, der Server speichert nichts.
# Build-Kontext ist das Repo-Root:  docker build -f html/Dockerfile .
FROM nginx:1.29-alpine
COPY html/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/html/ /usr/share/nginx/html/
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8000/health || exit 1
