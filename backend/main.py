"""
Veronica — AI Voice Assistant
FastAPI application entry point
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from api.routes import router as api_router
from core.config import settings
from core.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Veronica starting — debug=%s", settings.DEBUG)
    yield
    logger.info("Veronica shutting down")


app = FastAPI(
    title="Veronica AI Assistant",
    version="1.0.0",
    lifespan=lifespan,
    # Hide internal details from error responses in production
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.DEBUG else None,
)

# ── Security middleware ────────────────────────────────────────────────────────

# Only allow requests from expected hosts in production
if not settings.DEBUG:
    _hosts = ["localhost", "127.0.0.1", "*.vercel.app"]
    if settings.ALLOWED_HOST:
        _hosts.append(settings.ALLOWED_HOST)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=_hosts,
    )

# CORS — restrict to registered origins only
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
    expose_headers=["X-Transcript", "X-Response"],
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
        # Don't intercept API routes (safety net)
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(os.path.join(_build_dir, "index.html"))


# ── Dev entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    _port = int(os.environ.get("PORT", 5000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=_port,
        reload=settings.DEBUG,
        log_level="info",
    )
