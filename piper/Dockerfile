# IDA Piper – wandelt Stellas Antworten lokal in Sprache um (Piper, nur Prozessor, keine Grafikkarte).
# Stimmen laden beim ersten Gebrauch von Hugging Face nach /data/voices und bleiben im Volume.   docker build -f piper/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/piper
COPY piper/requirements.txt ./
RUN pip install -r requirements.txt

COPY piper/ /app/piper/
RUN rm -rf /app/piper/tests

RUN useradd --create-home --uid 1000 ida && mkdir -p /data/voices && chown -R ida:ida /app /data
USER ida
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
