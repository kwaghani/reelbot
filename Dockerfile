FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    EMBEDDING_MODEL=BAAI/bge-small-en-v1.5

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libgomp1 \
        nodejs \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt /app/worker/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/worker/requirements.txt
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5', device='cpu')"

COPY api /app/api
COPY worker /app/worker
COPY db /app/db
COPY config /app/config
COPY deploy /app/deploy
RUN chmod +x /app/deploy/render/*.sh

CMD ["./deploy/render/start-personal-api.sh"]
