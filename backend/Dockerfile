# Veronica Backend — Dockerfile
# Works with: Docker Desktop, Fly.io, Railway, Render, DigitalOcean, AWS ECS
# Frontend: https://veronica-drab.vercel.app

# ── Stage 1: build dependencies ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# System packages:
# - libgomp1:      OpenMP for sentence-transformers/numpy
# - poppler-utils: pdfimages for PDF image extraction
# - tesseract-ocr: OCR for scanned PDF pages
# - ffmpeg:        audio conversion for STT_PROVIDER=local (faster-whisper)
#                  Remove if you use STT_PROVIDER=openai to save ~60MB
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY . .

# data/ holds doc_registry.json and memory.json
# Use: docker run -v veronica_data:/app/data ... to persist across restarts
RUN mkdir -p data

RUN useradd -m -u 1001 veronica && chown -R veronica:veronica /app
USER veronica

ENV PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health')"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info"]
