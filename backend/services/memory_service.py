import json
import logging
import re
import asyncio
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

import httpx
from core.config import settings

logger = logging.getLogger(__name__)

# Use a new file to avoid format conflicts with existing flat memory
_MEMORY_FILE = Path(__file__).parent.parent / "data" / "long_term_memory.json"
_lock = threading.Lock()

MAX_VALUE_LENGTH = 300

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "relationships": {},
        "notes":         {}
    }

class MemoryService:
    def __init__(self):
        self._memory = self._load()

    def _load(self) -> dict:
        if not _MEMORY_FILE.exists():
            return _empty_memory()
        with _lock:
            try:
                data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Ensure all keys exist
                    base = _empty_memory()
                    base.update(data)
                    return base
                return _empty_memory()
            except Exception as e:
                logger.error("Memory load error: %s", e)
                return _empty_memory()

    def _save(self) -> None:
        _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            try:
                _MEMORY_FILE.write_text(
                    json.dumps(self._memory, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
            except Exception as e:
                logger.error("Memory save error: %s", e)

    def _truncate_value(self, val: Any) -> str:
        s = str(val)
        if len(s) > MAX_VALUE_LENGTH:
            return s[:MAX_VALUE_LENGTH].rstrip() + "…"
        return s

    def _recursive_update(self, target: dict, updates: dict) -> bool:
        changed = False
        for key, value in updates.items():
            if value is None: continue
            if isinstance(value, str) and not value.strip(): continue

            # If it's a nested category (no "value" key)
            if isinstance(value, dict) and "value" not in value:
                if key not in target or not isinstance(target[key], dict):
                    target[key] = {}
                    changed = True
                if self._recursive_update(target[key], value):
                    changed = True
            else:
                # Leaf node: {"value": "..."} or just "..."
                if isinstance(value, dict) and "value" in value:
                    entry = {"value": self._truncate_value(value["value"])}
                else:
                    entry = {"value": self._truncate_value(value)}

                if key not in target or target[key] != entry:
                    target[key] = entry
                    changed = True
        return changed

    def update_memory(self, updates: dict) -> None:
        """Merge new facts into storage."""
        if not updates: return
        if self._recursive_update(self._memory, updates):
            self._save()
            self.update_persistent_markdown_memory(updates)
            logger.info("Memory updated: %s", list(updates.keys()))

    def update_persistent_markdown_memory(self, updates: dict) -> None:
        """Append new facts to the human-readable Memory.md file."""
        if not settings.PERSISTENT_MEMORY_FILE:
            return

        path = Path(settings.PERSISTENT_MEMORY_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # If file doesn't exist, add a header
            if not path.exists():
                path.write_text("# Veronica — Persistent Memory\n\n", encoding="utf-8")

            with path.open("a", encoding="utf-8") as f:
                f.write(f"## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                for cat, val in updates.items():
                    if isinstance(val, dict):
                        for k, v in val.items():
                            extracted_val = v.get("value") if isinstance(v, dict) else v
                            if extracted_val:
                                f.write(f"- **{cat}/{k}**: {extracted_val}\n")
                    else:
                        f.write(f"- **{cat}**: {val}\n")
                f.write("\n")
        except Exception as e:
            logger.error("Failed to update Memory.md: %s", e)

    async def extract_memory_async(self, user_text: str, assistant_text: str = "") -> None:
        """
        Two-stage LLM extraction (Mark-style).
        Filtered by a quick check to save tokens.
        """
        text = user_text.strip()
        if len(text) < 8: return

        try:
            # Stage 1: Relevance Check
            is_relevant = await self._llm_check_relevance(text)
            if not is_relevant: return

            # Stage 2: JSON Extraction
            data = await self._llm_extract_json(text)
            if data:
                self.update_memory(data)
        except Exception as e:
            logger.warning("Auto-extraction failed: %s", e)

    async def _llm_check_relevance(self, text: str) -> bool:
        prompt = (
            "Does this message contain personal facts about the user "
            "(name, age, city, job, hobby, relationship, birthday, preference)? "
            f"Reply only YES or NO.\n\nMessage: {text[:300]}"
        )
        
        if settings.GEMINI_API_KEY:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={settings.GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    res = r.json()
                    txt = res["candidates"][0]["content"]["parts"][0]["text"].upper()
                    return "YES" in txt
        else:
            # Fallback to Ollama
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False}
                )
                if r.status_code == 200:
                    txt = r.json().get("response", "").upper()
                    return "YES" in txt
        return False

    async def _llm_extract_json(self, text: str) -> dict:
        prompt = (
            "Extract personal facts from this message. Return ONLY valid JSON or {} if nothing found.\n"
            "Categories: identity (name, age, birthday, city), preferences (hobbies, music, food), relationships, notes (job, other).\n"
            "Format:\n"
            '{"identity":{"name":{"value":"..."}}, "preferences":{"hobby":{"value":"..."}}, "notes":{"job":{"value":"..."}}}\n\n'
            f"Message: {text[:500]}\n\nJSON:"
        )

        if settings.GEMINI_API_KEY:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={settings.GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"}
            }
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(url, json=payload)
                if r.status_code == 200:
                    res = r.json()
                    raw = res["candidates"][0]["content"]["parts"][0]["text"].strip()
                    # Handle potential markdown fencing
                    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
                    return json.loads(raw)
        else:
            # Fallback to Ollama
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(
                    f"{settings.OLLAMA_BASE_URL}/api/generate",
                    json={
                        "model": settings.OLLAMA_MODEL, 
                        "prompt": prompt, 
                        "stream": False,
                        "format": "json"
                    }
                )
                if r.status_code == 200:
                    raw = r.json().get("response", "").strip()
                    return json.loads(raw)
        return {}

    def get_all(self) -> list[dict]:
        """Convert nested dict to flat list for Veronica's frontend UI."""
        flat = []
        for cat, items in self._memory.items():
            for key, entry in items.items():
                # Some items might be nested deeper (recursive walk if needed, but Mark's prompt is shallow-ish)
                if isinstance(entry, dict) and "value" in entry:
                    flat.append({
                        "category": cat,
                        "fact": f"{entry['value']}",
                        "timestamp": 0 # Not tracked in Mark's format
                    })
                elif isinstance(entry, dict):
                    # One level deeper (e.g. identity -> name -> value)
                    for subkey, subentry in entry.items():
                        if isinstance(subentry, dict) and "value" in subentry:
                            flat.append({
                                "category": f"{cat}/{key}",
                                "fact": f"{subentry['value']}",
                                "timestamp": 0
                            })
        return flat

    def clear(self) -> None:
        self._memory = _empty_memory()
        self._save()
        logger.info("Memory cleared")

    def format_for_prompt(self) -> str:
        """Ported from Mark: converts nested dict to [USER MEMORY] block."""
        lines = []
        
        # Identity
        id_ = self._memory.get("identity", {})
        for k in ["name", "age", "birthday", "city"]:
            v = id_.get(k, {}).get("value")
            if v: lines.append(f"{k.capitalize()}: {v}")

        # Prefs
        prefs = self._memory.get("preferences", {})
        for k, e in list(prefs.items())[:5]:
            v = e.get("value") if isinstance(e, dict) else e
            if v: lines.append(f"{k.replace('_', ' ').title()}: {v}")

        # Relationships
        rels = self._memory.get("relationships", {})
        for k, e in list(rels.items())[:5]:
            v = e.get("value") if isinstance(e, dict) else e
            if v: lines.append(f"{k.title()}: {v}")

        # Notes
        notes = self._memory.get("notes", {})
        for k, e in list(notes.items())[:5]:
            v = e.get("value") if isinstance(e, dict) else e
            if v: lines.append(f"{k}: {v}")

        if not lines: return ""
        
        res = "[USER MEMORY]\n" + "\n".join(f"- {l}" for l in lines)
        if len(res) > 800: res = res[:797] + "…"
        return res + "\n"

_memory_service = MemoryService()

def get_memory_service() -> MemoryService:
    return _memory_service
