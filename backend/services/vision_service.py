"""
Vision Service — Veronica's eyes
Captures the screen using mss, sends to Ollama (gemma3:12b — multimodal)
for analysis. Returns a concise, voice-friendly description.
"""

import base64
import io
import logging
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# Screen capture constraints
IMG_MAX_W = 1280
IMG_MAX_H = 720
JPEG_QUALITY = 70


def capture_screen() -> bytes:
    """
    Capture the primary monitor as JPEG bytes.
    Uses mss for fast, cross-platform screenshot.
    Resizes + compresses with Pillow for efficient LLM input.
    """
    import mss
    import mss.tools

    with mss.mss() as sct:
        # Monitor 1 = primary display (monitor 0 = all monitors combined)
        monitor = sct.monitors[1]
        shot = sct.grab(monitor)
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)

    # Compress with Pillow if available
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img.thumbnail((IMG_MAX_W, IMG_MAX_H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        jpeg_bytes = buf.getvalue()
        logger.info(
            "Screen captured: %dx%d → %dx%d, %dKB JPEG",
            monitor["width"], monitor["height"],
            img.width, img.height,
            len(jpeg_bytes) // 1024,
        )
        return jpeg_bytes
    except ImportError:
        logger.warning("Pillow not installed — sending raw PNG to LLM")
        return png_bytes


async def analyze_screen(question: str = "What is on the screen?") -> str:
    """
    Capture the screen and ask Ollama to describe it.
    Uses the multimodal gemma3:12b model.
    Returns a concise string suitable for TTS.
    """
    # Capture
    try:
        image_bytes = capture_screen()
    except Exception as e:
        logger.error("Screen capture failed: %s", e)
        raise RuntimeError(f"Could not capture screen: {e}")

    # Encode
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Build Ollama chat request with image
    messages = [
        {
            "role": "system",
            "content": (
                "You are Veronica, an AI assistant analyzing a screenshot for the user. "
                "Be concise and conversational. Your response will be spoken aloud. "
                "Describe what you see clearly in 2-4 sentences. "
                "Never use markdown, bullet points, or formatting symbols."
            ),
        },
        {
            "role": "user",
            "content": question,
            "images": [b64_image],
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 300},
                },
                headers={"Content-Type": "application/json"},
            )

        if r.status_code >= 400:
            raise RuntimeError(f"Ollama vision error {r.status_code}: {r.text[:200]}")

        description = r.json()["message"]["content"].strip()
        logger.info("Vision result: %s", description[:100])
        return description

    except httpx.TimeoutException:
        raise RuntimeError("Vision analysis timed out (gemma3:12b may be loading)")
    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        raise
