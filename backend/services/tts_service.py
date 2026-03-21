"""
Text-to-Speech Service

Provider priority (auto mode):
  1. ElevenLabs  — best quality, if ELEVENLABS_API_KEY set
  2. Google TTS  — good quality, free on Cloud Run (uses service account), if GOOGLE_TTS_ENABLED=true
  3. Browser TTS — returns empty audio + header, frontend uses window.speechSynthesis
                   Always works, zero configuration needed

TTS_PROVIDER options:
  "auto"        — tries providers in order above (recommended for cloud)
  "elevenlabs"  — ElevenLabs only (fails hard if key missing)
  "google"      — Google Cloud TTS only
  "browser"     — skip backend TTS, tell frontend to use Web Speech API
  "local"       — pyttsx3 (Windows/Linux/macOS local dev only)
"""

import logging
logger = logging.getLogger(__name__)

# Special sentinel returned when browser TTS should be used
BROWSER_TTS_SENTINEL = b""
BROWSER_TTS_MIME     = "text/plain"   # frontend checks this to trigger Web Speech API


# ── ElevenLabs ─────────────────────────────────────────────────────────────────

async def _tts_elevenlabs(text: str) -> tuple[bytes, str]:
    import httpx
    from core.config import settings
    if not settings.ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            json={
                "text": text,
                "model_id": "eleven_monolingual_v1",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            headers={
                "xi-api-key": settings.ELEVENLABS_API_KEY,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
        )
        resp.raise_for_status()
    logger.info("ElevenLabs TTS (%d bytes)", len(resp.content))
    return resp.content, "audio/mpeg"


# ── Google Cloud TTS ───────────────────────────────────────────────────────────

async def _tts_google(text: str) -> tuple[bytes, str]:
    """
    Google Cloud Text-to-Speech REST API.
    On Cloud Run, authentication is automatic via the service account
    (no API key needed — the container has an identity).
    Falls back to API key via GOOGLE_TTS_API_KEY if set.
    Free tier: 4 million characters/month for Standard voices.
    """
    import httpx
    import base64
    from core.config import settings

    # Get access token — try service account metadata first, then API key
    access_token = None
    use_api_key  = bool(settings.GOOGLE_TTS_API_KEY)

    if not use_api_key:
        # Fetch token from GCP metadata server (automatic on Cloud Run)
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                )
                if r.status_code == 200:
                    access_token = r.json()["access_token"]
        except Exception:
            pass  # not on GCP — try API key path

    if not access_token and not use_api_key:
        raise RuntimeError("Google TTS: not on Cloud Run and GOOGLE_TTS_API_KEY not set")

    url = "https://texttospeech.googleapis.com/v1/text:synthesize"
    params = {"key": settings.GOOGLE_TTS_API_KEY} if use_api_key else {}
    headers = {"Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": "en-US",
            "name":         "en-US-Standard-F",   # female voice
            "ssmlGender":   "FEMALE",
        },
        "audioConfig": {
            "audioEncoding": "MP3",
            "speakingRate":  1.05,
            "pitch":         1.0,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, params=params, headers=headers)
        resp.raise_for_status()

    audio_b64 = resp.json().get("audioContent", "")
    audio_bytes = base64.b64decode(audio_b64)
    logger.info("Google TTS (%d bytes)", len(audio_bytes))
    return audio_bytes, "audio/mpeg"


# ── pyttsx3 (local only) ───────────────────────────────────────────────────────

def _tts_local(text: str) -> tuple[bytes, str]:
    import tempfile, os
    import pyttsx3
    from core.config import settings

    female_kw = ["zira","hazel","susan","helen","linda","sarah","karen",
                 "victoria","samantha","fiona","moira","veena","tessa","female"]
    engine = pyttsx3.init()
    engine.setProperty("rate",   settings.TTS_RATE)
    engine.setProperty("volume", settings.TTS_VOLUME)
    for v in (engine.getProperty("voices") or []):
        name = (v.name or "").lower()
        if any(k in name for k in female_kw):
            engine.setProperty("voice", v.id)
            logger.info("pyttsx3 voice: %s", v.name)
            break

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp = f.name
    try:
        engine.save_to_file(text, tmp)
        engine.runAndWait()
        with open(tmp, "rb") as f:
            data = f.read()
        logger.info("pyttsx3 TTS (%d bytes)", len(data))
        return data, "audio/wav"
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


# ── Public API ─────────────────────────────────────────────────────────────────

async def synthesize_speech(text: str) -> tuple[bytes, str]:
    """
    Synthesize text → audio bytes + mime type.

    TTS_PROVIDER=auto   → ElevenLabs → Google TTS → browser sentinel
    TTS_PROVIDER=elevenlabs → ElevenLabs (hard fail if key missing)
    TTS_PROVIDER=google → Google TTS (hard fail if not configured)
    TTS_PROVIDER=browser → skip backend, return browser sentinel immediately
    TTS_PROVIDER=local  → pyttsx3 (local dev only)

    Browser sentinel: empty bytes + "text/plain" mime.
    The frontend checks for this and uses window.speechSynthesis instead.
    """
    from core.config import settings
    provider = settings.TTS_PROVIDER.lower()
    logger.info("TTS [%s]: '%s'", provider, text[:60])

    if provider == "elevenlabs":
        return await _tts_elevenlabs(text)

    if provider == "google":
        return await _tts_google(text)

    if provider == "browser":
        logger.info("TTS: browser sentinel (Web Speech API)")
        return BROWSER_TTS_SENTINEL, BROWSER_TTS_MIME

    if provider == "local":
        try:
            return _tts_local(text)
        except Exception as e:
            logger.warning("pyttsx3 failed (%s) — falling back to browser TTS", e)
            return BROWSER_TTS_SENTINEL, BROWSER_TTS_MIME

    # auto — try in order: ElevenLabs → Google → browser
    if settings.ELEVENLABS_API_KEY:
        try:
            return await _tts_elevenlabs(text)
        except Exception as e:
            logger.warning("ElevenLabs failed (%s) — trying Google TTS", type(e).__name__)

    try:
        return await _tts_google(text)
    except Exception as e:
        logger.warning("Google TTS failed (%s) — falling back to browser TTS", type(e).__name__)

    logger.info("TTS: all backends failed — using browser TTS sentinel")
    return BROWSER_TTS_SENTINEL, BROWSER_TTS_MIME
