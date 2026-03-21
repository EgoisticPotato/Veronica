"""
Veronica — AI Voice Assistant
FastAPI application entry point — production-hardened
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from api.routes import router as api_router
from core.config import settings
from core.logging_config import setup_logging, RequestLoggingMiddleware

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Veronica starting — debug=%s stt=%s tts=%s",
                settings.DEBUG, settings.STT_PROVIDER, settings.TTS_PROVIDER)
    # Validate critical secrets are set in production
    if not settings.DEBUG:
        missing = []
        if not settings.SPOTIFY_CLIENT_ID:     missing.append("SPOTIFY_CLIENT_ID")
        if not settings.SPOTIFY_CLIENT_SECRET:  missing.append("SPOTIFY_CLIENT_SECRET")
        if settings.STT_PROVIDER == "openai" and not settings.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if settings.TTS_PROVIDER == "elevenlabs" and not settings.ELEVENLABS_API_KEY:
            missing.append("ELEVENLABS_API_KEY")
        if missing:
            logger.warning("PRODUCTION WARNING: missing secrets: %s", missing)
    yield
    logger.info("Veronica shutting down")


app = FastAPI(
    title="Veronica AI Assistant",
    version="1.0.0",
    lifespan=lifespan,
    # Never expose API docs, schemas, or OpenAPI JSON in production
    docs_url    ="/api/docs"        if settings.DEBUG else None,
    redoc_url   =None,
    openapi_url ="/api/openapi.json" if settings.DEBUG else None,
)


# ── Security headers middleware ────────────────────────────────────────────────

from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]  = "nosniff"
        response.headers["X-Frame-Options"]          = "DENY"
        response.headers["X-XSS-Protection"]         = "1; mode=block"
        response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]       = "camera=(), microphone=(), geolocation=()"
        # HSTS — only send over HTTPS (Cloud Run enforces HTTPS automatically)
        if not settings.DEBUG:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Remove server fingerprinting headers
        response.headers.pop("server", None)
        return response


# ── Middleware stack (order matters — outermost runs first) ───────────────────

# 1. Request logging — outermost so every request is logged including rejected ones
app.add_middleware(RequestLoggingMiddleware)

# 2. Security headers — applied to all responses
app.add_middleware(SecurityHeadersMiddleware)

# 3. GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 4. Trusted host validation in production
if not settings.DEBUG:
    allowed = ["localhost", "127.0.0.1", "*.run.app", "*.vercel.app"]
    if settings.ALLOWED_HOST:
        allowed.append(settings.ALLOWED_HOST)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)

# 5. CORS — strict origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins     =settings.CORS_ORIGINS,
    allow_credentials =True,
    allow_methods     =["GET", "POST", "DELETE"],
    allow_headers     =["Content-Type", "Authorization"],
    expose_headers    =["Content-Disposition"],
)


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(api_router, prefix="/api/v1")


# ── Production static file serving ────────────────────────────────────────────

_build_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")

if os.path.exists(_build_dir):
    app.mount(
        "/static",
        StaticFiles(directory=os.path.join(_build_dir, "static")),
        name="static",
    )

    @app.get("/{full_path:path}")
    async def serve_react(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(os.path.join(_build_dir, "index.html"))


# ── Dev entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 5000))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=settings.DEBUG,
        log_level="info",
    )
