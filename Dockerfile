FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/worker:/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        libgomp1 \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY worker/requirements.txt /app/worker/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/worker/requirements.txt

COPY . /app
RUN chmod +x /app/deploy/render/*.sh

CMD ["./deploy/render/start-api.sh"]
