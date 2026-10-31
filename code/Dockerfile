# IDA Code – eigene App für den Code-Bereich (eigener Port, eigene PWA). Die Arbeit (KI, Projekte, Git, GitHub) macht IDA;
# dieser Dienst liefert die Oberfläche und reicht die nötigen API-Aufrufe an IDA weiter.
# Build-Kontext ist das Repo-Root (die Oberfläche liegt in frontend/):  docker build -f code/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIR=/app/frontend

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/code
COPY code/requirements.txt .
RUN pip install -r requirements.txt

COPY code/app /app/code/app
# Oberfläche + die gemeinsamen Design-Dateien und Schriften von IDA
COPY frontend/code/ /app/frontend/code/
COPY frontend/css/tokens.css frontend/css/components.css /app/frontend/css/
COPY frontend/fonts/ /app/frontend/fonts/

RUN useradd --create-home --uid 1000 code && chown -R code:code /app
USER code
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
