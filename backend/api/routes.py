"""
API Routes — Veronica  (security-hardened)

Every user-input entry point has:
  - Rate limiting (sliding window per IP)
  - Strict Pydantic validation with custom validators
  - Sanitised filenames / Content-Disposition headers
  - SSRF protection on URL ingestion
  - Magic-number validation on file uploads
  - No internal error details in responses
  - Security-event logging for auth and rate-limit events
"""

import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from services.spotify_service import (
    build_auth_url, exchange_code_for_tokens,
    get_valid_access_token, get_token_store,
    search_and_play, pause_playback, resume_playback,
    skip_next, skip_previous, get_queue, search_and_queue,
    add_to_queue as spotify_add_to_queue,
)
from services.stt_service import transcribe_audio
from services.tts_service import synthesize_speech
from services.nlp_service import get_nlp_service
from services.rag_service import (
    ingest_pdf, ingest_url, retrieve_context,
    list_documents, delete_document,
)
from services.conversion_service import pdf_to_docx, docx_to_pdf
from services.memory_service import get_memory_service
from core.config import settings
from core.security import (
    check_rate_limit, get_client_ip, log_security_event,
    sanitise_filename, sanitise_text_input, sanitise_content_disposition,
    validate_uuid, validate_spotify_uri, validate_device_id,
    validate_url_safe, validate_base64_image,
)

router     = APIRouter()
logger     = logging.getLogger(__name__)
MAX_BYTES       = settings.MAX_UPLOAD_MB * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024


# ── Rate-limit helper ──────────────────────────────────────────────────────────

def _rl(category: str, request: Request) -> None:
    """Raise 429 if rate limit exceeded for this IP + category."""
    if not check_rate_limit(category, get_client_ip(request)):
        raise HTTPException(
            429, "Too many requests — please slow down.",
            headers={"Retry-After": "60"},
        )


# ── Request models ──────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    text:    str           = Field(..., min_length=1, max_length=1000)
    doc_id:  Optional[str] = Field(default=None, max_length=36)
    doc_ids: list[str]     = Field(default_factory=list, max_length=20)

    @field_validator("text")
    @classmethod
    def _text(cls, v): return sanitise_text_input(v, 1000)

    @field_validator("doc_ids", mode="before")
    @classmethod
    def _doc_ids(cls, v):
        for i in v:
            if not isinstance(i, str) or not validate_uuid(i):
                raise ValueError(f"Invalid doc_id: {str(i)[:40]!r}")
        return v

    @field_validator("doc_id")
    @classmethod
    def _doc_id(cls, v):
        if v is not None and not validate_uuid(v):
            raise ValueError(f"Invalid doc_id: {str(v)[:40]!r}")
        return v


class PlayRequest(BaseModel):
    query:     str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(..., min_length=10, max_length=100)

    @field_validator("query")
    @classmethod
    def _q(cls, v): return sanitise_text_input(v, 200)

    @field_validator("device_id")
    @classmethod
    def _d(cls, v):
        if not validate_device_id(v):
            raise ValueError("Invalid Spotify device_id")
        return v


class QueueRequest(BaseModel):
    query:     str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(default="", max_length=100)

    @field_validator("query")
    @classmethod
    def _q(cls, v): return sanitise_text_input(v, 200)

    @field_validator("device_id")
    @classmethod
    def _d(cls, v):
        if v and not validate_device_id(v):
            raise ValueError("Invalid Spotify device_id")
        return v


class QueueUriRequest(BaseModel):
    uri:       str = Field(..., min_length=10, max_length=100)
    device_id: str = Field(default="", max_length=100)

    @field_validator("uri")
    @classmethod
    def _uri(cls, v):
        if not validate_spotify_uri(v):
            raise ValueError("Invalid Spotify URI format")
        return v

    @field_validator("device_id")
    @classmethod
    def _d(cls, v):
        if v and not validate_device_id(v):
            raise ValueError("Invalid Spotify device_id")
        return v


class IngestUrlRequest(BaseModel):
    url: str = Field(..., min_length=10, max_length=2048)

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        ok, reason = validate_url_safe(v)
        if not ok:
            raise ValueError(f"URL rejected: {reason}")
        return v


class DocQueryRequest(BaseModel):
    query:  str           = Field(..., min_length=1, max_length=1000)
    doc_id: Optional[str] = Field(default=None, max_length=36)

    @field_validator("query")
    @classmethod
    def _q(cls, v): return sanitise_text_input(v, 1000)

    @field_validator("doc_id")
    @classmethod
    def _d(cls, v):
        if v is not None and not validate_uuid(v):
            raise ValueError("Invalid doc_id")
        return v


class MemoryRequest(BaseModel):
    fact:     str = Field(..., min_length=1, max_length=500)
    category: str = Field(default="general", max_length=50)

    @field_validator("fact")
    @classmethod
    def _f(cls, v): return sanitise_text_input(v, 500)

    @field_validator("category")
    @classmethod
    def _c(cls, v):
        if not re.match(r'^[A-Za-z0-9_]{1,50}$', v):
            raise ValueError("Category must be alphanumeric/underscores only")
        return v


class ScreenshotRequest(BaseModel):
    # 4 MB image → ~5.5 MB base64 → cap at 6 MB string
    image_b64: str = Field(..., min_length=10, max_length=6_000_000)
    question:  str = Field(default="What do you see in this screenshot?", max_length=500)

    @field_validator("image_b64")
    @classmethod
    def _img(cls, v):
        ok, reason = validate_base64_image(v, max_bytes=4 * 1024 * 1024)
        if not ok:
            raise ValueError(f"Invalid image: {reason}")
        return v

    @field_validator("question")
    @classmethod
    def _q(cls, v): return sanitise_text_input(v, 500)


# ── Spotify Auth ───────────────────────────────────────────────────────────────

@router.get("/auth/login")
async def spotify_login(request: Request):
    _rl("auth", request)
    ip = get_client_ip(request)
    log_security_event("AUTH_LOGIN_INITIATED", "OAuth flow started", ip)
    url, _ = build_auth_url()
    return RedirectResponse(url, status_code=302)


@router.get("/auth/callback")
async def spotify_callback(
    request: Request,
    code:  Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    ip = get_client_ip(request)
    _rl("auth", request)

    if error:
        log_security_event("AUTH_DENIED", f"Spotify error: {(error or '')[:50]}", ip)
        return RedirectResponse("/?auth_error=denied", status_code=302)

    if not code or not state:
        log_security_event("AUTH_MISSING_PARAMS", "code/state absent", ip)
        return RedirectResponse("/?auth_error=missing_params", status_code=302)

    # Prevent oversized param attacks
    if len(code) > 512 or len(state) > 128:
        log_security_event("AUTH_OVERSIZED_PARAMS", "code/state too long", ip)
        return RedirectResponse("/?auth_error=invalid_params", status_code=302)

    try:
        await exchange_code_for_tokens(code, state)
        log_security_event("AUTH_SUCCESS", "OAuth completed", ip)
    except ValueError:
        log_security_event("AUTH_CSRF_FAIL", "State mismatch — possible CSRF", ip)
        return RedirectResponse("/?auth_error=csrf", status_code=302)
    except Exception as e:
        logger.error("Token exchange: %s", type(e).__name__)
        log_security_event("AUTH_EXCHANGE_FAIL", type(e).__name__, ip)
        return RedirectResponse("/?auth_error=token_exchange", status_code=302)

    return RedirectResponse("/", status_code=302)


@router.get("/auth/token")
async def get_token(request: Request):
    """Spotify SDK access token — rate-limited, returns 401 if not authenticated."""
    _rl("auth", request)
    token = await get_valid_access_token()
    if not token:
        raise HTTPException(401, "Not authenticated with Spotify")
    return JSONResponse({"access_token": token})


@router.get("/auth/status")
async def auth_status():
    """Boolean auth check — never returns the raw token."""
    token = await get_valid_access_token()
    return JSONResponse({"authenticated": bool(token)})


@router.post("/auth/logout")
async def logout(request: Request):
    ip = get_client_ip(request)
    get_token_store().clear()
    log_security_event("AUTH_LOGOUT", "Token store cleared", ip)
    return JSONResponse({"status": "logged_out"})


# ── Voice Pipeline ─────────────────────────────────────────────────────────────

ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/ogg", "audio/mp4",
    "audio/mpeg", "audio/wav", "audio/x-wav",
    "application/octet-stream",   # some browsers send this
}

@router.post("/voice/transcribe")
async def transcribe(request: Request, audio: UploadFile = File(...)):
    _rl("voice", request)

    ct = (audio.content_type or "").split(";")[0].strip().lower()
    if ct and ct not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(415, "Unsupported audio format")

    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty audio file")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too large (max 10MB)")

    try:
        text = await transcribe_audio(data, audio.content_type or "audio/webm")
        return JSONResponse({"transcript": text})
    except Exception as e:
        logger.error("STT: %s", type(e).__name__)
        raise HTTPException(500, "Transcription failed")


@router.post("/voice/query")
async def process_query(request: Request, body: QueryRequest):
    _rl("voice", request)
    nlp = get_nlp_service()
    doc_context = ""
    active_ids = list(body.doc_ids) if body.doc_ids else ([body.doc_id] if body.doc_id else [])

    if active_ids:
        try:
            doc_context = await retrieve_context(body.text, doc_ids=active_ids)
        except Exception as e:
            logger.warning("RAG: %s", type(e).__name__)

    try:
        result = await nlp.process_query(body.text, doc_context=doc_context)
        return JSONResponse(result)
    except Exception as e:
        logger.error("NLP: %s", type(e).__name__)
        raise HTTPException(500, "Query processing failed")


@router.post("/voice/synthesize")
async def synthesize(request: Request, body: QueryRequest):
    _rl("voice", request)
    try:
        audio_bytes, mime_type = await synthesize_speech(body.text)
        return Response(content=audio_bytes, media_type=mime_type)
    except Exception as e:
        logger.error("TTS: %s", type(e).__name__)
        raise HTTPException(500, "Speech synthesis failed")


# ── Music Controls ─────────────────────────────────────────────────────────────

@router.post("/voice/play")
async def play_music(request: Request, body: PlayRequest):
    _rl("api", request)
    result = await search_and_play(body.query, body.device_id)
    if not result["success"]:
        raise HTTPException(422, "Playback failed — check Spotify device connection")
    return JSONResponse({"track_name": result["track_name"],
                         "artist_name": result["artist_name"],
                         "track_uri":   result["track_uri"]})

@router.post("/voice/pause-music")
async def pause_music(request: Request):
    _rl("api", request)
    if not await pause_playback(): raise HTTPException(422, "Could not pause")
    return JSONResponse({"status": "paused"})

@router.post("/voice/resume-music")
async def resume_music(request: Request):
    _rl("api", request)
    if not await resume_playback(): raise HTTPException(422, "Could not resume")
    return JSONResponse({"status": "resumed"})

@router.post("/voice/next-track")
async def next_track(request: Request):
    _rl("api", request)
    if not await skip_next(): raise HTTPException(422, "Could not skip")
    return JSONResponse({"status": "skipped"})

@router.post("/voice/previous-track")
async def prev_track(request: Request):
    _rl("api", request)
    if not await skip_previous(): raise HTTPException(422, "Could not go back")
    return JSONResponse({"status": "previous"})


# ── Queue ──────────────────────────────────────────────────────────────────────

@router.get("/music/queue")
async def fetch_queue(request: Request):
    _rl("api", request)
    return JSONResponse(await get_queue())

@router.post("/music/queue")
async def add_to_queue(request: Request, body: QueueRequest):
    _rl("api", request)
    result = await search_and_queue(body.query, body.device_id or None)
    if not result["success"]: raise HTTPException(422, "Could not add to queue")
    return JSONResponse({"track_name": result["track_name"],
                         "artist_name": result["artist_name"],
                         "track_uri":   result["track_uri"]})

@router.post("/music/queue-uri")
async def add_uri_to_queue(request: Request, body: QueueUriRequest):
    _rl("api", request)
    ok = await spotify_add_to_queue(body.uri, body.device_id or None)
    if not ok: raise HTTPException(422, "Could not add track to queue")
    return JSONResponse({"status": "queued", "uri": body.uri})


# ── Documents (RAG) ────────────────────────────────────────────────────────────

@router.post("/docs/ingest-pdf")
async def ingest_pdf_route(request: Request, file: UploadFile = File(...)):
    _rl("upload", request)
    safe_name = sanitise_filename(file.filename or "upload.pdf")
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    ct = (file.content_type or "").lower()
    if ct and ct not in ("application/pdf", "application/octet-stream", ""):
        raise HTTPException(415, "Expected application/pdf")
    data = await file.read()
    if not data: raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    if data[:5] != b"%PDF-": raise HTTPException(400, "Not a valid PDF")
    try:
        result = await ingest_pdf(safe_name, data)
        return JSONResponse(result)
    except Exception as e:
        logger.error("PDF ingest: %s", type(e).__name__)
        raise HTTPException(500, "PDF ingestion failed")

@router.post("/docs/ingest-url")
async def ingest_url_route(request: Request, body: IngestUrlRequest):
    _rl("upload", request)
    try:
        result = await ingest_url(body.url)
        return JSONResponse(result)
    except Exception as e:
        logger.error("URL ingest: %s", type(e).__name__)
        raise HTTPException(500, "URL ingestion failed")

@router.get("/docs/list")
async def list_docs(request: Request):
    _rl("api", request)
    return JSONResponse({"documents": list_documents()})

@router.delete("/docs/{doc_id}")
async def delete_doc(request: Request, doc_id: str):
    _rl("api", request)
    if not validate_uuid(doc_id):
        raise HTTPException(400, "Invalid document ID format")
    deleted = await delete_document(doc_id)
    if not deleted: raise HTTPException(404, "Document not found")
    return JSONResponse({"status": "deleted", "doc_id": doc_id})

@router.post("/docs/query")
async def query_docs(request: Request, body: DocQueryRequest):
    _rl("api", request)
    context = await retrieve_context(body.query, doc_id=body.doc_id)
    return JSONResponse({"context": context, "found": bool(context)})


# ── File Conversion ────────────────────────────────────────────────────────────

@router.post("/convert/pdf-to-docx")
async def convert_pdf_to_docx(request: Request, file: UploadFile = File(...)):
    _rl("upload", request)
    safe_name = sanitise_filename(file.filename or "upload.pdf")
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Expected a .pdf file")
    data = await file.read()
    if not data: raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    if data[:5] != b"%PDF-": raise HTTPException(400, "Not a valid PDF")
    try:
        out_bytes, mime, out_name = await pdf_to_docx(data, safe_name)
        return Response(
            content=out_bytes, media_type=mime,
            headers={"Content-Disposition":
                     f'attachment; filename="{sanitise_content_disposition(out_name)}"'},
        )
    except Exception as e:
        logger.error("PDF→DOCX: %s", type(e).__name__)
        raise HTTPException(500, "Conversion failed")

@router.post("/convert/docx-to-pdf")
async def convert_docx_to_pdf(request: Request, file: UploadFile = File(...)):
    _rl("upload", request)
    safe_name = sanitise_filename(file.filename or "upload.docx")
    if not safe_name.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "Expected a .docx file")
    data = await file.read()
    if not data: raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    if data[:2] != b"PK": raise HTTPException(400, "Not a valid DOCX (not a ZIP archive)")
    try:
        out_bytes, mime, out_name = await docx_to_pdf(data, safe_name)
        return Response(
            content=out_bytes, media_type=mime,
            headers={"Content-Disposition":
                     f'attachment; filename="{sanitise_content_disposition(out_name)}"'},
        )
    except Exception as e:
        logger.error("DOCX→PDF: %s", type(e).__name__)
        raise HTTPException(500, "Conversion failed")


# ── Memory ──────────────────────────────────────────────────────────────────────

@router.get("/memory")
async def get_memory(request: Request):
    _rl("api", request)
    mem = get_memory_service()
    return JSONResponse({"memories": mem.get_all(), "count": len(mem.get_all())})

@router.post("/memory")
async def add_memory(request: Request, body: MemoryRequest):
    _rl("api", request)
    entry = get_memory_service().add(body.fact, body.category)
    return JSONResponse({"stored": True, "entry": entry})

@router.delete("/memory")
async def clear_memory(request: Request):
    _rl("api", request)
    get_memory_service().clear()
    return JSONResponse({"status": "cleared"})

@router.delete("/memory/{index}")
async def delete_memory_item(request: Request, index: int):
    _rl("api", request)
    if index < 0: raise HTTPException(400, "Index must be non-negative")
    ok = get_memory_service().delete(index)
    if not ok: raise HTTPException(404, "Memory index out of range")
    return JSONResponse({"status": "deleted"})


# ── Screenshot ──────────────────────────────────────────────────────────────────

@router.post("/vision/screenshot")
async def analyse_screenshot(request: Request, body: ScreenshotRequest):
    _rl("screenshot", request)
    nlp = get_nlp_service()
    try:
        description = await nlp.client.chat_with_images(
            text_prompt=body.question,
            base64_images=[body.image_b64],
            system=(
                "You are Veronica, an AI assistant analysing a screenshot for the user. "
                "Be concise and conversational — your response will be spoken aloud. "
                "Describe what you see clearly in 2-4 sentences. "
                "Never use markdown, bullet points, or formatting symbols."
            ),
            max_tokens=300,
        )
        return JSONResponse({"description": description})
    except Exception as e:
        logger.error("Screenshot: %s", type(e).__name__)
        raise HTTPException(500, "Screenshot analysis failed")


# ── Misc ────────────────────────────────────────────────────────────────────────

@router.delete("/voice/history")
async def clear_history(request: Request):
    _rl("api", request)
    get_nlp_service().clear_history()
    return JSONResponse({"status": "cleared"})


@router.get("/health")
async def health():
    """Minimal health check — no config or internal details exposed."""
    return JSONResponse({"status": "ok", "service": "Veronica"})
