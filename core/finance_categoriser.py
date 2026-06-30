"""
FinanceOS categorisation engine.

Financial transaction text is sensitive. Any LLM classification must be routed
through TaskType.PRIVATE, which is Ollama-only in AIDE's router.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.llm import LLMClient
from core.router import TaskType
from core.settings import settings


DEFAULT_CATEGORIES = {
    "Housing": ["rent", "miete", "vonovia", "deutsche wohnen"],
    "Groceries": ["rewe", "lidl", "aldi", "edeka", "kaufland", "penny", "netto"],
    "Transport": ["bvg", "deutsche bahn", "db ", "uber", "bolt", "aral", "shell"],
    "Utilities": ["vattenfall", "telekom", "vodafone", "o2", "strom", "internet"],
    "Health": ["apotheke", "pharmacy", "dm", "rossmann", "techniker", "gym"],
    "Dining Out": ["lieferando", "restaurant", "cafe", "bar", "mcdonald", "burger"],
    "Clothing": ["zalando", "h&m", "zara", "uniqlo", "nike", "adidas"],
    "Entertainment": ["netflix", "spotify", "amazon", "cinema", "kino", "audible"],
    "Travel": ["ryanair", "booking.com", "airbnb", "hotel", "flight"],
    "Education": ["udemy", "coursera", "apple", "book", "course"],
    "Savings": ["savings", "tagesgeld", "ing", "dkb savings"],
    "Income": ["salary", "gehalt", "lohn", "payroll", "honorar", "dividend"],
    "Bills": ["insurance", "versicherung", "gez", "huk", "abo", "subscription"],
    "Projects": ["project", "equipment", "hardware"],
}


@dataclass(frozen=True)
class CategorisationResult:
    category: str
    source: str
    confidence: float


class FinanceCategoriser:
    def __init__(self, db_path: str | Path | None = None, llm: LLMClient | None = None):
        self.db_path = str(db_path or settings.memory_db_path)
        self.llm = llm or LLMClient()
        self._init_db()
        self.seed_default_targets()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS budget_targets (
                    id TEXT PRIMARY KEY,
                    category TEXT UNIQUE NOT NULL,
                    monthly_target REAL NOT NULL DEFAULT 0,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS merchant_rules (
                    id TEXT PRIMARY KEY,
                    pattern TEXT NOT NULL,
                    category TEXT NOT NULL,
                    match_type TEXT NOT NULL DEFAULT 'keyword',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

    def seed_default_targets(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for category in [*DEFAULT_CATEGORIES.keys(), "Uncategorised"]:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO budget_targets
                    (id, category, monthly_target, notes, updated_at)
                    VALUES (?, ?, 0, ?, ?)
                    """,
                    (str(uuid.uuid4()), category, "Default FinanceOS category", now),
                )

    def list_categories(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category FROM budget_targets ORDER BY category"
            ).fetchall()
        return [row["category"] for row in rows]

    def upsert_rule(
        self,
        pattern: str,
        category: str,
        match_type: str = "keyword",
        confidence: float = 1.0,
    ) -> None:
        pattern = pattern.strip()
        if not pattern:
            raise ValueError("pattern is required")
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO merchant_rules
                (id, pattern, category, match_type, confidence, use_count, created_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    pattern,
                    category,
                    match_type,
                    float(confidence),
                    now,
                ),
            )

    def _match_stored_rule(self, description: str) -> CategorisationResult | None:
        normalised = self._normalise(description)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, pattern, category, match_type, confidence
                FROM merchant_rules
                ORDER BY confidence DESC, use_count DESC
                """
            ).fetchall()
            for row in rows:
                pattern = self._normalise(row["pattern"])
                exact = row["match_type"] == "exact" and normalised == pattern
                keyword = row["match_type"] != "exact" and pattern in normalised
                if exact or keyword:
                    conn.execute(
                        "UPDATE merchant_rules SET use_count = use_count + 1 WHERE id = ?",
                        (row["id"],),
                    )
                    return CategorisationResult(
                        category=row["category"],
                        source="rule",
                        confidence=float(row["confidence"]),
                    )
        return None

    def _match_default_keyword(self, description: str) -> CategorisationResult | None:
        normalised = self._normalise(description)
        for category, keywords in DEFAULT_CATEGORIES.items():
            if any(keyword in normalised for keyword in keywords):
                return CategorisationResult(category=category, source="keyword", confidence=0.9)
        return None

    async def categorise(
        self,
        description: str,
        amount: float | None = None,
        *,
        use_llm: bool = False,
    ) -> CategorisationResult:
        if amount is not None and amount > 0:
            keyword_result = self._match_default_keyword(description)
            if keyword_result and keyword_result.category == "Income":
                return keyword_result

        stored = self._match_stored_rule(description)
        if stored:
            return stored

        keyword = self._match_default_keyword(description)
        if keyword:
            self.upsert_rule(description[:80], keyword.category, "keyword", keyword.confidence)
            return keyword

        if use_llm:
            llm_result = await self._classify_private(description)
            if llm_result:
                self.upsert_rule(description[:80], llm_result.category, "ai", llm_result.confidence)
                return llm_result
        return CategorisationResult("Uncategorised", "fallback", 0.0)

    async def categorise_month(
        self,
        month_key: str,
        *,
        use_llm: bool = False,
        llm_limit: int = 0,
    ) -> dict[str, Any]:
        updated = 0
        uncategorised = 0
        llm_used = 0
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, description, amount FROM transactions
                WHERE month_key = ? AND (category IS NULL OR category = '' OR category = 'Uncategorised')
                ORDER BY date ASC
                """,
                (month_key,),
            ).fetchall()
        for row in rows:
            should_use_llm = use_llm and (llm_limit <= 0 or llm_used < llm_limit)
            result = await self.categorise(
                row["description"],
                row["amount"],
                use_llm=should_use_llm,
            )
            if should_use_llm:
                llm_used += 1
            with self._conn() as conn:
                conn.execute(
                    "UPDATE transactions SET category = ? WHERE id = ?",
                    (result.category, row["id"]),
                )
            updated += 1
            if result.category == "Uncategorised":
                uncategorised += 1
        return {
            "updated": updated,
            "uncategorised": uncategorised,
            "llm_used": llm_used,
        }

    async def correct_transaction_category(self, pattern: str, category: str) -> int:
        self.upsert_rule(pattern, category, "keyword", 1.0)
        like = f"%{pattern.strip()}%"
        with self._conn() as conn:
            cursor = conn.execute(
                "UPDATE transactions SET category = ? WHERE lower(description) LIKE lower(?)",
                (category, like),
            )
        return int(cursor.rowcount or 0)

    async def _classify_private(self, description: str) -> CategorisationResult | None:
        categories = ", ".join(self.list_categories())
        prompt = (
            "Classify this bank transaction into one category. "
            "Return compact JSON only with keys category and confidence. "
            f"Categories: {categories}\nTransaction: {description}"
        )
        try:
            response = await self.llm.chat(
                [{"role": "user", "content": prompt}],
                task_type=TaskType.PRIVATE,
                temperature=0,
                max_tokens=120,
                offline=True,
            )
        except Exception:
            return None
        content = str(response.get("content") or "").strip()
        match = re.search(r"\{.*\}", content, flags=re.S)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        category = str(payload.get("category") or "").strip()
        if category not in self.list_categories():
            return None
        confidence = float(payload.get("confidence") or 0.5)
        return CategorisationResult(category, "private_llm", max(0.0, min(confidence, 1.0)))

    @staticmethod
    def _normalise(value: str) -> str:
        return re.sub(r"\s+", " ", value.lower()).strip()
