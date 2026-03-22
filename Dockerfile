# ─────────────────────────────────────────────────────────────────
# Veronica Backend — Production Dockerfile
# Python 3.13 + system deps (ffmpeg, tesseract, poppler)
# ─────────────────────────────────────────────────────────────────
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# ── System packages ───────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    poppler-utils \
    tesseract-ocr \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ──────────────────────────────────────────
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ─────────────────────────────────────────────
COPY backend/ .

# ── Persistent data directory ────────────────────────────────────
RUN mkdir -p /app/data

# ── Health check ─────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/api/v1/health || exit 1

# ── Run ──────────────────────────────────────────────────────────
EXPOSE 8000
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
