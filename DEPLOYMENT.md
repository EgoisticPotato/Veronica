# Veronica — Cloud Deployment Guide
> From local machine to a live URL anyone can access, step by step.

## Architecture Overview

```
Browser (anyone in the world)
    │
    ▼
Vercel (frontend — React)          Free tier, global CDN
    │  /api/v1/* requests
    ▼
Google Cloud Run (backend — FastAPI)    Free tier, auto-scales to zero
    ├── OpenAI Whisper API              STT — $0.006/min
    ├── Ollama on GCP VM  ─OR─ Gemini   LLM — free
    ├── ElevenLabs API                  TTS — free 10k chars/month
    ├── Qdrant Cloud                    Vector DB — free 1GB
    ├── Tavily                          Web search — free 1k/month
    └── OpenRouter GPT-4o               Vision — pay per use
```

---

## Step 1 — Prerequisites (install once)

Install the Google Cloud CLI and Docker Desktop before anything else.

```bash
# Google Cloud CLI
# Windows: https://cloud.google.com/sdk/docs/install
# macOS:   brew install google-cloud-sdk
# Linux:   https://cloud.google.com/sdk/docs/install

gcloud init          # log in + set project
gcloud auth configure-docker   # allow Docker to push to GCR

# Docker Desktop: https://www.docker.com/products/docker-desktop
```

Create a new Google Cloud project at https://console.cloud.google.com. Enable these APIs:
- Cloud Run API
- Container Registry API (or Artifact Registry API)
- Cloud Build API

---

## Step 2 — Handle the LLM problem

Ollama can't run on Cloud Run because the container has no GPU and the models
are too large to load fast enough. You have two options.

**Option A — Gemini 2.0 Flash (recommended, fully free)**

Gemini is the cleanest option: free, fast, no infrastructure to manage.
You already have the Gemini client code from a previous version. To switch back:

In `nlp_service.py`, replace `OllamaClient` with `GeminiClient`:

```python
# In NLPService.client property, change to:
self._client = GeminiClient(
    api_key=settings.GEMINI_API_KEY,
    model=settings.GEMINI_MODEL,
)
```

And add to `.env.production`:
```env
GEMINI_API_KEY=your_key_from_aistudio.google.com
GEMINI_MODEL=gemini-2.0-flash
```

**Option B — Ollama on a GCP VM (keeps local LLM feel)**

Create a cheap `e2-standard-2` VM (2 vCPU, 8 GB RAM) in the same GCP region
as your Cloud Run service. Install Ollama on it. In Cloud Run, set:
```env
OLLAMA_BASE_URL=http://INTERNAL_VM_IP:11434
```
The VM costs ~$50/month on-demand, but you can stop it when not in use.
This is the right choice if you want to demo "fully private, no data leaves my infrastructure."

---

## Step 3 — Build and push the Docker image

```bash
cd veronica/backend

# Set your project ID
PROJECT_ID=your-gcp-project-id
IMAGE=gcr.io/$PROJECT_ID/veronica-backend:latest

# Build the image (uses Dockerfile + requirements.cloud.txt)
docker build -t $IMAGE .

# Test it locally before pushing
docker run --rm -p 8080:8080 \
  --env-file .env.production \
  $IMAGE

# If it starts without errors, push to Google Container Registry
docker push $IMAGE
```

The first build takes 5–10 minutes because it installs all Python packages.
Subsequent builds are much faster because Docker caches unchanged layers.

---

## Step 4 — Deploy to Cloud Run

```bash
gcloud run deploy veronica-backend \
  --image $IMAGE \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 10 \
  --min-instances 0 \
  --max-instances 3 \
  --port 8080 \
  --set-env-vars "$(cat .env.production | grep -v '^#' | grep -v '^$' | tr '\n' ',')"
```

**Why these values?**

`--memory 4Gi` is needed because `sentence-transformers` (the embedding model) loads ~1.5 GB into RAM, and the FastAPI workers need headroom on top of that.

`--min-instances 0` means the service scales to zero when nobody is using it — you pay nothing. The first request after idle takes ~3–5 seconds (cold start). For a portfolio project this is fine. For production with paying users, set `--min-instances 1`.

`--timeout 300` gives 5 minutes for long operations like PDF ingestion.

`--allow-unauthenticated` makes the service publicly accessible. Cloud Run's CORS settings (controlled by your FastAPI middleware) still restrict which frontends can call it.

After deployment, Cloud Run prints a URL like `https://veronica-backend-xxxx-uc.a.run.app`. Save this — it's your backend URL.

---

## Step 5 — Handle persistent data

Cloud Run containers are ephemeral — the `data/` directory (doc_registry.json, memory.json)
resets on every deployment or container restart. You have two options.

**Option A — Google Cloud Storage (recommended)**

Create a GCS bucket and mount it using Cloud Storage FUSE. Add to your `gcloud run deploy`:
```bash
--add-volume name=veronica-data,type=cloud-storage,bucket=your-bucket-name \
--add-volume-mount volume=veronica-data,mount-path=/app/data
```

The `data/` directory then persists across restarts. Enable the Cloud Storage API
and create the bucket first:
```bash
gsutil mb -p $PROJECT_ID gs://veronica-data-$PROJECT_ID
```

**Option B — Accept ephemerality (easiest for portfolio)**

For a portfolio demo, losing the doc registry on redeploy is acceptable. Users
re-upload documents after deployment. The Qdrant vectors persist (they're in Qdrant Cloud),
only the local registry mapping is lost. This is a known limitation worth mentioning
in interviews — it shows you understand the tradeoffs.

---

## Step 6 — Deploy the frontend to Vercel

```bash
# Install Vercel CLI
npm install -g vercel

cd veronica/frontend

# Create a production .env for the frontend
# The only frontend env var needed is the backend URL
echo "REACT_APP_API_URL=https://veronica-backend-xxxx-uc.a.run.app" > .env.production
```

Update `setupProxy.js` — in production the proxy isn't used, but you need
the frontend to know the backend URL:

```js
// In voiceService.js, change all fetch('/api/v1/...') to use the env var:
const BASE = process.env.REACT_APP_API_URL || '';
// Then: fetch(`${BASE}/api/v1/voice/query`, ...)
```

Deploy:
```bash
vercel --prod
```

Vercel asks which framework (React), build command (`npm run build`), output directory (`build`).
It gives you a URL like `https://veronica.vercel.app`.

---

## Step 7 — Update Spotify credentials

In your Spotify Developer Dashboard (https://developer.spotify.com/dashboard):

1. Open your app settings.
2. Add `https://your-app.vercel.app/api/v1/auth/callback` to Redirect URIs.
3. Update `SPOTIFY_REDIRECT_URI` in your Cloud Run environment variables.

In Cloud Run console → your service → Edit & Deploy → Variables & Secrets →
update `SPOTIFY_REDIRECT_URI` to the new URL.

---

## Step 8 — Update Cloud Run CORS

In your Cloud Run environment variables, update:
```env
CORS_ORIGINS=["https://your-app.vercel.app"]
```

Without this, the browser will block requests from Vercel to Cloud Run.

---

## Step 9 — Test the live deployment

Open `https://your-app.vercel.app`. You should see the Veronica login screen.
Click Connect Spotify, complete OAuth, and the app should work exactly as locally.

Test checklist:
- Voice input (hold Space, speak, release)
- Text input
- Music play/pause/queue via voice
- Document upload + RAG query
- Screenshot analysis
- Memory (say "my name is X", then ask "what's my name?")

---

## Cost breakdown (realistic monthly estimate for a portfolio project)

| Service | Free tier | Estimated usage | Cost |
|---|---|---|---|
| Google Cloud Run | 2M requests, 360k vCPU-seconds | ~500 requests/day | $0 |
| Vercel | Unlimited for personal | Frontend only | $0 |
| Gemini 2.0 Flash | 1,500 req/day | ~50 queries/day | $0 |
| OpenAI Whisper | None | 50 × 10s = 8 min/day | ~$0.05/month |
| ElevenLabs | 10,000 chars/month | ~50 responses | $0 |
| Qdrant Cloud | 1 GB storage | < 100 MB typical | $0 |
| Tavily | 1,000 req/month | ~100/month | $0 |
| OpenRouter GPT-4o | Pay per use | 10 screenshots/day | ~$0.30/month |

**Total: ~$0.35/month** for active daily personal use. Effectively free.

---

## What to say in interviews

"Veronica is deployed on Google Cloud Run with auto-scaling to zero — it costs
essentially nothing when not in use but scales instantly under load. The backend
is containerised with Docker using a multi-stage build that separates build
dependencies from the runtime image, keeping it lean. I use OpenAI's Whisper API
for speech transcription and ElevenLabs for text-to-speech, both cloud-native
and swappable via environment variables. The RAG system uses Qdrant Cloud as the
vector store, with sentence-transformers embeddings running in the container.
The frontend deploys to Vercel with a global CDN. The whole thing costs under
a dollar a month to run."

---

## Troubleshooting

**Cold start takes too long**: Set `--min-instances 1` in Cloud Run. Costs ~$7/month.

**Memory errors (OOM)**: Increase to `--memory 8Gi`. The sentence-transformers model is the main culprit.

**CORS errors in browser**: Check `CORS_ORIGINS` in Cloud Run env vars exactly matches your Vercel URL (no trailing slash).

**Spotify OAuth redirect fails**: The redirect URI in Spotify Dashboard must exactly match `SPOTIFY_REDIRECT_URI` in Cloud Run env — including `https://` vs `http://`.

**ElevenLabs quota exceeded**: Free tier is 10,000 chars/month. Either upgrade ($5/month) or switch `TTS_PROVIDER=openai` and use OpenAI TTS ($0.015/1k chars).

**sentence-transformers model download on cold start**: The first request after deployment triggers a ~90MB model download from HuggingFace. This causes a ~20s cold start on the first RAG operation. Pre-warm by adding a `/warmup` endpoint or using `--min-instances 1`.
