"""
Application configuration — Pydantic Settings
Single source of truth for all environment variables.

Local dev:   STT_PROVIDER=local,      TTS_PROVIDER=local,  OLLAMA running
Cloud Run:   STT_PROVIDER=openai,     TTS_PROVIDER=elevenlabs, Gemini API key set
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

    # ── App ────────────────────────────────────────────────────────────────────
    APP_NAME:   str  = "Veronica"
    DEBUG:      bool = False
    SECRET_KEY: str  = "change-me-in-production"   # overridden by Cloud Run secret
    ALLOWED_HOST: str = ""  # optional custom domain

    # ── CORS ───────────────────────────────────────────────────────────────────
    # Production: set to your Vercel URL e.g. ["https://veronica-xyz.vercel.app"]
    CORS_ORIGINS: List[str] = ["http://127.0.0.1:3000", "http://localhost:3000",
                              "https://veronica-drab.vercel.app"]

    # ── Spotify ────────────────────────────────────────────────────────────────
    SPOTIFY_CLIENT_ID:     str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    # Local:      http://127.0.0.1:3000/api/v1/auth/callback
    # Local dev:  http://127.0.0.1:3000/api/v1/auth/callback
    # Production:  https://veronica-drab.vercel.app/api/v1/auth/callback
    SPOTIFY_REDIRECT_URI:  str = "http://127.0.0.1:3000/api/v1/auth/callback"

    # ── LLM — Gemini (cloud, free 1500 req/day) ────────────────────────────────
    # Get key: https://aistudio.google.com/app/apikey
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL:   str = "gemini-2.0-flash"

    # ── LLM — Ollama (local dev fallback) ─────────────────────────────────────
    # Only used if GEMINI_API_KEY is empty (automatic fallback for local dev)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL:    str = "llama3.2"

    # ── Vision — OpenRouter GPT-4o (screenshot analysis) ──────────────────────
    # Get key: https://openrouter.ai (~$0.001/screenshot)
    # Falls back to Gemini native vision if not set
    OPENROUTER_API_KEY:      str = ""
    OPENROUTER_VISION_MODEL: str = "openai/gpt-4o"

    # ── STT ────────────────────────────────────────────────────────────────────
    # "openai"  → OpenAI Whisper API  ($0.006/min, no ffmpeg needed — use in Cloud Run)
    # "local"   → faster-whisper CPU  (free, needs ffmpeg — use for local dev)
    STT_PROVIDER:   str = "local"
    OPENAI_API_KEY: str = ""
    WHISPER_MODEL:  str = "base"   # only when STT_PROVIDER=local

    # ── TTS ────────────────────────────────────────────────────────────────────
    # "auto"       -> tries ElevenLabs -> Google TTS -> browser (recommended for cloud)
    # "elevenlabs" -> ElevenLabs only  (best quality, 10k chars/month free)
    # "google"     -> Google Cloud TTS (free on Cloud Run via service account)
    # "browser"    -> browser Web Speech API (zero cost, zero config)
    # "local"      -> pyttsx3          (Windows local dev only)
    TTS_PROVIDER:        str   = "local"    # set to "auto" for cloud deployment
    TTS_RATE:            int   = 175
    TTS_VOLUME:          float = 0.9
    ELEVENLABS_API_KEY:  str   = ""
    ELEVENLABS_VOICE_ID: str   = "EXAVITQu4vr4xnSDxMaL"
    GOOGLE_TTS_API_KEY:  str   = ""         # optional — auto-auth on Cloud Run

    # ── Web search ─────────────────────────────────────────────────────────────
    # Get free key: https://tavily.com (1000 searches/month free)
    WEB_SEARCH_ENABLED:     bool = True
    WEB_SEARCH_MAX_RESULTS: int  = 4
    TAVILY_API_KEY:         str  = ""

    # ── Qdrant ─────────────────────────────────────────────────────────────────
    # Local:  http://localhost:6333  (docker run -p 6333:6333 qdrant/qdrant)
    # Cloud:  https://xxx.qdrant.io  (free 1GB at https://qdrant.tech)
    QDRANT_URL:     str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""

    # ── RAG ────────────────────────────────────────────────────────────────────
    EMBEDDING_MODEL:     str   = "all-MiniLM-L6-v2"
    EMBEDDING_DIMENSION: int   = 384
    RAG_CHUNK_SIZE:      int   = 1000
    RAG_CHUNK_OVERLAP:   int   = 200
    RAG_TOP_K:           int   = 5
    RAG_SCORE_THRESHOLD: float = 0.20

    # ── File upload ─────────────────────────────────────────────────────────────
    MAX_UPLOAD_MB: int = 50


settings = Settings()
