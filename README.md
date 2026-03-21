# Veronica — AI Voice Assistant

A Jarvis-style voice assistant with a particle UI, local LLM, and Spotify integration.

```
User speaks → Whisper STT → Ollama NLP → pyttsx3/ElevenLabs TTS → spoken response
                                       ↘ Spotify Web API → music plays
```

---

## Quick Start

### Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| Python 3.13 | Backend | python.org |
| Node.js 18+ | Frontend | nodejs.org |
| ffmpeg | Audio conversion (webm→wav for Whisper) | see below |
| Ollama | Local LLM | ollama.com |

**ffmpeg install:**
- Windows: `winget install ffmpeg`  or  https://ffmpeg.org/download.html
- macOS:   `brew install ffmpeg`
- Linux:   `sudo apt install ffmpeg`

---

## Spotify Setup (required)

1. Go to https://developer.spotify.com/dashboard → Create App
2. In App Settings → Redirect URIs, add **exactly**:
   ```
   http://127.0.0.1:3000/api/v1/auth/callback
   ```
3. Copy your **Client ID** and **Client Secret**

> The redirect URI must match in three places:
> - Spotify Dashboard
> - `SPOTIFY_REDIRECT_URI` in your `.env`
> - The default in `core/config.py`
> All three are set to `http://127.0.0.1:3000/api/v1/auth/callback`.

---

## Backend Setup

```bash
cd backend

# Create virtualenv
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — fill in SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

# Start Ollama (separate terminal)
ollama serve
ollama pull qwen2.5-coder:7b   # one-time, ~4 GB

# Start backend
python main.py
# → Running on http://127.0.0.1:5000
```

---

## Frontend Setup

```bash
cd frontend
npm install
npm start
# → Running on http://localhost:3000
```

Open http://localhost:3000

---

## Usage

1. Click **Connect with Spotify** and log in
2. Open Spotify on any device → Devices → select **"Veronica — AI Assistant"**
3. **Hold the mic button** (or hold **Space**) and speak
4. Release to process

### Voice commands
- `"What is the speed of light?"` → spoken answer
- `"Play Blinding Lights by The Weeknd"` → searches Spotify + starts playback
- `"Play something by Daft Punk"` → same
- `"What time is it in Tokyo?"` → spoken answer

---

## Architecture

```
veronica/
├── backend/
│   ├── main.py                     FastAPI app, CORS, static serving
│   ├── core/
│   │   ├── config.py               All settings via .env
│   │   └── logging_config.py
│   ├── api/
│   │   └── routes.py               All HTTP endpoints
│   └── services/
│       ├── spotify_service.py      OAuth2 + Search API + Play API + CSRF
│       ├── stt_service.py          Whisper via faster-whisper + ffmpeg
│       ├── tts_service.py          ElevenLabs / pyttsx3 female voice
│       └── nlp_service.py          Ollama qwen2.5-coder:7b
└── frontend/
    └── src/
        ├── components/
        │   ├── VeronicaUI.js       Main UI + voice pipeline orchestration
        │   ├── SpotifyPlayer.js    Now-playing + controls
        │   └── Login.js
        ├── hooks/
        │   ├── useParticleEngine.js  520-particle canvas with anime.js
        │   ├── useSpotifyPlayer.js   Web Playback SDK lifecycle
        │   └── useMediaRecorder.js   Browser mic capture
        └── services/
            ├── voiceService.js     STT / NLP / TTS / play API calls
            └── spotifyService.js   Token check on mount
```

---

## URL/Proxy Map (dev)

| Browser request | Goes to | Handler |
|---|---|---|
| `GET /api/v1/auth/login` | setupProxy → backend:5000 | Redirect to Spotify |
| `GET /api/v1/auth/callback?code=...` | setupProxy → backend:5000 | Exchange tokens, redirect `/` |
| `GET /api/v1/auth/token` | setupProxy → backend:5000 | Return access token |
| `POST /api/v1/voice/transcribe` | setupProxy → backend:5000 | Whisper STT |
| `POST /api/v1/voice/query` | setupProxy → backend:5000 | Ollama NLP |
| `POST /api/v1/voice/synthesize` | setupProxy → backend:5000 | TTS audio |
| `POST /api/v1/voice/play` | setupProxy → backend:5000 | Spotify play |
| `GET /*` | React Router | React SPA |

---

## Troubleshooting

**Login redirects to wrong URL** — Ensure `SPOTIFY_REDIRECT_URI` in `.env` matches the Redirect URI in Spotify Dashboard exactly: `http://127.0.0.1:3000/api/v1/auth/callback`

**Music says "playing X" but nothing plays** — Select "Veronica — AI Assistant" as the active device in the Spotify app first, then ask again.

**"Spotify Premium required"** — Web Playback SDK only works with a Premium account.

**Whisper slow on first run** — Model downloads on first use (~150 MB for `base`). Use `WHISPER_MODEL=tiny` for fastest response.

**pyttsx3 male voice on Windows** — The service scans all voices for female names (Zira, Hazel, Susan, etc.). Install extra TTS voices via Windows Settings → Time & Language → Speech → Add voices.

**ffmpeg not found** — Install ffmpeg and ensure it's on your PATH. Test with `ffmpeg -version` in a terminal.
