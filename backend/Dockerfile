# ── Veronica Backend — Production Dockerfile ──────────────────────────────────
# Multi-stage build for lean image (~900MB vs ~3GB single-stage)
# Target: linux/amd64 (Google Cloud Run)
# Python 3.11-slim (3.13 not on slim yet; behaviour identical for our code)

# ── Stage 1: dependency builder ───────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Build tools for packages that compile native extensions (numpy, pydantic, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ git curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install into isolated prefix — only this prefix is copied to runtime image
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Runtime system packages only:
# - libgomp1:      OpenMP (required by sentence-transformers / numpy)
# - poppler-utils: pdfimages binary for lossless PDF image extraction
# - tesseract-ocr: OCR engine for scanned PDF pages
# ffmpeg NOT included (STT_PROVIDER=openai means no local audio conversion)
# libreoffice NOT included (DOCX→PDF uses reportlab in cloud; add if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder stage
COPY --from=builder /install /usr/local

# Copy application source (excludes .env, data/, __pycache__ via .dockerignore)
COPY . .

# Create data directory — NOTE: Cloud Run is stateless.
# doc_registry.json and memory.json written here are lost on container restart.
# For persistence: mount Cloud Storage FUSE or swap to Firestore/Redis.
RUN mkdir -p data

# Run as non-root for security
RUN useradd -m -u 1001 veronica && chown -R veronica:veronica /app
USER veronica

# Cloud Run injects PORT env var; default 8080
ENV PORT=8080
EXPOSE 8080

# Health check — Cloud Run uses this for readiness probes
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/v1/health')"

# Single worker (Cloud Run scales horizontally via multiple container instances)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --log-level info"]
