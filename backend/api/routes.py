"""
API Routes — Veronica
All routes mounted at /api/v1 in main.py

Auth:       /auth/*
Voice:      /voice/*
Music:      /voice/play  /voice/pause-music  etc.
Queue:      /music/queue
Documents:  /docs/*       (RAG — PDF upload + URL ingestion + query)
Convert:    /convert/*    (file format conversion)
Misc:       /health  /voice/history
"""

import logging
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import RedirectResponse, JSONResponse, Response
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional

from services.spotify_service import (
    build_auth_url, exchange_code_for_tokens,
    get_valid_access_token, get_token_store,
    search_and_play, pause_playback, resume_playback,
    skip_next, skip_previous, get_queue, search_and_queue,
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

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024


# ─── Request models ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    text:    str            = Field(..., min_length=1, max_length=1000)
    doc_id:  Optional[str]  = Field(default=None)   # legacy single-doc (kept for compat)
    doc_ids: list[str]      = Field(default_factory=list)  # active doc IDs (multi-doc)

class PlayRequest(BaseModel):
    query:     str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(..., min_length=10, max_length=100)

class QueueRequest(BaseModel):
    query:     str = Field(..., min_length=1, max_length=200)
    device_id: str = Field(default="", max_length=100)

class QueueUriRequest(BaseModel):
    uri:       str = Field(..., min_length=10, max_length=200)   # spotify:track:xxx
    device_id: str = Field(default="", max_length=100)

class IngestUrlRequest(BaseModel):
    url: str = Field(..., min_length=10, max_length=2000)

class DocQueryRequest(BaseModel):
    query:  str           = Field(..., min_length=1, max_length=1000)
    doc_id: Optional[str] = Field(default=None)

class MemoryRequest(BaseModel):
    fact:     str = Field(..., min_length=1, max_length=500)
    category: str = Field(default="general", max_length=50)

class ScreenshotRequest(BaseModel):
    image_b64: str = Field(..., min_length=10)
    question:  str = Field(default="What do you see in this screenshot?", max_length=500)

class MemoryRequest(BaseModel):
    fact:     str = Field(..., min_length=1, max_length=500)
    category: str = Field(default="general", max_length=50)

class ScreenshotRequest(BaseModel):
    image_b64: str = Field(..., min_length=10)
    question:  str = Field(default="What do you see in this screenshot?", max_length=500)


# ─── Spotify Auth ──────────────────────────────────────────────────────────────

@router.get("/auth/login")
async def spotify_login():
    url, _ = build_auth_url()
    return RedirectResponse(url, status_code=302)

@router.get("/auth/callback")
async def spotify_callback(request: Request,
                            code: str = None, state: str = None, error: str = None):
    if error:                       return RedirectResponse("/?auth_error=denied",         status_code=302)
    if not code or not state:       return RedirectResponse("/?auth_error=missing_params",  status_code=302)
    try:    await exchange_code_for_tokens(code, state)
    except ValueError:              return RedirectResponse("/?auth_error=csrf",             status_code=302)
    except Exception:               return RedirectResponse("/?auth_error=token_exchange",   status_code=302)
    return RedirectResponse("/", status_code=302)

@router.get("/auth/token")
async def get_token():
    return JSONResponse({"access_token": await get_valid_access_token()})

@router.post("/auth/logout")
async def logout():
    get_token_store().clear()
    return JSONResponse({"status": "logged_out"})


# ─── Voice Pipeline ────────────────────────────────────────────────────────────

@router.post("/voice/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    if not data:                      raise HTTPException(400, "Empty audio")
    if len(data) > 10 * 1024 * 1024: raise HTTPException(413, "Audio too large (max 10MB)")
    try:
        text = await transcribe_audio(data, audio.content_type or "audio/webm")
        return JSONResponse({"transcript": text})
    except Exception as e:
        logger.error("STT: %s", e)
        raise HTTPException(500, "Transcription failed")

@router.post("/voice/query")
async def process_query(body: QueryRequest):
    """
    NLP query, optionally grounded on a specific document (doc_id).
    If doc_id is provided, RAG context is retrieved from that document's Qdrant collection.
    If doc_id is None but documents are ingested, searches ALL collections.
    """
    nlp = get_nlp_service()
    doc_context = ""

    # Build active doc_ids list — support both legacy doc_id and new doc_ids list
    active_ids = list(body.doc_ids) if body.doc_ids else (
        [body.doc_id] if body.doc_id else []
    )

    # RAG retrieval — only when at least one doc is active
    # Empty active_ids → skip Qdrant entirely → web search / general knowledge
    if active_ids:
        try:
            doc_context = await retrieve_context(body.text, doc_ids=active_ids)
            logger.info("RAG context: %d chars for %d doc(s)",
                        len(doc_context), len(active_ids))
        except Exception as e:
            logger.warning("RAG retrieval failed: %s", e)

    try:
        result = await nlp.process_query(body.text, doc_context=doc_context)
        return JSONResponse(result)
    except Exception as e:
        logger.error("NLP: %s", e)
        raise HTTPException(500, "Query processing failed")

@router.post("/voice/synthesize")
async def synthesize(body: QueryRequest):
    try:
        audio_bytes, mime_type = await synthesize_speech(body.text)
        return Response(content=audio_bytes, media_type=mime_type)
    except Exception as e:
        logger.error("TTS: %s", e)
        raise HTTPException(500, "Speech synthesis failed")


# ─── Music Playback ────────────────────────────────────────────────────────────

@router.post("/voice/play")
async def play_music(body: PlayRequest):
    result = await search_and_play(body.query, body.device_id)
    if not result["success"]:
        raise HTTPException(422, result.get("error", "Playback failed"))
    return JSONResponse({"track_name": result["track_name"],
                         "artist_name": result["artist_name"],
                         "track_uri":   result["track_uri"]})

@router.post("/voice/pause-music")
async def pause_music():
    if not await pause_playback(): raise HTTPException(422, "Could not pause")
    return JSONResponse({"status": "paused"})

@router.post("/voice/resume-music")
async def resume_music():
    if not await resume_playback(): raise HTTPException(422, "Could not resume")
    return JSONResponse({"status": "resumed"})

@router.post("/voice/next-track")
async def next_track():
    if not await skip_next(): raise HTTPException(422, "Could not skip")
    return JSONResponse({"status": "skipped"})

@router.post("/voice/previous-track")
async def prev_track():
    if not await skip_previous(): raise HTTPException(422, "Could not go back")
    return JSONResponse({"status": "previous"})


# ─── Queue ─────────────────────────────────────────────────────────────────────

@router.get("/music/queue")
async def fetch_queue():
    return JSONResponse(await get_queue())

@router.post("/music/queue")
async def add_to_queue(body: QueueRequest):
    result = await search_and_queue(body.query, body.device_id or None)
    if not result["success"]: raise HTTPException(422, result.get("error", "Queue failed"))
    return JSONResponse({"track_name": result["track_name"],
                         "artist_name": result["artist_name"],
                         "track_uri":   result["track_uri"]})


# ─── Documents (RAG) ───────────────────────────────────────────────────────────

@router.post("/docs/ingest-pdf")
async def ingest_pdf_route(file: UploadFile = File(...)):
    """
    Upload a PDF file for RAG ingestion.
    Text pages are chunked and embedded. Image/scanned pages are described
    by the vision LLM (gpt-4o) before embedding.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files accepted")
    data = await file.read()
    if not data:              raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    try:
        result = await ingest_pdf(file.filename, data)
        return JSONResponse(result)
    except Exception as e:
        logger.error("PDF ingest: %s", e)
        raise HTTPException(500, f"PDF ingestion failed: {str(e)}")

@router.post("/docs/ingest-url")
async def ingest_url_route(body: IngestUrlRequest):
    """Scrape a URL and ingest its content for RAG."""
    try:
        result = await ingest_url(body.url)
        return JSONResponse(result)
    except Exception as e:
        logger.error("URL ingest: %s", e)
        raise HTTPException(500, f"URL ingestion failed: {str(e)}")

@router.get("/docs/list")
async def list_docs():
    """List all ingested documents."""
    return JSONResponse({"documents": list_documents()})

@router.delete("/docs/{doc_id}")
async def delete_doc(doc_id: str):
    """Delete a document and its Qdrant collection."""
    deleted = await delete_document(doc_id)
    if not deleted: raise HTTPException(404, "Document not found")
    return JSONResponse({"status": "deleted", "doc_id": doc_id})

@router.post("/docs/query")
async def query_docs(body: DocQueryRequest):
    """
    Direct document query — returns raw retrieved context without going through NLP.
    Useful for debugging RAG or building custom UI.
    """
    context = await retrieve_context(body.query, doc_id=body.doc_id)
    return JSONResponse({"context": context, "found": bool(context)})


# ─── File Conversion ───────────────────────────────────────────────────────────

@router.post("/convert/pdf-to-docx")
async def convert_pdf_to_docx(file: UploadFile = File(...)):
    """Convert PDF to DOCX. Returns the DOCX file as a download."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Expected a .pdf file")
    data = await file.read()
    if not data:              raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    try:
        out_bytes, mime, out_name = await pdf_to_docx(data, file.filename)
        return Response(
            content=out_bytes,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )
    except Exception as e:
        logger.error("PDF→DOCX: %s", e)
        raise HTTPException(500, f"Conversion failed: {str(e)}")

@router.post("/convert/docx-to-pdf")
async def convert_docx_to_pdf(file: UploadFile = File(...)):
    """Convert DOCX to PDF. Returns the PDF file as a download."""
    if not file.filename.lower().endswith((".docx", ".doc")):
        raise HTTPException(400, "Expected a .docx file")
    data = await file.read()
    if not data:              raise HTTPException(400, "Empty file")
    if len(data) > MAX_BYTES: raise HTTPException(413, f"File too large (max {settings.MAX_UPLOAD_MB}MB)")
    try:
        out_bytes, mime, out_name = await docx_to_pdf(data, file.filename)
        return Response(
            content=out_bytes,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        )
    except Exception as e:
        logger.error("DOCX→PDF: %s", e)
        raise HTTPException(500, f"Conversion failed: {str(e)}")


# ─── Misc ──────────────────────────────────────────────────────────────────────

@router.post("/music/queue-uri")
async def add_uri_to_queue(body: QueueUriRequest):
    """Add a track directly by URI to the queue (used for drag-to-reorder re-queuing)."""
    ok = await add_to_queue(body.uri, body.device_id or None)
    if not ok:
        raise HTTPException(status_code=422, detail="Could not add track to queue")
    return JSONResponse({"status": "queued", "uri": body.uri})


@router.delete("/voice/history")
async def clear_history():
    get_nlp_service().clear_history()
    return JSONResponse({"status": "cleared"})



# ─── Memory ────────────────────────────────────────────────────────────────────

@router.get("/memory")
async def get_memory():
    mem = get_memory_service()
    return JSONResponse({"memories": mem.get_all(), "count": len(mem.get_all())})

@router.post("/memory")
async def add_memory(body: MemoryRequest):
    entry = get_memory_service().add(body.fact, body.category)
    return JSONResponse({"stored": True, "entry": entry})

@router.delete("/memory")
async def clear_memory():
    get_memory_service().clear()
    return JSONResponse({"status": "cleared"})

@router.delete("/memory/{index}")
async def delete_memory_item(index: int):
    ok = get_memory_service().delete(index)
    if not ok:
        raise HTTPException(404, "Memory index out of range")
    return JSONResponse({"status": "deleted"})


# ─── Screenshot Analysis ───────────────────────────────────────────────────────

@router.post("/vision/screenshot")
async def analyse_screenshot(body: ScreenshotRequest):
    nlp = get_nlp_service()
    try:
        description = await nlp.vision_client.chat_with_images(
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
        logger.error("Screenshot analysis: %s", e)
        raise HTTPException(500, f"Screenshot analysis failed: {str(e)}")


# ─── Lyrics ────────────────────────────────────────────────────────────────────

@router.get("/music/lyrics")
async def get_lyrics(track: str, artist: str = ""):
    from services.search_service import web_search
    query = f"{track} {artist} lyrics".strip()
    try:
        results = await web_search(query, max_results=3)
        lyrics_text = ""
        for r in results:
            snippet = r.get("snippet", "")
            if len(snippet) > 200:
                lyrics_text = snippet
                break
        if not lyrics_text and results:
            lyrics_text = results[0].get("snippet", "")
        return JSONResponse({
            "track":  track,
            "artist": artist,
            "lyrics": lyrics_text,
            "source": results[0].get("url", "") if results else "",
        })
    except Exception as e:
        logger.error("Lyrics: %s", e)
        raise HTTPException(500, "Could not fetch lyrics")

@router.get("/health")
async def health():
    return JSONResponse({
        "status":    "ok",
        "service":   "Veronica",
        "docs":      len(list_documents()),
        "llm":       settings.OPENROUTER_MODEL,
    })
