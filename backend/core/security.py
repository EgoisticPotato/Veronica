"""
Security utilities for Veronica
─────────────────────────────────────────────────────────────────────────────
Covers:
  - Input sanitisation (filenames, URLs, base64, UUIDs, Spotify URIs)
  - SSRF protection for user-supplied URLs
  - Rate limiting (in-memory sliding window, good for single-instance)
  - Secure error responses (no internal detail leakage)
  - Security-event structured logging
"""

import ipaddress
import logging
import re
import time
import unicodedata
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Security event logger (separate stream for SIEM ingestion) ────────────────
sec_logger = logging.getLogger("veronica.security")


def log_security_event(event: str, detail: str, ip: str = "unknown") -> None:
    """Emit a structured security log line."""
    sec_logger.warning("[SECURITY] event=%s ip=%s detail=%s", event, ip, detail)


# ── Rate limiter ──────────────────────────────────────────────────────────────

class SlidingWindowRateLimiter:
    """
    In-memory sliding window rate limiter.
    Thread-safe enough for single-process FastAPI (asyncio event loop).
    For multi-instance Cloud Run: swap _windows for Redis sorted sets.

    Usage:
        limiter = SlidingWindowRateLimiter(max_calls=10, window_seconds=60)
        if not limiter.allow("192.168.1.1"):
            raise HTTPException(429, "Too many requests")
    """

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls      = max_calls
        self.window_seconds = window_seconds
        self._windows: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now    = time.time()
        cutoff = now - self.window_seconds
        calls  = self._windows[key]

        # Evict expired timestamps
        calls[:] = [t for t in calls if t > cutoff]

        if len(calls) >= self.max_calls:
            return False
        calls.append(now)
        return True

    def remaining(self, key: str) -> int:
        now    = time.time()
        cutoff = now - self.window_seconds
        calls  = self._windows.get(key, [])
        active = [t for t in calls if t > cutoff]
        return max(0, self.max_calls - len(active))


# Pre-built limiters for each sensitive endpoint category
_limiters: dict[str, SlidingWindowRateLimiter] = {
    # Voice pipeline — expensive: STT + LLM + TTS
    "voice":       SlidingWindowRateLimiter(max_calls=30,  window_seconds=60),
    # Auth — prevent OAuth abuse
    "auth":        SlidingWindowRateLimiter(max_calls=10,  window_seconds=60),
    # File upload/ingest — CPU-intensive
    "upload":      SlidingWindowRateLimiter(max_calls=20,  window_seconds=60),
    # Screenshot — paid API call
    "screenshot":  SlidingWindowRateLimiter(max_calls=10,  window_seconds=60),
    # General API
    "api":         SlidingWindowRateLimiter(max_calls=120, window_seconds=60),
}


def check_rate_limit(category: str, ip: str) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    limiter = _limiters.get(category, _limiters["api"])
    allowed = limiter.allow(ip)
    if not allowed:
        log_security_event("RATE_LIMIT_EXCEEDED", f"category={category}", ip)
    return allowed


def get_client_ip(request) -> str:
    """
    Extract real client IP, respecting X-Forwarded-For from Cloud Run / Vercel.
    Validates that the forwarded IP is actually a valid IP address.
    """
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        # X-Forwarded-For can be a comma-separated list; first is the client
        candidate = forwarded.split(",")[0].strip()
        try:
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


# ── Input sanitisation ────────────────────────────────────────────────────────

# UUID v4 pattern — used for doc_id validation
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Spotify URI: spotify:track:<base62>  or  spotify:album:...  etc.
_SPOTIFY_URI_RE = re.compile(
    r"^spotify:(track|album|artist|playlist|episode|show):[A-Za-z0-9]{22}$"
)

# Spotify device ID: 40 hex chars
_DEVICE_ID_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)

# Safe filename characters (no path separators, null bytes, or shell metacharacters)
_UNSAFE_FILENAME_RE = re.compile(r'[^\w\s\-.]')

# Private/loopback/link-local IP ranges blocked for SSRF protection
_BLOCKED_CIDRS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),   # AWS/GCP metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def sanitise_filename(raw: str) -> str:
    """
    Sanitise a user-supplied filename:
    - Strip unicode non-printable chars
    - Remove path separators and shell metacharacters
    - Collapse whitespace
    - Truncate to 200 chars
    - Ensure it has a safe extension
    """
    # Normalise unicode (NFC) and strip control characters
    clean = unicodedata.normalize("NFC", raw)
    clean = "".join(c for c in clean if unicodedata.category(c) != "Cc")
    # Remove path components
    clean = clean.replace("\\", "").replace("/", "").replace("..", "")
    # Remove non-word chars except hyphen and dot
    clean = _UNSAFE_FILENAME_RE.sub("_", clean)
    # Collapse whitespace
    clean = "_".join(clean.split())
    # Truncate
    clean = clean[:200]
    # Must not be empty after sanitisation
    if not clean or clean in (".", ".."):
        clean = "upload"
    return clean


def validate_uuid(value: str) -> bool:
    """Return True if value is a valid UUID v4."""
    return bool(_UUID_RE.match(value.strip()))


def validate_spotify_uri(uri: str) -> bool:
    """Return True if value matches Spotify URI format."""
    return bool(_SPOTIFY_URI_RE.match(uri.strip()))


def validate_device_id(device_id: str) -> bool:
    """Return True if value is a valid Spotify device ID (40 hex chars)."""
    return not device_id or bool(_DEVICE_ID_RE.match(device_id.strip()))


def validate_url_safe(url: str) -> tuple[bool, str]:
    """
    Validate a user-supplied URL for safe server-side fetching.
    Blocks:
      - Non-HTTP(S) schemes (file://, ftp://, gopher://, etc.)
      - Private/loopback/link-local IP targets (SSRF)
      - URLs with credentials (http://user:pass@...)
      - Overly long URLs
    Returns (is_safe, reason).
    """
    if len(url) > 2048:
        return False, "URL too long"

    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL"

    if parsed.scheme not in ("http", "https"):
        return False, f"Scheme '{parsed.scheme}' not allowed — only http/https"

    if parsed.username or parsed.password:
        return False, "URLs with credentials are not allowed"

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "No hostname"

    # Resolve hostname to IP for SSRF check
    import socket
    try:
        addrs = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addrs:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
                for blocked in _BLOCKED_CIDRS:
                    if ip in blocked:
                        return False, f"Target IP {ip_str} is in a private/reserved range"
            except ValueError:
                pass
    except socket.gaierror:
        return False, f"Could not resolve hostname: {hostname}"

    return True, ""


def validate_base64_image(b64: str, max_bytes: int = 4 * 1024 * 1024) -> tuple[bool, str]:
    """
    Validate a base64-encoded image:
    - Must be valid base64
    - Decoded size must not exceed max_bytes (default 4MB)
    - Decoded bytes must start with a known image magic number
    """
    import base64 as _b64

    # Strip data URI prefix if present
    if "," in b64:
        b64 = b64.split(",", 1)[1]

    # Check character set (base64 + padding)
    if not re.match(r'^[A-Za-z0-9+/]*={0,2}$', b64):
        return False, "Invalid base64 characters"

    try:
        decoded = _b64.b64decode(b64)
    except Exception:
        return False, "Could not decode base64"

    if len(decoded) > max_bytes:
        return False, f"Image too large ({len(decoded) // 1024}KB > {max_bytes // 1024}KB limit)"

    # Magic number validation
    MAGIC = {
        b"\xff\xd8\xff":  "JPEG",
        b"\x89PNG\r\n":   "PNG",
        b"GIF8":           "GIF",
        b"RIFF":           "WEBP",
    }
    for magic, fmt in MAGIC.items():
        if decoded[:len(magic)] == magic:
            return True, fmt

    return False, "Unknown or unsupported image format"


def sanitise_text_input(text: str, max_length: int = 1000) -> str:
    """
    Sanitise free-text input (queries, memory facts, etc.):
    - Strip null bytes
    - Normalise unicode
    - Truncate
    Does NOT HTML-encode — that's the frontend's job.
    """
    text = text.replace("\x00", "")
    text = unicodedata.normalize("NFC", text)
    return text[:max_length].strip()


def sanitise_content_disposition(filename: str) -> str:
    """
    Produce a safe Content-Disposition filename value.
    Prevents header injection by removing CRLFs and quotes.
    """
    safe = filename.replace('"', "").replace("\r", "").replace("\n", "").replace(";", "")
    return safe[:200]


# ── Secure error responses ────────────────────────────────────────────────────

def safe_error(public_msg: str, internal_exc: Exception = None,
               log_fn=None) -> dict:
    """
    Return a safe error dict with a user-facing message.
    Logs the internal exception without exposing it to the client.
    """
    if internal_exc and log_fn:
        log_fn("Internal error: %s", internal_exc)
    return {"detail": public_msg}
