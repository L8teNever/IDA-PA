# IDA Korrektur – Tippfehler-Korrektur für IDA Chats mit einem lokalen T5-Modell (nur Prozessor, keine Grafikkarte).
# Das Modell (~1 GB) lädt beim ersten Start nach /data/hf und bleibt im Volume.   docker build -f korrektur/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/data/hf \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app/korrektur
# Torch als CPU-Version (ohne CUDA: ein Bruchteil der Größe)
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.4,<3"
COPY korrektur/requirements.txt korrektur/requirements-model.txt ./
RUN pip install -r requirements.txt -r requirements-model.txt

COPY korrektur/ /app/korrektur/
RUN rm -rf /app/korrektur/tests

RUN useradd --create-home --uid 1000 ida && mkdir -p /data/hf && chown -R ida:ida /app /data
USER ida
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status==200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
