"""
Structured logging configuration — production-grade

Two streams:
  1. application  → stdout (Cloud Run picks this up, routes to Cloud Logging)
  2. security     → stdout with [SECURITY] prefix (can be filtered in SIEM)

Log format is JSON-friendly for Cloud Logging structured log ingestion.
"""

import logging
import sys
import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


def setup_logging():
    fmt = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet noisy third-party libs
    for noisy in ("httpx", "httpcore", "multipart", "PIL", "pdfplumber"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Security logger — INFO level so all security events are captured
    sec = logging.getLogger("veronica.security")
    sec.setLevel(logging.INFO)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log every inbound request with:
      - method, path, status code, duration
      - client IP (X-Forwarded-For aware)
      - User-Agent (truncated)
    Enables detection of:
      - Unusual traffic patterns (many 4xx, repeated paths)
      - Slow requests (potential DoS or abuse)
      - Unknown clients
    """

    _logger = logging.getLogger("veronica.access")

    async def dispatch(self, request: Request, call_next) -> Response:
        start  = time.perf_counter()
        method = request.method
        path   = request.url.path

        # Real client IP (Cloud Run passes X-Forwarded-For)
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )

        ua = request.headers.get("User-Agent", "")[:80]

        try:
            response = await call_next(request)
        except Exception as exc:
            self._logger.error(
                "UNHANDLED ip=%s method=%s path=%s error=%s",
                ip, method, path, type(exc).__name__
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        status = response.status_code

        # Warn on 4xx/5xx and slow requests
        log_fn = self._logger.warning if status >= 400 else self._logger.info
        log_fn(
            "ip=%-15s method=%-6s path=%-50s status=%d duration=%.0fms ua=%s",
            ip, method, path, status, duration_ms, ua,
        )

        # Flag very slow requests (>10s) as potentially suspicious
        if duration_ms > 10_000:
            logging.getLogger("veronica.security").warning(
                "[SECURITY] event=SLOW_REQUEST ip=%s path=%s duration=%.0fms",
                ip, path, duration_ms,
            )

        return response
