"""
Speech-to-Text Service

Two modes selected by STT_PROVIDER in .env:
  - "openai"  : OpenAI Whisper API — cloud, fast, no local model needed (default for production)
  - "local"   : faster-whisper running locally — needs ffmpeg + model download (~1.5 GB)

For Cloud Run deployment, set STT_PROVIDER=openai and OPENAI_API_KEY in env vars.
For local development, set STT_PROVIDER=local (or omit — local is tried first).
"""

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


# ── OpenAI Whisper API ──────────────────────────────────────────────────────────

async def _transcribe_openai(audio_bytes: bytes, content_type: str) -> str:
    """
    Transcribe via OpenAI Whisper API.
    Sends raw browser audio directly — no ffmpeg conversion needed.
    Cost: $0.006/minute → ~$0.001 per 10-second voice query.
    """
    import httpx
    from core.config import settings

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to .env to use cloud STT.")

    # Map browser MIME types to file extensions the API accepts
    ext_map = {
        "audio/webm":      "webm",
        "audio/ogg":       "ogg",
        "audio/mp4":       "mp4",
        "audio/mpeg":      "mp3",
        "audio/wav":       "wav",
        "audio/x-wav":     "wav",
    }
    ext = ext_map.get(content_type.split(";")[0].strip(), "webm")

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
            files={
                "file": (f"audio.{ext}", audio_bytes, content_type),
            },
            data={
                "model":    "whisper-1",
                "language": "en",
                "response_format": "text",
            },
        )

    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI STT error {response.status_code}: {response.text[:200]}")

    text = response.text.strip()
    logger.info("OpenAI STT: '%s'", text[:120])
    return text


# ── Local faster-whisper ────────────────────────────────────────────────────────

_local_model = None


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel
        from core.config import settings
        logger.info("Loading local Whisper model: %s", settings.WHISPER_MODEL)
        _local_model = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded")
    return _local_model


def _run_ffmpeg(inp_path: str, out_path: str, fmt: str = None) -> bool:
    import subprocess
    cmd = ["ffmpeg", "-y"]
    if fmt:
        cmd += ["-f", fmt]
    cmd += ["-i", inp_path, "-ar", "16000", "-ac", "1", "-f", "wav", out_path]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0


def _convert_to_wav(audio_bytes: bytes, content_type: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(audio_bytes)
        inp = f.name
    out = inp.replace(".bin", ".wav")
    try:
        for fmt in [None, "webm", "ogg", "opus"]:
            if _run_ffmpeg(inp, out, fmt):
                with open(out, "rb") as f:
                    return f.read()
        raise RuntimeError("All ffmpeg attempts failed — audio may be corrupt")
    finally:
        for p in [inp, out]:
            try: os.unlink(p)
            except FileNotFoundError: pass


async def _transcribe_local(audio_bytes: bytes, content_type: str) -> str:
    wav = audio_bytes if "wav" in content_type else _convert_to_wav(audio_bytes, content_type)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        tmp = f.name
    try:
        model = _get_local_model()
        if hasattr(model, "feature_extractor"):   # faster-whisper
            segs, _ = model.transcribe(tmp, language="en", beam_size=5,
                                       vad_filter=True,
                                       vad_parameters={"min_silence_duration_ms": 300})
            text = " ".join(s.text for s in segs).strip()
        else:                                      # openai-whisper fallback
            text = model.transcribe(tmp, language="en", fp16=False)["text"].strip()
        logger.info("Local STT: '%s'", text[:120])
        return text
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass


# ── Public API ──────────────────────────────────────────────────────────────────

async def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """
    Transcribe browser audio.
    Routing: STT_PROVIDER=openai → OpenAI API; anything else → local faster-whisper.
    """
    from core.config import settings
    logger.info("Transcribing %d bytes [provider=%s]", len(audio_bytes), settings.STT_PROVIDER)

    if settings.STT_PROVIDER == "openai":
        return await _transcribe_openai(audio_bytes, content_type)
    else:
        return await _transcribe_local(audio_bytes, content_type)
