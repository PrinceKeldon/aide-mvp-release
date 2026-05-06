"""
FinanceOS-specific Obsidian persistence.

This keeps finance reports, ingestion summaries, data snapshots, and AIDE
Finance chat transcripts in a dedicated vault allocation.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.settings import settings


class FinanceMemoryVault:
    def __init__(self, vault_path: str | Path | None = None):
        base = Path(vault_path or settings.obsidian_vault_path).expanduser()
        self.root = base / "FinanceOS"
        self.reports_dir = self.root / "Reports"
        self.ingestions_dir = self.root / "Ingestions"
        self.conversations_dir = self.root / "Conversations"
        self.data_dir = self.root / "Data"
        self.ensure()

    def ensure(self) -> None:
        for path in (
            self.reports_dir,
            self.ingestions_dir,
            self.conversations_dir,
            self.data_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def write_report(
        self,
        month_key: str,
        markdown: str,
        comparison: dict[str, Any] | None = None,
    ) -> Path:
        path = self.reports_dir / f"{self._safe_name(month_key)}.md"
        payload = {
            "type": "finance_report",
            "month_key": month_key,
            "updated_at": self._now(),
            "tags": ["financeos", "aide-finance", f"finance-{month_key}"],
        }
        path.write_text(self._frontmatter(payload) + markdown, encoding="utf-8")
        if comparison is not None:
            self.write_data_snapshot(month_key, comparison)
        return path

    def write_data_snapshot(self, month_key: str, comparison: dict[str, Any]) -> Path:
        path = self.data_dir / f"{self._safe_name(month_key)}.json"
        path.write_text(
            json.dumps(
                {
                    "type": "finance_month_snapshot",
                    "month_key": month_key,
                    "updated_at": self._now(),
                    "comparison": comparison,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return path

    def write_ingestion_summary(self, result: dict[str, Any]) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        source_name = self._safe_name(Path(result.get("source_file") or "statement").stem)
        path = self.ingestions_dir / f"{timestamp}-{source_name}.md"
        month_keys = ", ".join(result.get("month_keys") or [result.get("month_key", "")])
        totals = result.get("totals") or {}
        date_range = result.get("date_range") or {}
        lines = [
            f"# Statement Ingestion - {timestamp}",
            "",
            f"- Source file: {result.get('source_file', '')}",
            f"- Bank: {result.get('bank_name', '')}",
            f"- Months detected: {month_keys}",
            f"- Date range: {date_range.get('start', '')} to {date_range.get('end', '')}",
            f"- Parsed transactions: {result.get('parsed_count', 0)}",
            f"- Hard refresh: {bool(result.get('hard_refresh', False))}",
            f"- Prior rows deleted: {result.get('deleted_count', 0)}",
            f"- Newly inserted: {result.get('inserted_count', 0)}",
            f"- Categorised this run: {result.get('categorised_count', 0)}",
            f"- Needs review: {result.get('uncategorised_count', 0)}",
            f"- Income: EUR {float(totals.get('income', 0)):.2f}",
            f"- Spend: EUR {float(totals.get('spend', 0)):.2f}",
            f"- Net: EUR {float(totals.get('net', 0)):.2f}",
        ]
        payload = {
            "type": "finance_ingestion",
            "created_at": self._now(),
            "month_keys": result.get("month_keys") or [],
            "hard_refresh": bool(result.get("hard_refresh", False)),
            "tags": ["financeos", "finance-ingestion", "aide-finance"],
        }
        path.write_text(self._frontmatter(payload) + "\n".join(lines) + "\n", encoding="utf-8")
        return path

    def append_chat(
        self,
        *,
        month_key: str,
        user_message: str,
        assistant_reply: str,
        timestamp: str | None = None,
    ) -> Path:
        timestamp = timestamp or self._now()
        day_key = timestamp[:10]
        path = self.conversations_dir / f"{day_key}.md"
        if not path.exists():
            payload = {
                "type": "finance_chat",
                "date": day_key,
                "tags": ["financeos", "aide-finance-chat"],
            }
            path.write_text(
                self._frontmatter(payload) + f"# AIDE Finance Chat - {day_key}\n\n",
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"## {timestamp} - {month_key}\n\n")
            handle.write(f"**You:** {user_message.strip()}\n\n")
            handle.write(f"**AIDE Finance:** {assistant_reply.strip()}\n\n")
        return path

    @staticmethod
    def _frontmatter(payload: dict[str, Any]) -> str:
        return f"---\n{json.dumps(payload, indent=2, sort_keys=True)}\n---\n\n"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe_name(value: str) -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
        return value.strip("-") or "financeos"
