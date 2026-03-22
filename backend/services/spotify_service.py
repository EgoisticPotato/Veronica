"""
Spotify OAuth2 Authorization Code Flow + Web API
- OAuth with CSRF state verification
- Token store with auto-refresh
- Search + playback via Spotify Web API (fixes "play song" doing nothing)
"""

import base64
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

SPOTIFY_AUTH_URL  = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE  = "https://api.spotify.com/v1"

SPOTIFY_SCOPES = " ".join([
    "streaming",
    "user-read-email",
    "user-read-private",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
])


# ─── Token Store ─────────────────────────────────────────────────────────────

class TokenStore:
    """
    In-memory token store (single-user dev).
    For multi-user production: swap for Redis with per-session keys.
    """

    def __init__(self):
        self._access_token:  Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._expires_at:    float = 0.0
        # CSRF state: maps state → timestamp so we can verify and expire it
        self._pending_states: dict[str, float] = {}

    # ── Token properties ─────────────────────────────────────────────────

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token

    @property
    def refresh_token(self) -> Optional[str]:
        return self._refresh_token

    @property
    def is_expired(self) -> bool:
        return time.time() >= self._expires_at - 60  # 60 s buffer

    def store(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self._access_token  = access_token
        self._refresh_token = refresh_token
        self._expires_at    = time.time() + expires_in
        logger.info("Tokens stored — expires in %ds", expires_in)

    def clear(self) -> None:
        self._access_token  = None
        self._refresh_token = None
        self._expires_at    = 0.0
        logger.info("Token store cleared")

    # ── CSRF state management ─────────────────────────────────────────────

    def create_state(self) -> str:
        """Generate a cryptographically random state and store it."""
        state = secrets.token_urlsafe(24)
        self._pending_states[state] = time.time()
        # Purge states older than 10 minutes
        cutoff = time.time() - 600
        self._pending_states = {
            k: v for k, v in self._pending_states.items() if v > cutoff
        }
        return state

    def verify_and_consume_state(self, state: str) -> bool:
        """
        Verify state exists and has not expired (10 min window).
        Consumes (removes) it to prevent replay.
        """
        issued_at = self._pending_states.pop(state, None)
        if issued_at is None:
            logger.warning("OAuth state not found: %s", state)
            return False
        if time.time() - issued_at > 600:
            logger.warning("OAuth state expired for: %s", state)
            return False
        return True


# Singleton
_token_store = TokenStore()


def get_token_store() -> TokenStore:
    return _token_store


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _b64_credentials() -> str:
    raw = f"{settings.SPOTIFY_CLIENT_ID}:{settings.SPOTIFY_CLIENT_SECRET}"
    return base64.b64encode(raw.encode()).decode()


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


# ─── OAuth Flow ───────────────────────────────────────────────────────────────

def build_auth_url() -> tuple[str, str]:
    """Build Spotify authorization URL. Returns (url, state)."""
    state = _token_store.create_state()
    params = {
        "response_type": "code",
        "client_id":     settings.SPOTIFY_CLIENT_ID,
        "scope":         SPOTIFY_SCOPES,
        "redirect_uri":  settings.SPOTIFY_REDIRECT_URI,
        "state":         state,
    }
    url = f"{SPOTIFY_AUTH_URL}?{urlencode(params)}"
    return url, state


async def exchange_code_for_tokens(code: str, state: str) -> dict:
    """
    Exchange authorization code for tokens.
    Verifies CSRF state before proceeding.
    Raises ValueError on state mismatch, httpx.HTTPStatusError on Spotify error.
    """
    if not _token_store.verify_and_consume_state(state):
        raise ValueError("Invalid or expired OAuth state — possible CSRF attempt")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
            },
            headers={
                "Authorization": f"Basic {_b64_credentials()}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        response.raise_for_status()
        data = response.json()

    _token_store.store(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        expires_in=data["expires_in"],
    )
    return data


async def refresh_access_token() -> Optional[str]:
    """Silently refresh access token using stored refresh token."""
    if not _token_store.refresh_token:
        logger.warning("No refresh token available")
        return None

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            SPOTIFY_TOKEN_URL,
            data={
                "grant_type":    "refresh_token",
                "refresh_token": _token_store.refresh_token,
            },
            headers={
                "Authorization": f"Basic {_b64_credentials()}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
        )
        if response.status_code != 200:
            logger.error("Token refresh failed %d: %s", response.status_code, response.text)
            return None
        data = response.json()

    _token_store.store(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", _token_store.refresh_token),
        expires_in=data["expires_in"],
    )
    logger.info("Access token refreshed")
    return data["access_token"]


async def get_valid_access_token() -> Optional[str]:
    """Return a valid token, refreshing automatically if expired."""
    if _token_store.is_expired and _token_store.refresh_token:
        return await refresh_access_token()
    return _token_store.access_token


# ─── Spotify Web API — Search & Playback ─────────────────────────────────────

async def search_track(query: str, access_token: str) -> Optional[dict]:
    """
    Search Spotify for a track.
    Returns the first track result dict or None.
    """
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{SPOTIFY_API_BASE}/search",
            params={"q": query, "type": "track", "limit": 1},
            headers=_auth_headers(access_token),
        )
        if response.status_code != 200:
            logger.error("Spotify search failed %d: %s", response.status_code, response.text)
            return None
        data = response.json()

    items = data.get("tracks", {}).get("items", [])
    if not items:
        logger.warning("No tracks found for query: %s", query)
        return None

    track = items[0]
    logger.info(
        "Found track: '%s' by %s",
        track["name"],
        track["artists"][0]["name"],
    )
    return track


async def play_track(track_uri: str, device_id: str, access_token: str) -> bool:
    """
    Start playback of a track on the given device via Spotify Web API.
    Retries once without device_id if 404 (device not found) — lets Spotify
    pick the active device automatically on the second attempt.
    """
    import asyncio

    async def _attempt(params: dict) -> int:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{SPOTIFY_API_BASE}/me/player/play",
                params=params,
                json={"uris": [track_uri]},
                headers={**_auth_headers(access_token), "Content-Type": "application/json"},
            )
        return r.status_code, r.text

    # First attempt: with specific device_id
    status, text = await _attempt({"device_id": device_id} if device_id else {})
    if status in (200, 204):
        logger.info("Playback started: %s on device %s", track_uri, (device_id or "auto")[:8])
        return True

    # 404 = device not registered yet — wait briefly and retry without device_id
    if status == 404:
        logger.warning("Device not found, retrying without device_id in 1s...")
        await asyncio.sleep(1)
        status, text = await _attempt({})
        if status in (200, 204):
            logger.info("Playback started (auto-device): %s", track_uri)
            return True

    logger.error("Playback failed %d: %s", status, text[:200])
    return False


async def search_and_play(query: str, device_id: str) -> dict:
    """
    Combined: search for a track and immediately play it.
    Returns a result dict with keys: success, track_name, artist_name, error.
    """
    token = await get_valid_access_token()
    if not token:
        return {"success": False, "error": "Not authenticated with Spotify"}

    track = await search_track(query, token)
    if not track:
        return {"success": False, "error": f"No track found for: {query}"}

    track_name  = track["name"]
    artist_name = track["artists"][0]["name"]
    track_uri   = track["uri"]

    success = await play_track(track_uri, device_id, token)
    if not success:
        return {
            "success": False,
            "track_name": track_name,
            "artist_name": artist_name,
            "error": "Playback failed — ensure Veronica device is active in Spotify",
        }

    return {
        "success":     True,
        "track_name":  track_name,
        "artist_name": artist_name,
        "track_uri":   track_uri,
    }


async def pause_playback() -> bool:
    """Pause current Spotify playback."""
    token = await get_valid_access_token()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.put(
            f"{SPOTIFY_API_BASE}/me/player/pause",
            headers=_auth_headers(token),
        )
    ok = response.status_code in (200, 204)
    logger.info("Pause playback → %d", response.status_code)
    return ok


async def resume_playback() -> bool:
    """Resume current Spotify playback."""
    token = await get_valid_access_token()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.put(
            f"{SPOTIFY_API_BASE}/me/player/play",
            headers={**_auth_headers(token), "Content-Type": "application/json"},
            json={},
        )
    ok = response.status_code in (200, 204)
    logger.info("Resume playback → %d", response.status_code)
    return ok


async def skip_next() -> bool:
    """Skip to next track."""
    token = await get_valid_access_token()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{SPOTIFY_API_BASE}/me/player/next",
            headers=_auth_headers(token),
        )
    return response.status_code in (200, 204)


async def skip_previous() -> bool:
    """Skip to previous track."""
    token = await get_valid_access_token()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{SPOTIFY_API_BASE}/me/player/previous",
            headers=_auth_headers(token),
        )
    return response.status_code in (200, 204)


async def get_queue() -> dict:
    """
    Fetch the current playback queue.
    Returns { currently_playing, queue: [...] } or empty dict on failure.
    """
    token = await get_valid_access_token()
    if not token:
        return {}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{SPOTIFY_API_BASE}/me/player/queue",
            headers=_auth_headers(token),
        )
    if response.status_code != 200:
        logger.warning("get_queue failed: %d", response.status_code)
        return {}
    data = response.json()

    def _slim_track(t: dict) -> dict:
        """Return only what the frontend needs — keep payload small."""
        if not t:
            return {}
        return {
            "uri":         t.get("uri", ""),
            "name":        t.get("name", ""),
            "artist":      t.get("artists", [{}])[0].get("name", ""),
            "album_art":   (t.get("album", {}).get("images") or [{}])[0].get("url", ""),
            "duration_ms": t.get("duration_ms", 0),
        }

    return {
        "currently_playing": _slim_track(data.get("currently_playing")),
        "queue": [_slim_track(t) for t in data.get("queue", [])[:8]],  # cap at 8
    }


async def add_to_queue(track_uri: str, device_id: str = None) -> bool:
    """
    Add a track URI to the playback queue.
    PUT https://api.spotify.com/v1/me/player/queue?uri=<uri>&device_id=<id>
    """
    token = await get_valid_access_token()
    if not token:
        return False

    params = {"uri": track_uri}
    if device_id:
        params["device_id"] = device_id

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{SPOTIFY_API_BASE}/me/player/queue",
            params=params,
            headers=_auth_headers(token),
        )
    ok = response.status_code in (200, 204)
    logger.info("add_to_queue %s → %d", track_uri, response.status_code)
    return ok


async def search_and_queue(query: str, device_id: str = None) -> dict:
    """Search for a track and add it to the queue."""
    token = await get_valid_access_token()
    if not token:
        return {"success": False, "error": "Not authenticated"}

    track = await search_track(query, token)
    if not track:
        return {"success": False, "error": f"No track found for: {query}"}

    ok = await add_to_queue(track["uri"], device_id)
    if not ok:
        return {"success": False, "error": "Could not add to queue"}

    return {
        "success":     True,
        "track_name":  track["name"],
        "artist_name": track["artists"][0]["name"],
        "track_uri":   track["uri"],
    }