# Veronica — Complete Technical Summary
### Full system documentation for recreation in another session

---

## Overview

Veronica is a full-stack AI voice assistant with:
- **Voice I/O**: Browser mic → Whisper STT → Ollama LLM → pyttsx3/ElevenLabs TTS
- **Live web search**: Tavily API grounding for current-events queries
- **Spotify integration**: Web Playback SDK (device registration) + Web API (all controls)
- **Particle UI**: 500-particle canvas with anime.js morphing (idle float / listening ? / speaking frequency)
- **Queue management**: View, add, click-to-play from queue
- **Barge-in**: Hold Space to interrupt Veronica mid-sentence and ask again
- **Music/voice interlock**: Hold Space pauses music → ask query → music auto-resumes after answer

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, uvicorn |
| LLM | Ollama local (qwen2.5-coder:7b) |
| STT | faster-whisper (local) + ffmpeg for audio conversion |
| TTS | pyttsx3 (local, female voice) or ElevenLabs (premium) |
| Web Search | Tavily AI API (free tier: 1000/month) |
| Music | Spotify Web Playback SDK + Spotify Web API |
| Frontend | React 18, anime.js 3.2, Web Audio API |

---

## Project Structure

```
veronica/
├── backend/
│   ├── main.py                        FastAPI app entry point
│   ├── requirements.txt
│   ├── .env.example
│   ├── core/
│   │   ├── config.py                  Pydantic Settings — all env config
│   │   └── logging_config.py          Structured stdout logging
│   ├── api/
│   │   └── routes.py                  All HTTP endpoints (mounted at /api/v1)
│   └── services/
│       ├── spotify_service.py         OAuth2 + all Spotify Web API calls
│       ├── stt_service.py             Whisper STT with ffmpeg audio conversion
│       ├── tts_service.py             ElevenLabs / pyttsx3 female voice TTS
│       ├── nlp_service.py             Ollama NLP with music intent detection
│       └── search_service.py          Tavily web search + live-data classifier
└── frontend/
    ├── package.json                   React 18, animejs, no proxy field
    └── src/
        ├── App.js                     Root — checks token, renders Login or VeronicaUI
        ├── index.js                   ReactDOM.createRoot entry
        ├── setupProxy.js              CRA proxy: /api/* → localhost:5000
        ├── styles/global.css          CSS reset, scrollbar, selection
        ├── hooks/
        │   ├── useParticleEngine.js   500-particle canvas engine
        │   ├── useSpotifyPlayer.js    SDK device + Web API controls
        │   └── useMediaRecorder.js    Browser mic capture (no timeslicing)
        ├── components/
        │   ├── VeronicaUI.js          Main orchestrator UI
        │   ├── VeronicaUI.css         Split layout, HUD, text input styles
        │   ├── SpotifyPlayer.js       Right panel: art, controls, queue
        │   ├── SpotifyPlayer.css      Player styles including queue
        │   ├── Login.js               Spotify connect screen
        │   └── Login.css
        └── services/
            ├── voiceService.js        All backend API calls + Web Audio player
            └── spotifyService.js      Token check on mount
```

---

## Backend — File by File

### `backend/main.py`
FastAPI application. Key details:
- `lifespan` context manager for startup/shutdown logging
- `TrustedHostMiddleware` active in production (DEBUG=False) — blocks unexpected Host headers
- `CORSMiddleware` restricted to `settings.CORS_ORIGINS` (only `http://127.0.0.1:3000` in dev), methods limited to GET/POST/DELETE, specific headers only
- API docs (`/api/docs`) hidden in production
- Router mounted at `/api/v1` prefix
- In production (when `frontend/build/` exists): mounts `/static` for React assets, catch-all route serves `index.html` for React Router
- Dev: `uvicorn` binds to `127.0.0.1:5000` only (not 0.0.0.0)

### `backend/core/config.py`
Pydantic `BaseSettings` reads from `.env` file. All settings:
```
APP_NAME, DEBUG, SECRET_KEY
CORS_ORIGINS                    # ["http://127.0.0.1:3000"]
SPOTIFY_CLIENT_ID
SPOTIFY_CLIENT_SECRET
SPOTIFY_REDIRECT_URI            # http://127.0.0.1:3000/api/v1/auth/callback
OLLAMA_BASE_URL                 # http://localhost:11434
OLLAMA_MODEL                    # qwen2.5-coder:7b
WEB_SEARCH_ENABLED              # True
WEB_SEARCH_MAX_RESULTS          # 4
TAVILY_API_KEY
WHISPER_MODEL                   # base
TTS_RATE, TTS_VOLUME
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
```
**Critical**: `SPOTIFY_REDIRECT_URI` must be registered EXACTLY in Spotify Dashboard as `http://127.0.0.1:3000/api/v1/auth/callback`

### `backend/api/routes.py`
All endpoints, all mounted under `/api/v1`:

**Auth endpoints:**
- `GET /auth/login` → calls `build_auth_url()`, returns 302 redirect to Spotify
- `GET /auth/callback?code=&state=` → calls `exchange_code_for_tokens(code, state)`. On CSRF fail redirects to `/?auth_error=csrf`. On success redirects to `/`
- `GET /auth/token` → returns `{"access_token": str|null}` — used by React on mount
- `POST /auth/logout` → clears token store

**Voice pipeline:**
- `POST /voice/transcribe` — accepts `multipart/form-data` with `audio` file field (max 10MB), calls `transcribe_audio()`, returns `{"transcript": str}`
- `POST /voice/query` — body `{"text": str}`, calls `nlp.process_query()`, returns `{"response", "is_music", "music_query", "music_action"}`
- `POST /voice/synthesize` — body `{"text": str}`, returns raw audio bytes (WAV or MP3)

**Music control:**
- `POST /voice/play` — body `{"query": str, "device_id": str}` → `search_and_play()`
- `POST /voice/pause-music` → `pause_playback()`
- `POST /voice/resume-music` → `resume_playback()`
- `POST /voice/next-track` → `skip_next()`
- `POST /voice/previous-track` → `skip_previous()`

**Queue:**
- `GET /music/queue` → `get_queue()` returns `{"currently_playing": {}, "queue": [...]}`
- `POST /music/queue` — body `{"query": str, "device_id": str}` → `search_and_queue()`

**Misc:**
- `DELETE /voice/history` → clears NLP conversation history
- `GET /health` → `{"status": "ok"}`

---

### `backend/services/spotify_service.py`

**`TokenStore` class (singleton `_token_store`)**
- Stores `_access_token`, `_refresh_token`, `_expires_at` in memory
- `is_expired` property: returns True if within 60s of expiry
- `store(access_token, refresh_token, expires_in)`: saves tokens
- `clear()`: wipes all tokens on logout
- `create_state()`: generates `secrets.token_urlsafe(24)` CSRF state, stores with timestamp, purges states older than 10 minutes
- `verify_and_consume_state(state)`: pops state from dict (one-time use), verifies it was issued within 10 minutes — prevents CSRF and replay attacks

**OAuth functions:**
- `build_auth_url()`: constructs Spotify `/authorize` URL with scopes: `streaming user-read-email user-read-private user-read-playback-state user-modify-playback-state user-read-currently-playing`
- `exchange_code_for_tokens(code, state)`: verifies CSRF state first, then POSTs to Spotify token endpoint, stores result
- `refresh_access_token()`: uses refresh token to get new access token silently
- `get_valid_access_token()`: returns stored token, auto-refreshing if expired

**Web API functions (all use `httpx.AsyncClient`):**
- `search_track(query, token)`: `GET /v1/search?type=track&limit=1`, returns first result dict
- `play_track(uri, device_id, token)`: `PUT /v1/me/player/play` with `{"uris": [uri]}` and `?device_id=`
- `search_and_play(query, device_id)`: combines search + play, returns `{success, track_name, artist_name, track_uri}`
- `pause_playback()`: `PUT /v1/me/player/pause`
- `resume_playback()`: `PUT /v1/me/player/play` with empty body
- `skip_next()`: `POST /v1/me/player/next`
- `skip_previous()`: `POST /v1/me/player/previous`
- `get_queue()`: `GET /v1/me/player/queue`, returns slim track objects `{uri, name, artist, album_art, duration_ms}`, capped at 8 items
- `add_to_queue(track_uri, device_id)`: `POST /v1/me/player/queue?uri=<uri>`
- `search_and_queue(query, device_id)`: search + add to queue

---

### `backend/services/stt_service.py`

**The core audio problem**: Chrome's `MediaRecorder` sends `audio/webm;codecs=opus`. If timesliced (calling `start(100)`), the EBML container header only appears in the first chunk — concatenated chunks produce an invalid file. **Fix applied on frontend** (no timeslicing) **and backend** (multi-attempt conversion).

**`_get_model()`**: lazy-loads `faster_whisper.WhisperModel` on first call (avoids startup delay). Falls back to `openai-whisper` if faster-whisper not installed. Model size from `settings.WHISPER_MODEL`.

**`_run_ffmpeg(inp_path, out_path, format_hint)`**: runs ffmpeg to convert to WAV 16kHz mono. Returns True/False. Logs only last 300 chars of stderr (prevents giant ffmpeg build info in logs).

**`_convert_to_wav(audio_bytes, content_type)`**: 
- Writes to `.bin` temp file (not `.webm`) — avoids extension-based format guessing that caused "low score" detection
- Tries 4 strategies in order: auto-detect → force webm → force ogg → force opus
- Stops at first success, cleans up temp files in `finally`

**`transcribe_audio(audio_bytes, content_type)`**:
- WAV passthrough (no conversion)
- Otherwise calls `_convert_to_wav`
- Writes WAV to temp file, runs Whisper
- `faster_whisper` API: `model.transcribe(path, language="en", beam_size=5, vad_filter=True, vad_parameters={"min_silence_duration_ms": 300})` — VAD filter removes silence
- `openai-whisper` fallback: `model.transcribe(path, language="en", fp16=False)`
- Cleans up temp file in `finally`

---

### `backend/services/tts_service.py`

**`_tts_elevenlabs(text)`**: POSTs to `https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}`. Uses Rachel voice (`EXAVITQu4vr4xnSDxMaL`) by default. Returns MP3 bytes.

**`_select_female_voice(engine)`**: enumerates all pyttsx3 voices, searches for female keywords in voice ID/name: `zira, hazel, susan, helen, linda, sarah, karen, victoria, samantha, fiona, moira, veena, tessa, female, woman, girl, f_`. Falls back to `voices[1]` (second voice, often female on Windows). This fixes Windows defaulting to male voice.

**`_tts_pyttsx3_to_bytes(text)`**: runs pyttsx3 synchronously (blocking), saves to temp WAV, reads bytes back. Must run in executor (not async-safe).

**`synthesize_speech(text)`** (public):
- Tries ElevenLabs if `ELEVENLABS_API_KEY` set
- Falls back to pyttsx3 via `asyncio.get_event_loop().run_in_executor(None, _tts_pyttsx3_to_bytes, text)`
- Returns `(bytes, mime_type)` — MP3 for ElevenLabs, WAV for pyttsx3

---

### `backend/services/nlp_service.py`

**`OllamaClient`**: async httpx client for `POST {OLLAMA_BASE_URL}/api/chat`. Sends `{"model", "messages", "stream": False, "options": {"num_predict": max_tokens, "temperature": 0.7}}`. Returns `data["message"]["content"]`. `is_available()` pings `/api/tags` with 3s timeout.

**`MusicIntent` dataclass**: `is_music: bool`, `action: str` (play/stop/pause/resume/next/previous/queue), `search_query: str`

**`NLPService.process_query(query)`** returns:
```python
{
    "response":     str,   # text to speak
    "is_music":     bool,
    "music_query":  str|None,   # only for play/queue actions
    "music_action": str|None,   # play|stop|pause|resume|next|previous|queue|None
}
```

**`_detect_music_intent(query)`** — keyword matching first (no LLM call):
- Checks `q_lower` against `_MUSIC_STOP_KW`, `_MUSIC_PAUSE_KW`, `_MUSIC_RESUME_KW`, `_MUSIC_NEXT_KW`, `_MUSIC_PREV_KW`, `_MUSIC_QUEUE_KW` sets
- Only calls Ollama if `_MUSIC_PLAY_KW` keywords present
- Ollama uses `MUSIC_EXTRACT_PROMPT` to return `{"query": "Artist Song"}` JSON
- Strips markdown fences from Ollama response before `json.loads()`
- Fallback: strips "play/listen to/queue/put on" prefix from raw query

**`_handle_general_query(query)`**:
- Calls `needs_search(query)` — regex classifier for live-data queries
- If search needed: calls `web_search()`, formats context with `format_search_context()`
- Prepends `[Today's date is ...]` to system prompt always
- Prepends search context to system prompt if available
- Maintains `_conversation_history` list, keeps last 10 turns
- Calls Ollama with full history

**`_music_confirmation(intent)`**: returns spoken string like "Playing X on Spotify." or "Skipping to the next track."

---

### `backend/services/search_service.py`

**`needs_search(query)`**: compiled regex `_LIVE_PATTERNS` checks for: today/now/currently, date/time queries, "who is the president/ceo/etc", latest/recent/2024/2025/2026, "is X still", stock prices, weather, scores, news, alive/dead. Returns bool.

**`web_search(query, max_results)`**: POSTs to `https://api.tavily.com/search` with `{"api_key", "query", "search_depth": "basic", "max_results", "include_answer": True, "include_raw_content": False}`. Extracts:
1. `data["answer"]` — Tavily's pre-synthesized summary (most useful for voice)
2. Individual `data["results"]` items with `content` field

**`format_search_context(results, query)`**: formats into numbered list with today's date header. Injected at top of Ollama system prompt. Ends with instruction to not mention the search.

---

## Frontend — File by File

### `src/setupProxy.js`
CRA proxy config. Proxies `/api/*` → `http://127.0.0.1:5000`. This means:
- `GET /api/v1/auth/login` in browser → backend
- `GET /api/v1/auth/callback` — Spotify redirects to `http://127.0.0.1:3000/api/v1/auth/callback` which is proxied to backend
- No `/auth` proxy (removed) — all auth goes through `/api/v1/auth/*`
- **No `"proxy"` field in package.json** — that conflicts with setupProxy.js

### `src/App.js`
On mount: calls `spotifyService.getToken()` → `GET /api/v1/auth/token`. If token returned → renders `<VeronicaUI token={token} />`. Otherwise renders `<Login />`.

### `src/hooks/useMediaRecorder.js`

**Critical design**: calls `recorder.start()` with NO timeslice argument. This records as one continuous stream. The EBML container header is written once at the start. `recorder.requestData()` called just before `stop()` to flush remaining audio into one final `ondataavailable` event. Result: a single complete valid webm blob.

- `startRecording()`: requests mic with `{channelCount:1, sampleRate:16000, echoCancellation:true, noiseSuppression:true, autoGainControl:true}`. Detects best MIME type: `audio/webm;codecs=opus` → `audio/webm` → `audio/ogg;codecs=opus` etc.
- `stopRecording()`: returns Promise, calls `requestData()` + `stop()`, resolves with final Blob in `onstop` callback. Stops all mic tracks, sets `isRecording=false`.

### `src/hooks/useParticleEngine.js`

**Initialization problem solved**: Canvas `width`/`height` CSS attributes are 0 at React mount time. Using `initDoneRef` to guard init caused init to run before canvas had real dimensions. **Fix**: `ResizeObserver` on the canvas element itself calls `bootEngine(canvas)` whenever the canvas has non-zero layout dimensions.

**`bootEngine(canvas)`**:
- Cancels any existing RAF
- Stores `W, H, CX, CY` in `engineRef`
- Pre-computes `idlePosRef` using golden-ratio phyllotaxis: `r = sqrt((i+0.5)/N) * min(W,H)*0.47`, angle `= i * 2.399963` — evenly distributes 500 particles across canvas
- Each particle gets individual drift params: `driftPhaseX/Y` (random 0-2π), `driftSpeedX/Y` (0.25-0.65), `driftAmp` (10-32px)
- Starts RAF render loop

**Render loop per state:**
- `idle`: `fx = p.x + sin(t * driftSpeedX + driftPhaseX) * driftAmp * 0.55` — each particle on its own sinusoidal path. Opacity twinkles individually.
- `listening`: particles locked to question mark shape (morphed via anime.js), shimmer `±1.8px` with per-particle phase offsets
- `speaking`: radial expansion `fx = p.x + nx * (fa[i] * 1.4 + sin(t*2.4+i*0.09)*2)` where `fa[i]` is real audio frequency amplitude pushed from `pushFrequencyData()`

**`morphTo(targets, opts)`**: cancels pending anime.js animations, creates one anime instance per particle interpolating `{x, y}` to target position. Duration jitter `±350ms`, delay jitter `±200ms`.

**Shape generators:**
- `circleTargets(cx, cy, r, count)`: evenly spaced on circle
- `questionMarkTargets(cx, cy, count)`: outer arc (22%), inner arc (14%), tail sweep (20%), stem (10%), dot (remainder) — all computed geometrically
- `idleTargets(W, H, count)`: golden-ratio spiral with per-particle drift metadata

**State → morph mapping:**
- `idle` → scatter to `idlePosRef` positions (duration 1200ms, easeInOutCubic)
- `listening` → question mark (800ms, easeInOutQuart)
- `speaking` → circle (700ms, easeOutCubic)

**`pushFrequencyData(uint8Array)`**: copies `uint8Array[i] / 4` into `freqAmpsRef` (scales 0-255 → 0-64). Called every RAF frame during TTS playback from Web Audio analyser.

### `src/hooks/useSpotifyPlayer.js`

**Hybrid approach**: SDK only for device registration + `player_state_changed` events. All controls use Spotify Web API via `fetch()` — always visible in DevTools Network tab, never fail silently.

**Why not SDK controls**: React StrictMode double-mounts cause `onSpotifyWebPlaybackSDKReady` to fire during first mount. Cleanup tears it down. Second mount sets the callback again but SDK already called it — so `playerRef.current` stays null. All `player.togglePlay()` etc. silently do nothing via `?.` operator.

**SDK setup guard**: checks `window.Spotify?.Player` before script load — if already loaded (hot reload), calls `initPlayer()` directly. If `playerRef.current` already set, returns early (prevents duplicate player).

**`apiCall(method, path, body)`**: helper using `tokenRef.current` (always-current ref, not stale state closure). Handles 204 No Content as success. Logs errors with status code.

**Position tick**: `startTick(startPos)` sets a 500ms interval incrementing `position` state. Uses `pausedRef.current` (not `isPaused` state) inside the closure to avoid stale capture. `stopTick()` clears interval.

**Controls (all Web API):**
- `togglePlay()`: if `isPaused` → `PUT /play` with `{device_id}`, else `PUT /pause`
- `nextTrack()`: `POST /next`
- `previousTrack()`: `POST /previous`
- `seek(ms)`: optimistic `setPosition(ms)` + `PUT /seek?position_ms=N`
- `setVolume(val)`: optimistic `setVolumeState(val)` + `PUT /volume?volume_percent=N`
- `playUri(uri)`: `PUT /play` with `{uris: [uri], device_id}` — used for click-to-play from queue
- `refreshQueue()`: `GET /api/v1/music/queue` → `setQueue(data.queue)` — called on-demand only (no polling)

**`player_state_changed` handler**: updates `currentTrack`, `duration`, `isPaused`, `pausedRef`, calls `startTick` or `stopTick`, sets `isActive=true`.

### `src/services/voiceService.js`

- `transcribe(blob)`: `POST /api/v1/voice/transcribe` with FormData `audio` field, returns `transcript` string
- `query(text)`: `POST /api/v1/voice/query`, returns `{response, is_music, music_query, music_action}`
- `synthesize(text)`: `POST /api/v1/voice/synthesize`, returns audio Blob
- `playOnSpotify(query, device_id)`: `POST /api/v1/voice/play`
- `controlMusic(action)`: maps action to endpoint — `stop/pause` → `/voice/pause-music`, `resume` → `/voice/resume-music`, `next` → `/voice/next-track`, `previous` → `/voice/previous-track`
- `fetchQueue()`: `GET /api/v1/music/queue`
- `addToQueue(query, device_id)`: `POST /api/v1/music/queue`
- `playAudioWithAnalyzer(blob, onFrame, onEnd)`:
  - Creates `AudioContext`, decodes blob to `AudioBuffer`
  - Creates `BufferSource` + `AnalyserNode` (fftSize 256)
  - Starts RAF loop calling `onFrame(uint8Array)` every frame — used to push frequency data into particle engine
  - **Returns `stop()` function** (not the source node) — calling it cancels RAF, calls `source.stop()`, closes AudioContext, fires `onEnd()`. `stopped` flag prevents double-fire.
  - `source.onended` also fires `onEnd()` on natural completion, but only if `stopped=false`

### `src/components/VeronicaUI.js`

**State:**
- `uiState`: `'idle'|'listening'|'speaking'` — drives particle engine morphing
- `statusText`: displayed in HUD
- `transcript`, `response`: conversation display
- `isProcessing`: disables mic button and text input
- `textInput`: controlled input value
- `authError`: set from `?auth_error=` URL param on redirect

**Refs:**
- `canvasRef`: passed to `useParticleEngine` and `useSpotifyPlayer`
- `stopSpeakingRef`: holds the `stop()` function from `playAudioWithAnalyzer` — called to interrupt TTS
- `wasPlayingRef`: `true` when music was paused for hold-to-speak — triggers auto-resume after response
- `uiStateRef`: mirror of `uiState` for use in event listeners (avoids stale closures)
- `inputRef`: ref to main text input for Space bar guard
- `spaceDownTimeRef`, `spaceHoldFiredRef`: tap-vs-hold detection

**`speakText(text)`**:
1. Sets `uiState='speaking'`
2. Calls `voiceService.synthesize(text)` → blob
3. Calls `voiceService.playAudioWithAnalyzer(blob, pushFrequencyData, onEnd)`
4. Stores returned `stopFn` in `stopSpeakingRef`
5. `onEnd` callback: clears `stopSpeakingRef`, calls `resetToIdle()`, checks `wasPlayingRef` → if true, calls `voiceService.controlMusic('resume')` and resets `wasPlayingRef=false`

**`handleQuery(text)`** — shared by voice and text input:
1. `activateElement()` — unlocks Spotify autoplay permissions (required for text input path)
2. `voiceService.query(text)` → `nlpResult`
3. Routes on `nlpResult.music_action`:
   - `play`: `voiceService.playOnSpotify(music_query, deviceId)` concurrent with `speakText`
   - `stop/pause`: `controlMusic('pause')` first, then `speakText`
   - `resume`: `controlMusic('resume')` first, then `speakText`
   - `next/previous`: control first, then `speakText`
   - `queue`: `voiceService.addToQueue(music_query, deviceId)`
4. Error paths all check `wasPlayingRef` and resume music

**`handleMicRelease()`**: STT → calls `handleQuery(text)`

**Space bar (tap vs hold)**:
- `keydown`: if in INPUT/TEXTAREA → return. If `uiState==='speaking'` → barge-in immediately. Otherwise set `spaceDownTimeRef.current=Date.now()` and `setTimeout(300ms)`. At 300ms: if `!isPaused` → `wasPlayingRef=true` + `controlMusic('pause')`, then `handleMicPress()`
- `keyup`: `clearTimeout`. If `isRecording` → `handleMicRelease()`. If tap (<300ms, not held) → `controlMusic(isPaused ? 'resume' : 'pause')` (music toggle)

**Split layout**: CSS class `split-active` added to root when `spotifyActive`. Left panel transitions from `flex: 1 1 100%` to `flex: 0 0 55%`. Right panel transitions from `flex: 0 0 0%` to `flex: 0 0 45%`. Duration 0.65s cubic-bezier.

### `src/components/SpotifyPlayer.js`

Props: `currentTrack, isPaused, isActive, position, duration, onTogglePlay, onNext, onPrev, onSeek, onVolumeChange, volume, queue, deviceId, onQueueChange, onPlayUri`

**Progress bar**: click handler computes `(e.clientX - rect.left) / rect.width * duration` → calls `onSeek(ms)`. Thumb appears on hover via CSS.

**Queue list**: each item has `onClick={() => onPlayUri?.(track.uri)}`. Renders `sp-queue-art-wrap` containing album art + `sp-queue-play-icon` overlay (hidden, shown on hover). CSS: art dims to 0.4 opacity on hover, play icon fades in.

**Add to queue**: `handleAddToQueue()` — calls `voiceService.addToQueue(queueInput, deviceId)`, shows `✓ Track — Artist` success message for 3 seconds. On success: `setTimeout(() => onQueueChange?.(), 800)` — 800ms delay gives Spotify backend time to register the track before re-fetching.

### `src/components/SpotifyPlayer.css`
Notable: `.sp-progress-track` has `padding: 8px 0; margin: -8px 0` to expand the clickable area to 20px tall despite being visually 4px. Queue items have `cursor: pointer` and hover background. `sp-queue-art-wrap` is `position: relative` to host the absolute-positioned play icon overlay.

---

## API Endpoint Map

```
Frontend (localhost:3000)          setupProxy.js         Backend (localhost:5000)
        |                              |                          |
GET  /api/v1/auth/login       ──────► │ ──────────────────────► GET /api/v1/auth/login
GET  /api/v1/auth/callback    ◄────── │ ◄────────────────────── (Spotify redirects here)
GET  /api/v1/auth/token       ──────► │ ──────────────────────► returns access_token
POST /api/v1/auth/logout      ──────► │ ──────────────────────► clears token store
POST /api/v1/voice/transcribe ──────► │ ──────────────────────► Whisper STT
POST /api/v1/voice/query      ──────► │ ──────────────────────► Ollama NLP
POST /api/v1/voice/synthesize ──────► │ ──────────────────────► pyttsx3/ElevenLabs TTS
POST /api/v1/voice/play       ──────► │ ──────────────────────► Spotify search + play
POST /api/v1/voice/pause-music──────► │ ──────────────────────► Spotify pause
POST /api/v1/voice/resume-music─────► │ ──────────────────────► Spotify resume
POST /api/v1/voice/next-track ──────► │ ──────────────────────► Spotify next
POST /api/v1/voice/previous-track───► │ ──────────────────────► Spotify previous
GET  /api/v1/music/queue      ──────► │ ──────────────────────► Spotify queue fetch
POST /api/v1/music/queue      ──────► │ ──────────────────────► Spotify add to queue
DELETE /api/v1/voice/history  ──────► │ ──────────────────────► clears NLP history

Direct to Spotify Web API (from frontend, no proxy):
POST https://api.spotify.com/v1/me/player/play         (togglePlay, playUri)
PUT  https://api.spotify.com/v1/me/player/pause        (togglePlay)
POST https://api.spotify.com/v1/me/player/next         (nextTrack)
POST https://api.spotify.com/v1/me/player/previous     (previousTrack)
PUT  https://api.spotify.com/v1/me/player/seek         (seek)
PUT  https://api.spotify.com/v1/me/player/volume       (setVolume)
```

---

## Complete User Interaction Flows

### Voice query flow
1. User holds Space (or holds mic button)
2. If music playing: `wasPlayingRef=true`, `controlMusic('pause')`
3. `handleMicPress()`: `activateElement()`, `uiState='listening'`, particles morph to `?`, `startRecording()`
4. User releases Space
5. `stopRecording()` → Blob
6. `POST /api/v1/voice/transcribe` → transcript string
7. `POST /api/v1/voice/query` → `{response, is_music, music_action, music_query}`
8. If general: `POST /api/v1/voice/synthesize` → audio blob → `playAudioWithAnalyzer` → particles pulse with frequency, `uiState='speaking'`
9. Audio ends → `onEnd()` → `resetToIdle()` → if `wasPlayingRef`: `controlMusic('resume')`

### Music play via voice
1. User says "play Shape of You by Ed Sheeran"
2. STT → "play Shape of You by Ed Sheeran"
3. NLP: keyword check finds "play" → Ollama extracts `{"query": "Shape of You Ed Sheeran"}` → returns `{is_music:true, music_action:"play", music_query:"Shape of You Ed Sheeran"}`
4. Frontend: `activateElement()`, concurrent `speakText("Playing...")` + `playOnSpotify("Shape of You Ed Sheeran", deviceId)`
5. Backend `search_and_play`: `GET /v1/search?q=Shape+of+You+Ed+Sheeran&type=track&limit=1` → gets URI → `PUT /v1/me/player/play` with `{"uris": ["spotify:track:..."], device_id: "..."}`

### Barge-in flow
1. Veronica is speaking (`stopSpeakingRef` holds `stop()` function)
2. User presses Space
3. `uiStateRef.current === 'speaking'` → `handleMicPress()` called immediately (no hold wait)
4. `handleMicPress()` calls `interruptSpeaking()` → `stopSpeakingRef.current()` → cancels RAF, `source.stop()`, closes AudioContext, fires `onEnd()` which calls `resetToIdle()`
5. 80ms wait for AudioContext to close
6. Mic opens, recording starts

---

## Setup Instructions

### Prerequisites
- Python 3.13, Node.js 18+, ffmpeg on PATH, Ollama installed

### Spotify Dashboard
1. Create app at developer.spotify.com
2. Add redirect URI: `http://127.0.0.1:3000/api/v1/auth/callback`
3. Copy Client ID and Client Secret

### Backend
```bash
cd backend
python -m venv venv
venv\Scripts\activate          # Windows
source venv/bin/activate       # macOS/Linux
pip install -r requirements.txt
cp .env.example .env
# Fill in: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, TAVILY_API_KEY
python main.py
```

### Ollama
```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

### Frontend
```bash
cd frontend
npm install
npm start
```

---

## requirements.txt
```
fastapi==0.115.5
uvicorn[standard]==0.32.1
python-multipart==0.0.17
pydantic==2.10.3
pydantic-settings==2.6.1
httpx==0.28.1
faster-whisper==1.1.0
pyttsx3==2.98
elevenlabs==1.9.0
python-dotenv==1.0.1
tavily-python==0.5.0
```

## package.json dependencies
```json
{
  "react": "^18.3.1",
  "react-dom": "^18.3.1",
  "react-scripts": "5.0.1",
  "animejs": "^3.2.2",
  "http-proxy-middleware": "^2.0.7"
}
```

---

## Key Bugs Fixed During Development

1. **EBML header (webm timeslicing)**: `recorder.start(100)` splits audio into fragments missing container headers. Fixed: `recorder.start()` no args + `requestData()` before stop.
2. **Particles invisible**: Canvas `width/height` are 0 at React mount. Fixed: `ResizeObserver` on canvas drives `bootEngine()` after layout.
3. **SDK controls silent**: `playerRef.current` null after StrictMode double-mount. Fixed: all controls use Spotify Web API `fetch()`.
4. **Redirect URI mismatch**: Backend routes at `/api/v1/auth/callback` but REDIRECT_URI was `/auth/callback`. Fixed: URI is `http://127.0.0.1:3000/api/v1/auth/callback` everywhere.
5. **Music not playing from text input**: `activateElement()` not called on text submit path. Fixed: called in `handleQuery()` which both paths use.
6. **Space in queue input pauses music**: Guard was `activeElement === inputRef.current` (only main input). Fixed: `tagName === 'INPUT' || 'TEXTAREA'` catches all inputs.
7. **Search returning 0 results**: `duckduckgo-search` renamed to `ddgs`, then `ddgs` switched backend to Bing which blocks scrapers. Fixed: switched to Tavily AI API.
8. **pyttsx3 male voice on Windows**: Registry default is first voice (male). Fixed: enumerate all voices, match female keywords, fall back to `voices[1]`.
9. **Audio format (ffmpeg)**: `.webm` file extension triggered low-score detection. Fixed: write to `.bin`, try 4 format hints in sequence.
