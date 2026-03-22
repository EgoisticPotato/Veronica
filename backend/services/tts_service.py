"""
Text-to-Speech Service
Priority: ElevenLabs (premium) → pyttsx3 with female voice selection
Fixes Windows male-voice default by explicitly enumerating and selecting female voices.
"""

import io
import logging
import os
import re
import tempfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ElevenLabs TTS (premium, best quality)
# ---------------------------------------------------------------------------

async def _tts_elevenlabs(text: str) -> bytes:
    """Generate speech via ElevenLabs API. Returns MP3 bytes."""
    from core.config import settings
    import httpx

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": settings.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# pyttsx3 TTS (local, offline) — with proper female voice selection
# ---------------------------------------------------------------------------

def _select_female_voice(engine) -> bool:
    """
    Enumerate system voices and select the best female one.
    Works on Windows (SAPI), macOS, and Linux (espeak).
    Returns True if a female voice was found and set.
    """
    voices = engine.getProperty("voices")
    if not voices:
        return False

    # Female voice keywords to search for in voice ID / name
    female_keywords = [
        "zira", "hazel", "susan", "helen", "linda", "sarah", "karen",
        "victoria", "samantha", "fiona", "moira", "veena", "tessa",
        "female", "woman", "girl", "f_",
    ]

    # First pass: explicit female keywords
    for voice in voices:
        voice_id_lower = voice.id.lower()
        voice_name_lower = voice.name.lower()
        combined = voice_id_lower + " " + voice_name_lower

        for kw in female_keywords:
            if kw in combined:
                engine.setProperty("voice", voice.id)
                logger.info("Selected female voice: %s", voice.name)
                return True

    # Second pass: pick any non-default (index > 0) — often female on Windows
    if len(voices) > 1:
        engine.setProperty("voice", voices[1].id)
        logger.info("Falling back to second voice: %s", voices[1].name)
        return True

    logger.warning("Could not find a female voice; using system default")
    return False


def _tts_pyttsx3_to_bytes(text: str) -> bytes:
    """Synthesize speech locally via pyttsx3, return WAV bytes."""
    import pyttsx3

    from core.config import settings

    engine = pyttsx3.init()
    _select_female_voice(engine)
    engine.setProperty("rate", settings.TTS_RATE)
    engine.setProperty("volume", settings.TTS_VOLUME)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        engine.save_to_file(text, tmp_path)
        engine.runAndWait()
        engine.stop()

        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def synthesize_speech(text: str) -> tuple[bytes, str]:
    """
    Convert text to speech audio.

    Returns:
        (audio_bytes, mime_type) — MP3 if ElevenLabs, WAV if pyttsx3
    """
    from core.config import settings

    # Try ElevenLabs first if key is configured
    if settings.ELEVENLABS_API_KEY:
        try:
            audio = await _tts_elevenlabs(text)
            logger.info("TTS via ElevenLabs (%d bytes)", len(audio))
            return audio, "audio/mpeg"
        except Exception as e:
            logger.warning("ElevenLabs TTS failed, falling back: %s", e)

    # Fallback: local pyttsx3
    import asyncio
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, _tts_pyttsx3_to_bytes, text)
    logger.info("TTS via pyttsx3 (%d bytes)", len(audio))
    return audio, "audio/wav"
