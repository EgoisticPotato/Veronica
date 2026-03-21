"""
Conversation Memory Service
Persists user facts across sessions in backend/data/memory.json.

Two types of memory:
  1. Explicit — user says "remember that my name is Mehul"
  2. Auto-extracted — NLP detects facts ("my name is X", "I work at Y", "I prefer Z")

Memory is injected into the system prompt so Veronica remembers the user across restarts.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MEMORY_FILE = Path(__file__).parent.parent / "data" / "memory.json"

# Patterns that signal a memorable fact
_MEMORY_PATTERNS = [
    (r"\bmy name is ([A-Za-z ]+)",              "name"),
    (r"\bi(?:'m| am) ([A-Za-z ]+)",             "identity"),
    (r"\bi work (?:at|for) ([A-Za-z0-9 ]+)",    "workplace"),
    (r"\bi (?:live|am) in ([A-Za-z ,]+)",        "location"),
    (r"\bi prefer ([A-Za-z0-9 ]+)",              "preference"),
    (r"\bi(?:'m| am) (\d+) years old",           "age"),
    (r"\bmy (?:favourite|favorite) .+ is (.+)",  "favourite"),
    (r"\bremember that (.+)",                     "explicit"),
    (r"\bmy birthday is (.+)",                    "birthday"),
]

_MAX_MEMORIES = 50   # cap so prompt doesn't balloon


class MemoryService:
    def __init__(self):
        self._memories: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            _MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            if _MEMORY_FILE.exists():
                self._memories = json.loads(_MEMORY_FILE.read_text())
                logger.info("Memory loaded: %d facts", len(self._memories))
        except Exception as e:
            logger.warning("Could not load memory: %s", e)
            self._memories = []

    def _save(self) -> None:
        try:
            _MEMORY_FILE.write_text(json.dumps(self._memories, indent=2))
        except Exception as e:
            logger.warning("Could not save memory: %s", e)

    def add(self, fact: str, category: str = "general") -> dict:
        """Explicitly store a fact."""
        # Deduplicate by category
        self._memories = [m for m in self._memories if m.get("category") != category
                          or category == "general"]
        entry = {
            "fact":      fact.strip(),
            "category":  category,
            "timestamp": time.time(),
        }
        self._memories.append(entry)
        # Keep most recent MAX_MEMORIES
        self._memories = self._memories[-_MAX_MEMORIES:]
        self._save()
        logger.info("Memory added [%s]: %s", category, fact[:60])
        return entry

    def auto_extract(self, text: str) -> list[dict]:
        """
        Scan user utterance for memorable facts and store them automatically.
        Returns list of newly stored entries.
        """
        added = []
        lower = text.lower()
        for pattern, category in _MEMORY_PATTERNS:
            m = re.search(pattern, lower)
            if m:
                fact = m.group(1).strip().rstrip(".,!?")
                if fact and len(fact) > 1:
                    entry = self.add(f"{category}: {fact}", category)
                    added.append(entry)
        return added

    def get_all(self) -> list[dict]:
        return list(self._memories)

    def clear(self) -> None:
        self._memories = []
        self._save()
        logger.info("Memory cleared")

    def delete(self, index: int) -> bool:
        if 0 <= index < len(self._memories):
            removed = self._memories.pop(index)
            self._save()
            logger.info("Memory deleted: %s", removed["fact"][:60])
            return True
        return False

    def as_prompt_block(self) -> str:
        """Format memories for injection into system prompt."""
        if not self._memories:
            return ""
        facts = [f"- {m['fact']}" for m in self._memories[-20:]]  # last 20
        return (
            "[What I know about the user:]\n"
            + "\n".join(facts)
            + "\n[Use the above to personalise responses naturally.]"
        )


_memory_service = MemoryService()

def get_memory_service() -> MemoryService:
    return _memory_service
