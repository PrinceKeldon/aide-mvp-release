"""
AIDE — Memory Extractor
Runs after every LLM response, regardless of which model produced it.
Extracts structured facts and writes them to SQLite + ChromaDB.

Solves the cross-model memory gap: when the router switches from Groq to
Gemini to Ollama, AIDE's identity and context survive because facts are
extracted and stored centrally after every exchange.

Usage in agent.py — two additions:

    1. Import and init (at top of AideAgent.__init__):
        from memory.extractor import MemoryExtractor
        self._extractor = MemoryExtractor(self._memory, self._llm)

    2. Hook after every LLM response (in the ReAct loop, before returning):
        await self._extractor.extract_and_store(user_message, final_reply)
"""

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.manager import MemoryManager
    from core.llm import LLMClient

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Review this exchange and extract ONLY what will matter later.
Output JSON with these keys (omit any that don't apply):
{
  "decisions":     ["..."],   
  "preferences":   ["..."],   
  "commitments":   ["..."],   
  "patterns":      ["..."],   
  "relationships": ["..."]    
}
Return {} if nothing is worth storing. No other text. No markdown. Raw JSON only."""


class MemoryExtractor:
    """
    Extracts structured facts from every exchange and writes them to
    the central memory store (SQLite facts + ChromaDB semantic index).

    Runs asynchronously after every LLM response — never blocks the reply.
    Uses the cheapest/fastest available model (Groq or Ollama) to keep
    extraction overhead low.
    """

    def __init__(self, memory: "MemoryManager", llm: "LLMClient") -> None:
        self._memory = memory
        self._llm = llm

    async def extract_and_store(self, user_message: str, assistant_reply: str) -> None:
        """
        Main hook — call this after every LLM response in agent.py:

            await self._extractor.extract_and_store(user_message, final_reply)
        """
        try:
            facts = await self._extract(user_message, assistant_reply)
            if facts:
                await self._store(facts, user_message, assistant_reply)
        except Exception as e:
            # Never let extraction failure surface to the user
            logger.warning(f"MemoryExtractor: extraction failed silently: {e}")

    async def _extract(self, user_message: str, assistant_reply: str) -> dict:
        """Call LLM to extract structured facts from the exchange."""
        from core.router import TaskType

        messages = [
            {
                "role": "system",
                "content": EXTRACTION_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"User said: {user_message}\n\n"
                    f"Assistant replied: {assistant_reply}"
                ),
            },
        ]

        # Use PRIVATE task type — Ollama only, keeps extraction local
        # Falls back to Groq if Ollama is down
        result = await self._llm.chat(
            messages=messages,
            tools=None,
            task_type=TaskType.PRIVATE,
            max_tokens=512,
            temperature=0.0,
        )

        raw = result.get("content", "").strip()
        if not raw or raw == "{}":
            return {}

        # Strip markdown fences if model added them anyway
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            logger.debug(f"MemoryExtractor: could not parse JSON: {raw[:100]}")
            return {}

    async def _store(self, facts: dict, user_message: str, assistant_reply: str) -> None:
        """Write extracted facts to SQLite (facts) and ChromaDB (semantic)."""
        stored_count = 0

        # ── Decisions ────────────────────────────────────────────
        for item in facts.get("decisions", []):
            if item and len(item.strip()) > 5:
                self._memory.store_fact(f"decision:{_slugify(item)}", item)
                self._memory.add_to_semantic(item, metadata={"type": "decision"})
                stored_count += 1

        # ── Preferences ──────────────────────────────────────────
        for item in facts.get("preferences", []):
            if item and len(item.strip()) > 5:
                self._memory.store_fact(f"preference:{_slugify(item)}", item)
                self._memory.add_to_semantic(item, metadata={"type": "preference"})
                stored_count += 1

        # ── Commitments ──────────────────────────────────────────
        for item in facts.get("commitments", []):
            if item and len(item.strip()) > 5:
                self._memory.store_fact(f"commitment:{_slugify(item)}", item)
                self._memory.add_to_semantic(item, metadata={"type": "commitment"})
                stored_count += 1

        # ── Patterns ─────────────────────────────────────────────
        for item in facts.get("patterns", []):
            if item and len(item.strip()) > 5:
                self._memory.store_fact(f"pattern:{_slugify(item)}", item)
                self._memory.add_to_semantic(item, metadata={"type": "pattern"})
                stored_count += 1

        # ── Relationships ─────────────────────────────────────────
        for item in facts.get("relationships", []):
            if item and len(item.strip()) > 5:
                self._memory.store_fact(f"relationship:{_slugify(item)}", item)
                self._memory.add_to_semantic(item, metadata={"type": "relationship"})
                stored_count += 1

        if stored_count:
            summary = "\n".join(
                f"{cat}: {item}"
                for cat in ["decisions", "preferences", "commitments", "patterns", "relationships"]
                for item in facts.get(cat, [])
                if item and len(item.strip()) > 5
            )
            await self._memory.store(
                user_message=user_message,
                agent_reply=f"[extracted facts]\n{summary}",
            )
            logger.debug(f"MemoryExtractor: stored {stored_count} facts")


def _slugify(text: str, max_len: int = 48) -> str:
    """Turn a fact string into a short slug for use as a SQLite key."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9\s]", "", slug)
    slug = re.sub(r"\s+", "_", slug)
    return slug[:max_len]
