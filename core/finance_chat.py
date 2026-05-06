"""
Dedicated AIDE Finance chat for FinanceOS.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.finance_memory import FinanceMemoryVault
from core.finance_report import FinanceReportGenerator
from core.llm import LLMClient
from core.router import TaskType
from core.settings import settings


SYSTEM_PROMPT = """
You are AIDE Finance inside AIDE FinanceOS.
Operate as a private, local finance analyst for the owner.

Rules:
- Treat all bank data as private. Use local/offline reasoning only.
- Use only the FinanceOS context supplied in this request. Do not invent transactions,
  dates, categories, income, spending, or balances.
- Check month boundaries carefully. A statement can span multiple months; every
  conclusion must refer to the active month_key and date range.
- Use exact arithmetic from the provided totals. If data is missing or uncategorised,
  state that clearly before recommending action.
- Give concise, decision-ready answers with EUR amounts to two decimal places when
  discussing money.
""".strip()


class FinanceChatService:
    def __init__(
        self,
        db_path: str | Path | None = None,
        llm: LLMClient | None = None,
        vault: FinanceMemoryVault | None = None,
    ):
        self.db_path = str(db_path or settings.memory_db_path)
        self.llm = llm or LLMClient()
        self.vault = vault or FinanceMemoryVault()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_chat_messages (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL
                )
                """
            )

    async def ask(self, message: str, month_key: str | None = None) -> dict[str, Any]:
        message = message.strip()
        if not message:
            raise ValueError("Finance chat message is required.")
        active_month = month_key or self._latest_month_key()
        if not active_month:
            raise ValueError("Ingest a statement before using AIDE Finance chat.")

        context = self._context(active_month)
        timestamp = datetime.now(timezone.utc).isoformat()
        self._store_message(active_month, "user", message, timestamp)

        try:
            response = await self.llm.chat(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Active FinanceOS context:\n{json.dumps(context, indent=2, sort_keys=True)}\n\n"
                            f"Owner question: {message}"
                        ),
                    },
                ],
                task_type=TaskType.PRIVATE,
                temperature=0,
                max_tokens=700,
                offline=True,
            )
            reply = str(response.get("content") or "").strip()
        except Exception as exc:
            reply = self._fallback_reply(active_month, context, str(exc))

        if not reply:
            reply = self._fallback_reply(active_month, context, "empty model response")
        self._store_message(active_month, "assistant", reply, datetime.now(timezone.utc).isoformat())
        self.vault.append_chat(
            month_key=active_month,
            user_message=message,
            assistant_reply=reply,
            timestamp=timestamp,
        )
        return {
            "month_key": active_month,
            "reply": reply,
            "context": context,
        }

    def history(self, month_key: str | None = None, limit: int = 40) -> list[dict[str, Any]]:
        params: tuple[Any, ...]
        query = """
            SELECT timestamp, month_key, role, content
            FROM finance_chat_messages
        """
        if month_key:
            query += " WHERE month_key = ?"
            params = (month_key,)
        else:
            params = ()
        query += " ORDER BY timestamp DESC LIMIT ?"
        params = (*params, int(limit))
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in reversed(rows)]

    def _context(self, month_key: str) -> dict[str, Any]:
        report = FinanceReportGenerator(self.db_path).monthly_report(month_key, save=False)
        transactions = self._sample_transactions(month_key)
        comparison = report["comparison"]
        return {
            "month_key": month_key,
            "date_range": comparison["date_range"],
            "transaction_count": comparison["transaction_count"],
            "totals": {
                "income": comparison["total_income"],
                "spend": comparison["total_spend"],
                "net": comparison["net_position"],
                "savings_rate": comparison["savings_rate"],
            },
            "uncategorised": {
                "count": comparison["uncategorised_count"],
                "total": comparison["uncategorised_total"],
            },
            "category_variances": comparison["category_variances"],
            "top_overspend": comparison["top_overspend"],
            "top_underspend": comparison["top_underspend"],
            "sample_transactions": transactions,
        }

    def _sample_transactions(self, month_key: str, limit: int = 24) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT date, description, amount, currency, category
                FROM transactions
                WHERE month_key = ?
                ORDER BY date ASC, created_at ASC
                LIMIT ?
                """,
                (month_key, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _latest_month_key(self) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT month_key FROM transactions GROUP BY month_key ORDER BY month_key DESC LIMIT 1"
            ).fetchone()
        return row["month_key"] if row else None

    def _store_message(self, month_key: str, role: str, content: str, timestamp: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO finance_chat_messages (id, timestamp, month_key, role, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), timestamp, month_key, role, content),
            )

    @staticmethod
    def _fallback_reply(month_key: str, context: dict[str, Any], reason: str) -> str:
        totals = context["totals"]
        date_range = context["date_range"]
        uncategorised = context["uncategorised"]
        return (
            f"For {month_key} ({date_range.get('start') or 'n/a'} to {date_range.get('end') or 'n/a'}), "
            f"FinanceOS has checked {context['transaction_count']} transactions: "
            f"income EUR {totals['income']:.2f}, spend EUR {totals['spend']:.2f}, "
            f"net EUR {totals['net']:.2f}, savings rate {totals['savings_rate']:.1f}%. "
            f"{uncategorised['count']} transactions totalling EUR {uncategorised['total']:.2f} still need review. "
            f"I could not complete the local model response ({reason}), so this is a deterministic FinanceOS summary."
        )
