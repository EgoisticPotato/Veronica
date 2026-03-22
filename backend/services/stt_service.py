"""
Speech-to-Text Service using faster-whisper
Browser audio pipeline: MediaRecorder → POST → ffmpeg → WAV → Whisper

The key challenge: Chrome sends audio/webm but the EBML container can be
malformed if the frontend timesliced the recording. We fix this on the
frontend (no timeslicing) AND defend here with:
  1. Force-try multiple input format hints to ffmpeg
  2. Fall back to piping raw bytes as stdin (avoids file extension ambiguity)
"""

import logging
import subprocess
import tempfile
import os

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
            from core.config import settings
            logger.info("Loading Whisper model: %s", settings.WHISPER_MODEL)
            _model = WhisperModel(
                settings.WHISPER_MODEL,
                device="cpu",
                compute_type="int8",
            )
            logger.info("Whisper model loaded")
        except ImportError:
            logger.warning("faster-whisper not available, trying openai-whisper")
            import whisper
            from core.config import settings
            _model = whisper.load_model(settings.WHISPER_MODEL)
    return _model


def _run_ffmpeg(inp_path: str, out_path: str, input_format_hint: str = None) -> bool:
    """
    Run ffmpeg to convert inp_path → out_path (WAV 16kHz mono).
    Optionally force input format with -f <hint>.
    Returns True on success.
    """
    cmd = ["ffmpeg", "-y"]
    if input_format_hint:
        cmd += ["-f", input_format_hint]
    cmd += [
        "-i",   inp_path,
        "-ar",  "16000",
        "-ac",  "1",
        "-f",   "wav",
        out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=30)
    if result.returncode != 0:
        logger.debug(
            "ffmpeg attempt (fmt=%s) failed: %s",
            input_format_hint or "auto",
            result.stderr.decode(errors="replace")[-300:],   # last 300 chars only
        )
        return False
    return True


def _convert_to_wav(audio_bytes: bytes, content_type: str) -> bytes:
    """
    Convert raw browser audio to WAV 16kHz mono.

    Strategy (tries in order until one succeeds):
      1. Auto-detect (no -f flag) — works when container header is intact
      2. Force -f webm — helps when mime says webm but header is borderline
      3. Force -f ogg  — Chrome sometimes wraps opus in ogg despite webm mime
      4. Force -f opus — raw opus stream (no container)
    """
    # Write raw bytes to a temp file without an extension
    # (extension-based guessing is what causes the "low score" detection)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as inp:
        inp.write(audio_bytes)
        inp_path = inp.name

    out_path = inp_path.replace(".bin", ".wav")

    try:
        # Ordered list of attempts: (format_hint_or_None, label)
        attempts = [
            (None,   "auto-detect"),
            ("webm", "force webm"),
            ("ogg",  "force ogg"),
            ("opus", "force opus"),
        ]

        for fmt_hint, label in attempts:
            logger.debug("ffmpeg conversion attempt: %s", label)
            if _run_ffmpeg(inp_path, out_path, fmt_hint):
                logger.info("Audio converted via ffmpeg (%s)", label)
                with open(out_path, "rb") as f:
                    return f.read()

        raise RuntimeError(
            "All ffmpeg conversion attempts failed. "
            "Audio may be corrupt or an unsupported format. "
            "content_type=" + content_type
        )
    finally:
        for p in [inp_path, out_path]:
            try:
                os.unlink(p)
            except FileNotFoundError:
                pass


async def transcribe_audio(audio_bytes: bytes, content_type: str = "audio/webm") -> str:
    """
    Transcribe browser MediaRecorder audio to text.

    Args:
        audio_bytes:  Raw bytes from browser (webm, ogg, mp4, wav)
        content_type: MIME type reported by MediaRecorder

    Returns:
        Transcribed text (may be empty string if no speech detected)
    """
    if not audio_bytes:
        raise ValueError("Empty audio bytes")

    logger.info(
        "Transcribing %d bytes, content_type=%s", len(audio_bytes), content_type
    )

    # WAV passthrough — no conversion needed
    if "wav" in content_type:
        wav_bytes = audio_bytes
    else:
        wav_bytes = _convert_to_wav(audio_bytes, content_type)

    # Write WAV to temp file for Whisper
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name

    try:
        model = _get_model()

        # faster-whisper (preferred)
        if hasattr(model, "feature_extractor"):
            segments, info = model.transcribe(
                tmp_path,
                language="en",
                beam_size=5,
                vad_filter=True,          # skip silent segments
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = " ".join(seg.text for seg in segments).strip()
        else:
            # openai-whisper fallback
            result = model.transcribe(tmp_path, language="en", fp16=False)
            text = result["text"].strip()

        logger.info("Transcribed: '%s'", text[:120])
        return text

    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
