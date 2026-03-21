"""
RAG Service — rebuilt from reference implementation (RAG_DEMO.ipynb)

Key design decisions matching the reference:
  - One Qdrant collection per ingested document (named "vc_{doc_id[:8]}")
  - sentence-transformers all-MiniLM-L6-v2 for embeddings (384-dim, local)
  - query_points() API (qdrant-client >= 1.7) — no deprecated search()
  - Score threshold: 0.30 (same as reference)
  - Deduplication by content prefix (same as reference)
  - ONE combined context block → ONE LLM call (same as reference)
  - Registry persisted to backend/data/doc_registry.json (survives restarts)

Query routing:
  - doc_ids provided (active docs): search ONLY those collections
    → If results found: return combined context
    → If no results above threshold: return explicit "not found in doc" message
  - doc_ids empty/None (all docs inactive): skip Qdrant, use web search
"""

import asyncio
import base64
import io
import json as _json
import logging
import pathlib as _pathlib
import re
import time
import uuid
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from core.config import settings

logger = logging.getLogger(__name__)

# ── Registry persistence ───────────────────────────────────────────────────────
_REGISTRY_FILE = _pathlib.Path(__file__).parent.parent / "data" / "doc_registry.json"


def _load_registry() -> dict:
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _REGISTRY_FILE.exists():
            return _json.loads(_REGISTRY_FILE.read_text())
    except Exception as e:
        logger.warning("Could not load registry: %s", e)
    return {}


def _save_registry() -> None:
    try:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REGISTRY_FILE.write_text(_json.dumps(_registry, indent=2))
    except Exception as e:
        logger.warning("Could not save registry: %s", e)


_registry: dict[str, dict] = _load_registry()

# ── Lazy globals ───────────────────────────────────────────────────────────────
_embedding_model = None
_qdrant_client   = None

SCORE_THRESHOLD = 0.30   # same as reference implementation


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading embedding model: %s", settings.EMBEDDING_MODEL)
        _embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        logger.info("Embedding model loaded (dim=%d)", settings.EMBEDDING_DIMENSION)
    return _embedding_model


def _get_qdrant():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import AsyncQdrantClient
        kwargs = {"url": settings.QDRANT_URL}
        if settings.QDRANT_API_KEY:
            kwargs["api_key"] = settings.QDRANT_API_KEY
        _qdrant_client = AsyncQdrantClient(**kwargs)
        logger.info("Qdrant client initialised: %s", settings.QDRANT_URL)
    return _qdrant_client


# ── Text chunking ──────────────────────────────────────────────────────────────

def _chunk_text(text: str) -> list[str]:
    """
    RecursiveCharacterTextSplitter logic — matches reference implementation.
    chunk_size=1000, chunk_overlap=200
    """
    size       = settings.RAG_CHUNK_SIZE
    overlap    = settings.RAG_CHUNK_OVERLAP
    separators = ["\n\n", "\n", ". ", " ", ""]

    def split_by_sep(t: str, sep: str) -> list[str]:
        return list(t) if sep == "" else t.split(sep)

    def merge_splits(splits: list[str], sep: str) -> list[str]:
        chunks, current, cur_len = [], [], 0
        for s in splits:
            s_len = len(s)
            if current and cur_len + s_len + len(sep) > size:
                chunk = sep.join(current).strip()
                if chunk:
                    chunks.append(chunk)
                while current and cur_len > overlap:
                    removed = current.pop(0)
                    cur_len -= len(removed) + len(sep)
            current.append(s)
            cur_len += s_len + len(sep)
        if current:
            chunk = sep.join(current).strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def recursive_split(t: str, seps: list[str]) -> list[str]:
        sep  = seps[0] if seps else ""
        rest = seps[1:] if seps else []
        good = []
        for s in split_by_sep(t, sep):
            if len(s) <= size:
                good.append(s)
            elif rest:
                good.extend(recursive_split(s, rest))
            else:
                for i in range(0, len(s), size - overlap):
                    good.append(s[i:i+size])
        return merge_splits(good, sep if sep else "")

    return [c for c in recursive_split(text, separators) if len(c.strip()) >= 50]


# ── PDF extraction ─────────────────────────────────────────────────────────────

async def _extract_pdf(pdf_bytes: bytes) -> list[dict]:
    """
    Extract text from PDF pages.
    Returns list of { type: "text", content: str, page: int }.
    Image-heavy pages are included only if pymupdf is available.
    """
    import pypdf

    try:
        import fitz as _fitz
        _fitz_ok = True
    except ImportError:
        _fitz_ok = False
        logger.info("pymupdf not installed — scanned pages skipped")

    reader   = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fitz_doc = _fitz.open(stream=pdf_bytes, filetype="pdf") if _fitz_ok else None
    pages    = []

    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) >= 50:
            pages.append({"type": "text", "content": text, "page": i + 1})
        elif _fitz_ok and fitz_doc:
            fp        = fitz_doc[i]
            mat       = _fitz.Matrix(2.0, 2.0)
            pix       = fp.get_pixmap(matrix=mat, alpha=False)
            b64       = base64.b64encode(pix.tobytes("png")).decode()
            pages.append({"type": "image", "content": b64, "page": i + 1})
        else:
            logger.warning("Page %d: no text, pymupdf unavailable — skipped", i + 1)

    if fitz_doc:
        fitz_doc.close()

    logger.info("PDF: %d pages (%d text, %d image)",
                len(pages),
                sum(1 for p in pages if p["type"] == "text"),
                sum(1 for p in pages if p["type"] == "image"))
    return pages


# ── URL scraping ───────────────────────────────────────────────────────────────

async def _scrape_url(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        html = r.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())

    if len(text) < 100:
        raise ValueError(f"Insufficient content at {url} ({len(text)} chars)")

    logger.info("Scraped %s — %d chars", url[:80], len(text))
    return text


# ── Qdrant collection management ──────────────────────────────────────────────

async def _ensure_collection(collection_name: str) -> None:
    """Create collection if it does not exist. Never recreates (preserves data)."""
    from qdrant_client.models import VectorParams, Distance
    qdrant = _get_qdrant()
    try:
        await qdrant.get_collection(collection_name)
        logger.info("Collection exists, reusing: %s", collection_name)
    except Exception:
        await qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=settings.EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )
        logger.info("Collection created: %s", collection_name)


async def _upsert_chunks(collection_name: str, chunks: list[str], metadata: dict) -> int:
    """Embed and upsert chunks in batches of 100. Returns total upserted."""
    from qdrant_client.models import PointStruct

    model  = _get_embedding_model()
    qdrant = _get_qdrant()
    BATCH  = 100
    total  = 0

    for i in range(0, len(chunks), BATCH):
        batch = chunks[i:i+BATCH]
        loop  = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda b=batch: model.encode(b, convert_to_numpy=True, show_progress_bar=False)
        )
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=embeddings[j].tolist(),
                payload={
                    "page_content": chunk,   # matches reference key name
                    "text":         chunk,   # keep for backward compat
                    "source":       metadata.get("source", ""),
                    "doc_id":       metadata.get("doc_id", ""),
                },
            )
            for j, chunk in enumerate(batch)
        ]
        await qdrant.upsert(collection_name=collection_name, points=points)
        total += len(points)
        logger.info("Upserted %d/%d chunks → %s", total, len(chunks), collection_name)

    return total


# ── Qdrant search (using query_points — qdrant-client >= 1.7) ─────────────────

async def _qdrant_search(collection_name: str, query_vector: list[float],
                          limit: int = 5) -> list:
    """
    Search using query_points() (qdrant-client >= 1.7).
    Returns list of ScoredPoint objects.
    Falls back to search() for older clients.
    """
    qdrant = _get_qdrant()
    try:
        response = await qdrant.query_points(
            collection_name=collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
        )
        points = getattr(response, "points", None)
        if points is None:
            return []
        return list(points)
    except Exception as e:
        # Fallback to older search() API
        logger.debug("query_points failed (%s), falling back to search()", e)
        try:
            hits = await qdrant.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
            )
            return hits
        except Exception as e2:
            logger.warning("Qdrant search failed for %s: %s", collection_name, e2)
            return []


# ── Public API ─────────────────────────────────────────────────────────────────

async def ingest_pdf(filename: str, pdf_bytes: bytes) -> dict:
    """Ingest a PDF into its own Qdrant collection."""
    from services.nlp_service import get_nlp_service

    doc_id          = str(uuid.uuid4())
    collection_name = f"vc_{doc_id[:8]}"

    await _ensure_collection(collection_name)

    pages       = await _extract_pdf(pdf_bytes)
    all_chunks  = []
    image_count = 0

    for page in pages:
        if page["type"] == "text":
            for chunk in _chunk_text(page["content"]):
                all_chunks.append({"text": chunk, "page": page["page"]})
        else:
            image_count += 1
            try:
                desc = await get_nlp_service().client.chat_with_images(
                    text_prompt=(
                        "Extract all text and describe all visual content from this PDF page. "
                        "Be comprehensive — include tables, diagrams, charts, and any visible text. "
                        "Format as plain text, no markdown."
                    ),
                    base64_images=[page["content"]],
                    system="You are a document analysis assistant.",
                    max_tokens=1000,
                )
                for chunk in _chunk_text(desc):
                    all_chunks.append({"text": chunk, "page": page["page"]})
            except Exception as e:
                logger.warning("Vision LLM failed page %d: %s", page["page"], e)

    if not all_chunks:
        raise ValueError("No extractable content found in PDF")

    chunk_count = await _upsert_chunks(
        collection_name,
        [c["text"] for c in all_chunks],
        {"source": filename, "doc_id": doc_id},
    )

    _registry[doc_id] = {
        "filename":        filename,
        "source_type":     "pdf",
        "collection_name": collection_name,
        "chunk_count":     chunk_count,
        "image_pages":     image_count,
        "created_at":      time.time(),
    }
    _save_registry()

    logger.info("PDF ingested: %s → %d chunks, %d image pages",
                filename, chunk_count, image_count)
    return {"doc_id": doc_id, "filename": filename,
            "chunk_count": chunk_count, "image_pages": image_count}


async def ingest_url(url: str) -> dict:
    """Scrape a URL and ingest its content."""
    doc_id          = str(uuid.uuid4())
    collection_name = f"vc_{doc_id[:8]}"

    await _ensure_collection(collection_name)
    text   = await _scrape_url(url)
    chunks = _chunk_text(text)

    if not chunks:
        raise ValueError(f"No usable content from {url}")

    chunk_count = await _upsert_chunks(
        collection_name, chunks,
        {"source": url, "doc_id": doc_id, "source_type": "url"},
    )

    _registry[doc_id] = {
        "filename":        url,
        "source_type":     "url",
        "collection_name": collection_name,
        "chunk_count":     chunk_count,
        "image_pages":     0,
        "created_at":      time.time(),
    }
    _save_registry()

    logger.info("URL ingested: %s → %d chunks", url[:80], chunk_count)
    return {"doc_id": doc_id, "url": url, "chunk_count": chunk_count}


async def retrieve_context(query: str,
                            doc_ids: Optional[list[str]] = None) -> str:
    """
    Retrieve relevant chunks for a query from active document collections.

    doc_ids: list of active doc IDs to search (None / [] = no active docs)

    Returns:
      - Non-empty string with context if relevant chunks found above threshold
      - Empty string if no active docs (caller should use web search)
      - Special "not found" string if docs active but nothing relevant found
        (caller should NOT fall back to web search — tell user doc lacks that info)
    """
    # No active docs → signal caller to use web search
    if not doc_ids:
        logger.info("RAG: no active docs — web search path")
        return ""

    # Filter to known doc_ids
    known = [d for d in doc_ids if d in _registry]
    if not known:
        logger.warning("RAG: none of %s found in registry (known: %s)",
                       doc_ids, list(_registry.keys()))
        return ""

    collections = [_registry[d]["collection_name"] for d in known]
    filenames   = [_registry[d]["filename"] for d in known]
    logger.info("RAG search in %d collection(s): %s", len(collections),
                [c for c in collections])

    # Embed query (CPU-bound — run in executor)
    model = _get_embedding_model()
    loop  = asyncio.get_event_loop()
    query_vec = await loop.run_in_executor(
        None,
        lambda: model.encode([query], convert_to_numpy=True,
                              show_progress_bar=False)[0].tolist()
    )

    # Search each active collection
    raw_results = []
    for col in collections:
        hits = await _qdrant_search(col, query_vec, limit=settings.RAG_TOP_K)
        raw_results.extend(hits)

    logger.info("RAG: %d raw hits across %d collection(s)",
                len(raw_results), len(collections))

    # Apply score threshold (0.30 — same as reference)
    threshold = SCORE_THRESHOLD
    results   = [r for r in raw_results if r.score >= threshold]
    logger.info("RAG: %d hits above threshold %.2f", len(results), threshold)

    # Docs were active but nothing passed threshold
    if not results:
        doc_names = ", ".join(f['filename'][:40] for f in
                              [_registry[d] for d in known])
        return (
            f"[The active document(s) ({doc_names}) were searched but no relevant "
            f"content was found for this query. "
            f"Answer only from the document — do NOT use general knowledge. "
            f"Tell the user the document does not contain this information.]"
        )

    # Sort by score, deduplicate by content prefix (same as reference)
    results.sort(key=lambda r: r.score, reverse=True)
    seen, contexts = set(), []

    for r in results[:settings.RAG_TOP_K]:
        # Support both "page_content" (new) and "text" (old) payload keys
        content = (r.payload.get("page_content") or r.payload.get("text") or "").strip()
        source  = r.payload.get("source", "")
        key     = content[:120]
        if content and key not in seen:
            seen.add(key)
            contexts.append(f"[Source: {source} | score={r.score:.3f}]\n{content}")
            logger.debug("RAG hit: score=%.3f source=%s", r.score, source[:60])

    if not contexts:
        return ""

    combined = "\n\n---\n\n".join(contexts)
    return (
        f"[Document context for query: '{query}']\n"
        f"{combined}\n"
        f"[Use the above document excerpts to answer. "
        f"If nothing is relevant to the question, say the document does not contain that information. "
        f"Do NOT answer from general knowledge when a document is active.]"
    )


def list_documents() -> list[dict]:
    return [
        {
            "doc_id":      doc_id,
            "filename":    info["filename"],
            "source_type": info["source_type"],
            "chunk_count": info["chunk_count"],
            "image_pages": info.get("image_pages", 0),
            "created_at":  info["created_at"],
        }
        for doc_id, info in _registry.items()
    ]


async def delete_document(doc_id: str) -> bool:
    if doc_id not in _registry:
        return False
    col = _registry[doc_id]["collection_name"]
    try:
        await _get_qdrant().delete_collection(col)
        logger.info("Deleted collection: %s", col)
    except Exception as e:
        logger.warning("Could not delete collection %s: %s", col, e)
    del _registry[doc_id]
    _save_registry()
    return True
