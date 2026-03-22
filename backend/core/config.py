"""
Application configuration — Pydantic Settings
Single source of truth for all environment-driven config.

Local dev:  STT_PROVIDER=local,      TTS_PROVIDER=local
Cloud Run:  STT_PROVIDER=openai,     TTS_PROVIDER=elevenlabs
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────
    APP_NAME:   str  = "Veronica"
    DEBUG:      bool = False
    SECRET_KEY: str  = "change-me-in-production-use-secrets-token-hex-32"

    # ── CORS ───────────────────────────────────────────────────────────────
    # In production set this to your Vercel frontend URL, e.g.:
    #   CORS_ORIGINS=["https://veronica.vercel.app"]
    CORS_ORIGINS: List[str] = [
        "https://veronica-drab.vercel.app",
        "https://veronica-latest-backend.onrender.com",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://localhost:3001",
    ]

    # ── Spotify ────────────────────────────────────────────────────────────
    SPOTIFY_CLIENT_ID:     str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    # In production, set to your Vercel frontend URL + /api/v1/auth/callback
    # OR your backend URL if you want the redirect to hit the backend directly (safer for cookies)
    SPOTIFY_REDIRECT_URI:  str = "https://veronica-latest-backend.onrender.com/api/v1/auth/callback"

    # ── LLM — Ollama (local) ───────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL:    str = "gemini-2.0-flash" # Use gemini-2.0-flash via OpenRouter by default

    # ── Vision — OpenRouter GPT-4o (cloud, screenshot analysis only) ───────
    GEMINI_API_KEY:          str = ""
    GEMINI_MODEL:            str = "gemini-2.0-flash"
    OPENROUTER_API_KEY:      str = ""
    OPENROUTER_VISION_MODEL: str = "openai/gpt-4o"

    # "openai"  → OpenAI Whisper API (fast, cloud, no ffmpeg needed in container)
    # "local"   → faster-whisper on CPU (offline, needs ffmpeg + model download)
    STT_PROVIDER:  str = "openai"
    OPENAI_API_KEY: str = ""
    WHISPER_MODEL:  str = "base"    # used only when STT_PROVIDER=local

    # "elevenlabs" → ElevenLabs API (cloud, best quality)
    # "local"      → pyttsx3 with female voice selection (offline, Windows only)
    TTS_PROVIDER:         str = "elevenlabs"
    TTS_RATE:             int = 175
    TTS_VOLUME:           float = 0.9
    ELEVENLABS_API_KEY:   str = ""
    ELEVENLABS_VOICE_ID:  str = "EXAVITQu4vr4xnSDxMaL"

    # ── Web search ─────────────────────────────────────────────────────────
    WEB_SEARCH_ENABLED:   bool = True
    WEB_SEARCH_MAX_RESULTS: int = 4
    TAVILY_API_KEY:       str = ""

    # ── Qdrant vector DB ───────────────────────────────────────────────────
    QDRANT_URL:     str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""

    # ── RAG ────────────────────────────────────────────────────────────────
    EMBEDDING_MODEL:     str   = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int   = 384
    RAG_CHUNK_SIZE:      int   = 1000
    RAG_CHUNK_OVERLAP:   int   = 200
    RAG_TOP_K:           int   = 5
    RAG_SCORE_THRESHOLD: float = 0.20

    # ── File upload ────────────────────────────────────────────────────────
    MAX_UPLOAD_MB: int = 50


    # ── Deployment ──────────────────────────────────────────────────────────
    ALLOWED_HOST: str = ""   # optional custom domain for TrustedHostMiddleware

settings = Settings()