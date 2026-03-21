"""
NLP Service — Veronica's brain
LLM: Google Gemini 2.0 Flash (cloud, free tier — 1500 req/day)
Vision: OpenRouter GPT-4o (screenshots only — ~$0.001 each)

Required .env:
    GEMINI_API_KEY=...
    GEMINI_MODEL=gemini-2.0-flash
    OPENROUTER_API_KEY=...
    OPENROUTER_VISION_MODEL=openai/gpt-4o
"""

import logging
import re
from datetime import datetime
from typing import Optional

import httpx

from core.config import settings
from services.search_service import needs_search, web_search, format_search_context
from services.memory_service import get_memory_service

logger = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

SYSTEM_PROMPT = """You are Veronica, a sophisticated AI voice assistant.
You are intelligent, warm, and concise. Respond in 1-3 sentences unless a longer answer is genuinely needed.
You have a calm, feminine personality.

Rules:
- Keep responses conversational — they will be spoken aloud
- Never use markdown, bullet points, asterisks, hyphens, or any formatting symbols
- Spell out numbers and abbreviations as words when they will be spoken
- If you cannot answer something, say so gracefully
- If web search results are provided above, use them — they are current and accurate
- If document context is provided above, answer based on it
"""

# ── Music keyword lists ────────────────────────────────────────────────────────
_MUSIC_STOP_KW   = ["stop the music","stop music","stop playing","stop song","mute music",
                    "silence","turn off music","stop it","kill the music","stop now"]
_MUSIC_PAUSE_KW  = ["pause the music","pause music","pause song","pause it",
                    "can you pause","please pause","pause for now","hold the music"]
_MUSIC_RESUME_KW = ["resume music","continue music","continue playing","unpause",
                    "play again","keep playing","play on","resume playing","start again"]
_MUSIC_NEXT_KW   = ["next song","next track","skip this","skip song","next one",
                    "play next","change song","change track"]
_MUSIC_PREV_KW   = ["previous song","previous track","go back","last song",
                    "play previous","back to"]
_MUSIC_QUEUE_KW  = ["add to queue","add to my queue","queue up","queue this",
                    "put in queue","add this to queue","put this in queue",
                    "to the queue","to queue","to my queue","in the queue","in queue"]
_MUSIC_PLAY_KW   = ["play","music","song","track","album","artist","spotify","listen","put on"]

_MUSIC_STOP_KW_EXACT   = {"stop", "silence"}
_MUSIC_PAUSE_KW_EXACT  = {"pause", "wait"}
_MUSIC_RESUME_KW_EXACT = {"resume", "unpause"}
_MUSIC_NEXT_KW_EXACT   = {"next", "skip", "forward"}
_MUSIC_PREV_KW_EXACT   = {"previous", "prev", "back"}


# ── Gemini client ──────────────────────────────────────────────────────────────

class GeminiClient:
    """
    Async client for Google Gemini API.
    Free tier: 1500 requests/day, 15 RPM on gemini-2.0-flash.
    No streaming needed — TTS needs the full text anyway.
    """

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model   = model

    def is_configured(self) -> bool:
        return bool(self.api_key and self.model)

    async def chat(
        self,
        messages:   list[dict],
        system:     Optional[str] = None,
        max_tokens: int = 400,
    ) -> str:
        """
        Send a chat request to Gemini and return the text response.
        Messages use OpenAI-style format {role, content} — converted to Gemini format.
        """
        url = f"{GEMINI_BASE}/{self.model}:generateContent?key={self.api_key}"

        # Convert OpenAI roles to Gemini roles ("assistant" → "model")
        contents = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature":     0.7,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"Gemini API error {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Unexpected Gemini response: {data}") from e

    async def chat_with_images(
        self,
        text_prompt:   str,
        base64_images: list[str],
        system:        Optional[str] = None,
        max_tokens:    int = 800,
    ) -> str:
        """
        Vision call via OpenRouter GPT-4o.
        Gemini vision is available but GPT-4o is faster for screenshot analysis.
        Falls back to Gemini native vision if OPENROUTER_API_KEY is not set.
        """
        if settings.OPENROUTER_API_KEY:
            return await self._vision_openrouter(
                text_prompt, base64_images, system, max_tokens
            )
        return await self._vision_gemini(
            text_prompt, base64_images, system, max_tokens
        )

    async def _vision_openrouter(
        self, text_prompt, base64_images, system, max_tokens
    ) -> str:
        content: list = [{"type": "text", "text": text_prompt}]
        for b64 in base64_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"},
            })
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": content})

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model":      settings.OPENROUTER_VISION_MODEL,
                    "messages":   msgs,
                    "max_tokens": max_tokens,
                    "stream":     False,
                },
                headers={
                    "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://veronica-assistant.local",
                    "X-Title":       "Veronica AI Assistant",
                },
            )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenRouter Vision error {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"].strip()

    async def _vision_gemini(
        self, text_prompt, base64_images, system, max_tokens
    ) -> str:
        """Gemini native vision fallback (when no OpenRouter key)."""
        url = f"{GEMINI_BASE}/gemini-2.0-flash:generateContent?key={self.api_key}"
        parts: list = [{"text": text_prompt}]
        for b64 in base64_images:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        payload: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                url, json=payload,
                headers={"Content-Type": "application/json"},
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini Vision error {r.status_code}: {r.text[:300]}")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Music intent ───────────────────────────────────────────────────────────────

class MusicIntent:
    def __init__(self, is_music: bool, action: str = "play", search_query: str = ""):
        self.is_music     = is_music
        self.action       = action
        self.search_query = search_query


# ── Ollama client (local dev fallback) ────────────────────────────────────────

class OllamaClient:
    """Local Ollama - used automatically when GEMINI_API_KEY is not set."""
    def __init__(self, model: str, base_url: str):
        self.model    = model
        self.base_url = base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.model)

    async def chat(self, messages, system=None, max_tokens=400) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": msgs,
                      "stream": False, "options": {"num_predict": max_tokens}},
                headers={"Content-Type": "application/json"},
            )
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:200]}")
        return r.json()["message"]["content"].strip()

    async def chat_with_images(self, text_prompt, base64_images,
                                system=None, max_tokens=800) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": text_prompt,
                     "images": base64_images})
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{self.base_url}/api/chat",
                json={"model": self.model, "messages": msgs, "stream": False,
                      "options": {"num_predict": max_tokens}},
                headers={"Content-Type": "application/json"},
            )
        if r.status_code >= 400:
            raise RuntimeError(
                f"Ollama Vision error {r.status_code}: {r.text[:200]}")
        return r.json()["message"]["content"].strip()


# ── NLP Service ───────────────────────────────────────────────────────────────

class NLPService:
    def __init__(self):
        self._client = None
        self._conversation_history: list[dict] = []

    @property
    def client(self):
        """Auto-select Gemini (cloud) or Ollama (local dev)."""
        if self._client is None:
            if settings.GEMINI_API_KEY:
                self._client = GeminiClient(
                    api_key=settings.GEMINI_API_KEY,
                    model=settings.GEMINI_MODEL,
                )
                logger.info("LLM provider: Gemini %s", settings.GEMINI_MODEL)
            else:
                self._client = OllamaClient(
                    model=settings.OLLAMA_MODEL,
                    base_url=settings.OLLAMA_BASE_URL,
                )
                logger.info("LLM provider: Ollama %s (local)", settings.OLLAMA_MODEL)
        return self._client

    async def process_query(self, query: str, doc_context: str = "") -> dict:
        if not query.strip():
            return {"response": "I didn't catch that. Could you repeat?",
                    "is_music": False, "music_query": None, "music_action": None}

        if not self.client.is_configured():
            return {
                "response": "AI not configured. Set GEMINI_API_KEY (cloud) or start Ollama (local).",
                "is_music": False, "music_query": None, "music_action": None,
            }

        intent = self._detect_music_intent(query)
        if intent.is_music:
            return {
                "response":     self._music_confirmation(intent),
                "is_music":     True,
                "music_query":  intent.search_query or None,
                "music_action": intent.action,
            }

        response_text = await self._handle_general_query(query, doc_context)
        return {"response": response_text, "is_music": False,
                "music_query": None, "music_action": None}

    def _music_confirmation(self, intent: MusicIntent) -> str:
        return {
            "play":     f"Playing {intent.search_query} on Spotify.",
            "queue":    f"Adding {intent.search_query} to your queue.",
            "stop":     "Stopping the music.",
            "pause":    "Pausing.",
            "resume":   "Resuming playback.",
            "next":     "Skipping to the next track.",
            "previous": "Going back to the previous track.",
        }.get(intent.action, "Done.")

    def _detect_music_intent(self, query: str) -> MusicIntent:
        """Pure keyword + regex — zero LLM calls for music commands."""
        q = query.lower().strip().rstrip(".")

        if any(k in q for k in _MUSIC_STOP_KW)   or q in _MUSIC_STOP_KW_EXACT:
            return MusicIntent(True, "stop")
        if any(k in q for k in _MUSIC_PAUSE_KW)  or q in _MUSIC_PAUSE_KW_EXACT:
            return MusicIntent(True, "pause")
        if any(k in q for k in _MUSIC_RESUME_KW) or q in _MUSIC_RESUME_KW_EXACT:
            return MusicIntent(True, "resume")
        if any(k in q for k in _MUSIC_NEXT_KW)   or q in _MUSIC_NEXT_KW_EXACT:
            return MusicIntent(True, "next")
        if any(k in q for k in _MUSIC_PREV_KW)   or q in _MUSIC_PREV_KW_EXACT:
            return MusicIntent(True, "previous")

        _is_queue = (
            any(k in q for k in _MUSIC_QUEUE_KW)
            or q.startswith("queue ")
            or (q.startswith("add ") and not any(
                k in q for k in ["to queue","to my queue","to the queue","to play","to playlist"]
            ))
        )
        if _is_queue:
            cleaned = q
            cleaned = re.sub(r'^(please\s+)?(can\s+you\s+)?(add|queue\s+up|queue|put)\s+', '', cleaned)
            cleaned = re.sub(r'\s+(to|in)\s+(the\s+|my\s+)?queue\.?$', '', cleaned)
            cleaned = cleaned.strip()
            logger.info("Queue intent: '%s' → '%s'", q, cleaned)
            return MusicIntent(True, "queue", cleaned or q)

        if not any(k in q for k in _MUSIC_PLAY_KW) and not q.startswith("play "):
            return MusicIntent(False)

        cleaned = re.sub(
            r'^(please\s+)?(can\s+you\s+)?(play|listen\s+to|put\s+on)\s+',
            '', q, flags=re.IGNORECASE
        ).strip()
        logger.info("Play intent: '%s' → '%s'", q, cleaned)
        return MusicIntent(True, "play", cleaned or q)

    async def _handle_general_query(self, query: str, doc_context: str = "") -> str:
        context_blocks = []

        if doc_context.strip():
            context_blocks.append(doc_context)
        elif settings.WEB_SEARCH_ENABLED and needs_search(query):
            logger.info("Web search: '%s'", query[:60])
            results = await web_search(query, max_results=settings.WEB_SEARCH_MAX_RESULTS)
            ctx = format_search_context(results, query)
            context_blocks.append(ctx)

        memory_block = get_memory_service().as_prompt_block()
        system = f"[Today's date is {datetime.now().strftime('%B %d, %Y')}.]"
        if memory_block:
            system += "\n" + memory_block
        if context_blocks:
            system += "\n" + "\n\n".join(context_blocks)
        system += "\n\n" + SYSTEM_PROMPT

        get_memory_service().auto_extract(query)

        self._conversation_history.append({"role": "user", "content": query})
        history = self._conversation_history[-10:]

        try:
            answer = await self.client.chat(messages=history, system=system, max_tokens=300)
            self._conversation_history.append({"role": "assistant", "content": answer})
            logger.info("LLM response: '%s'", answer[:80])
            return answer
        except Exception as e:
            logger.error("LLM error: %s", e)
            return "Something went wrong. Please try again."

    def clear_history(self) -> None:
        self._conversation_history.clear()


_nlp_service = NLPService()

def get_nlp_service() -> NLPService:
    return _nlp_service
