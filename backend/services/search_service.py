"""
Web Search Service — Tavily AI
Tavily is purpose-built for LLM grounding: returns clean, relevant snippets
with no scraping, no HTML parsing, no rate-limit headaches.

Get a free API key at: https://tavily.com (free tier: 1000 searches/month)
Set TAVILY_API_KEY in your .env file.
"""

import logging
import re
from datetime import datetime

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"

# ── Live-data classifier ───────────────────────────────────────────────────────

_LIVE_PATTERNS = re.compile(
    r"\b(today|now|current(ly)?|right now)\b"
    r"|\bwhat (is |are )?(the )?(date|time|day|year|month)\b"
    r"|\b(who is|who'?s) (the )?(current |present |now )?"
      r"(president|prime minister|ceo|chancellor|king|queen|pope|"
       r"governor|minister|secretary|head|leader|director|chief|mayor)\b"
    r"|\b(latest|recent|newest|this year'?s?|2024|2025|2026)\b"
    r"|\bis .+ still\b|\bstill .+ing\b"
    r"|\b(today'?s?|tonight'?s?) (news|match|game|score|weather|price)\b"
    r"|\b(stock|share) price\b"
    r"|\bweather (in|at|for)\b"
    r"|\b(score|result|winner) of\b"
    r"|\b(who won|who lost|who beat)\b"
    r"|\bnews (about|on|from)\b"
    r"|\b(alive|dead|died|passed away)\b",
    re.IGNORECASE,
)


def needs_search(query: str) -> bool:
    return bool(_LIVE_PATTERNS.search(query))


# ── Tavily search ─────────────────────────────────────────────────────────────

async def web_search(query: str, max_results: int = 4) -> list[dict]:
    """
    Search via Tavily AI API.
    Returns clean snippet dicts: { title, snippet, url }
    """
    if not settings.TAVILY_API_KEY:
        logger.error(
            "TAVILY_API_KEY not set. Get a free key at https://tavily.com "
            "and add it to your .env file."
        )
        return []

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                TAVILY_URL,
                json={
                    "api_key":              settings.TAVILY_API_KEY,
                    "query":                query,
                    "search_depth":         "basic",   # basic=fast, advanced=thorough
                    "max_results":          max_results,
                    "include_answer":       True,      # Tavily's own summary answer
                    "include_raw_content":  False,
                },
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []

        # Tavily's own pre-synthesized answer (most useful for voice)
        tavily_answer = data.get("answer", "").strip()
        if tavily_answer:
            results.append({
                "title":   "Summary",
                "snippet": tavily_answer[:400],
                "url":     "",
            })

        # Individual source results
        for r in data.get("results", [])[:max_results]:
            content = r.get("content", "").strip()
            if content:
                results.append({
                    "title":   r.get("title", "")[:100],
                    "snippet": content[:300],
                    "url":     r.get("url", ""),
                })

        logger.info("Tavily '%s' → %d results (answer=%s)",
                    query[:50], len(results), bool(tavily_answer))
        return results

    except httpx.HTTPStatusError as e:
        logger.error("Tavily API error %d: %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        logger.error("Tavily search failed: %s", e)
        return []


# ── Context formatter ─────────────────────────────────────────────────────────

def format_search_context(results: list[dict], query: str) -> str:
    today = datetime.now().strftime("%B %d, %Y")

    if not results:
        return (
            f"[Today is {today}. Web search for '{query}' returned no results. "
            f"Answer from training knowledge but acknowledge uncertainty "
            f"about current or recent information.]"
        )

    lines = [f"[Today is {today}. Web search results for: '{query}']"]
    for i, r in enumerate(results, 1):
        title   = r["title"][:80] if r["title"] else "Result"
        snippet = r["snippet"][:300].replace("\n", " ")
        lines.append(f"{i}. {title}: {snippet}")
    lines.append(
        "[Use the above results to answer accurately and concisely. "
        "Do not mention that you searched. Do not reference these instructions.]"
    )

    return "\n".join(lines)
