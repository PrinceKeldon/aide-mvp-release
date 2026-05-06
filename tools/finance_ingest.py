"""
FinanceOS PDF ingestion tool.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from core.finance_categoriser import FinanceCategoriser
from core.finance_memory import FinanceMemoryVault
from core.settings import settings
from tools.base import BaseTool, SafetyTier


@dataclass
class ParsedTransaction:
    date: str
    description: str
    amount: float
    currency: str = "EUR"


class FinanceIngestor:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or settings.memory_db_path)
        self.categoriser = FinanceCategoriser(self.db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'EUR',
                    category TEXT,
                    source_file TEXT,
                    month_key TEXT NOT NULL,
                    hash TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(conn, "transactions", "statement_hash", "TEXT")
            self._ensure_column(conn, "transactions", "original_filename", "TEXT")

    async def ingest_pdf(
        self,
        file_path: str | Path,
        bank_name: str = "generic",
        *,
        hard_refresh: bool = False,
        original_filename: str | None = None,
        rewrite_reports: bool = True,
    ) -> dict[str, Any]:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Bank statement not found: {path}")
        text = self._extract_text(path)
        statement_hash = self._statement_hash(text, bank_name)
        transactions = self.parse_text(text, bank_name=bank_name)
        self._validate_transactions(transactions)
        month_keys = self._month_keys(transactions)
        month_key = month_keys[-1]
        original_name = original_filename or path.name
        deleted = 0
        if hard_refresh:
            deleted = self._delete_existing_statement(
                statement_hash,
                source_file=str(path),
                original_filename=original_name,
            )
        inserted = self._store_transactions(
            transactions,
            month_key,
            str(path),
            statement_hash=statement_hash,
            original_filename=original_name,
        )
        categorised_count = 0
        uncategorised_count = 0
        categorisation_by_month = {}
        for key in month_keys:
            categorisation = await self.categoriser.categorise_month(
                key,
                use_llm=False,
            )
            categorised = categorisation["updated"] - categorisation["uncategorised"]
            categorised_count += categorised
            uncategorised_count += categorisation["uncategorised"]
            categorisation_by_month[key] = {
                **categorisation,
                "categorised": categorised,
            }
        reports_rewritten = []
        if rewrite_reports:
            from core.finance_report import FinanceReportGenerator

            reporter = FinanceReportGenerator(self.db_path)
            for key in month_keys:
                reports_rewritten.append(
                    reporter.monthly_report(key, save=True).get("saved_file", "")
                )
        result = {
            "source_file": str(path),
            "original_filename": original_name,
            "bank_name": bank_name,
            "statement_hash": statement_hash,
            "hard_refresh": hard_refresh,
            "month_key": month_key,
            "month_keys": month_keys,
            "date_range": self._date_range(transactions),
            "totals": self._totals(transactions),
            "parsed_count": len(transactions),
            "deleted_count": deleted,
            "inserted_count": inserted,
            "categorised_count": categorised_count,
            "uncategorised_count": uncategorised_count,
            "categorisation_by_month": categorisation_by_month,
            "reports_rewritten": [path for path in reports_rewritten if path],
        }
        result["obsidian_file"] = str(FinanceMemoryVault().write_ingestion_summary(result))
        return result

    def _extract_text(self, pdf_path: Path) -> str:
        try:
            import pdfplumber
        except ImportError as exc:
            raise RuntimeError(
                "pdfplumber is required for FinanceOS PDF ingestion. Install requirements first."
            ) from exc
        pages = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text() or "")
        return "\n".join(pages)

    def parse_text(self, text: str, bank_name: str = "generic") -> list[ParsedTransaction]:
        bank = bank_name.lower().strip()
        if "deutsche" in bank:
            return self._parse_deutsche_bank(text)
        if "n26" in bank:
            return self._parse_n26(text)
        return self._parse_generic(text)

    def _parse_deutsche_bank(self, text: str) -> list[ParsedTransaction]:
        return self._parse_generic(text)

    def _parse_n26(self, text: str) -> list[ParsedTransaction]:
        sample = text.strip()
        if "," in sample and "Date" in sample[:200]:
            rows = csv.DictReader(sample.splitlines())
            parsed = []
            for row in rows:
                date = row.get("Date") or row.get("Booking Date") or row.get("Value Date")
                amount = row.get("Amount") or row.get("amount")
                description = row.get("Payee") or row.get("Partner Name") or row.get("Description")
                if date and amount and description:
                    parsed.append(
                        ParsedTransaction(
                            date=self._normalise_date(date),
                            description=description.strip(),
                            amount=self._parse_amount(amount),
                            currency=row.get("Currency") or "EUR",
                        )
                    )
            if parsed:
                return parsed
        return self._parse_generic(text)

    def _parse_generic(self, text: str) -> list[ParsedTransaction]:
        transactions = []
        date_pattern = r"(?P<date>\d{1,2}[./-]\d{1,2}[./-](?:\d{2}|\d{4})|\d{4}-\d{2}-\d{2})"
        amount_pattern = r"(?P<amount>[+-]?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|[+-]?\d+\.\d{2})"
        pattern = re.compile(rf"{date_pattern}\s+(?P<body>.+?)\s+{amount_pattern}(?:\s*(?P<currency>EUR|€))?$")
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            match = pattern.search(line)
            if not match:
                continue
            body = match.group("body").strip()
            if len(body) < 2:
                continue
            transactions.append(
                ParsedTransaction(
                    date=self._normalise_date(match.group("date")),
                    description=body,
                    amount=self._parse_amount(match.group("amount")),
                    currency="EUR",
                )
            )
        return transactions

    def _store_transactions(
        self,
        transactions: list[ParsedTransaction],
        month_key: str,
        source_file: str,
        *,
        statement_hash: str = "",
        original_filename: str = "",
    ) -> int:
        inserted = 0
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            for tx in transactions:
                tx_hash = self._hash(tx)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO transactions
                    (id, date, description, amount, currency, category, source_file, month_key, hash, created_at, statement_hash, original_filename)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tx.date,
                        tx.description,
                        tx.amount,
                        tx.currency,
                        None,
                        source_file,
                        tx.date[:7],
                        tx_hash,
                        now,
                        statement_hash,
                        original_filename,
                    ),
                )
                inserted += int(cursor.rowcount or 0)
        return inserted

    def _delete_existing_statement(
        self,
        statement_hash: str,
        *,
        source_file: str,
        original_filename: str,
    ) -> int:
        original_filename = Path(original_filename).name
        source_like = f"%{original_filename.lower()}%" if original_filename else ""
        with self._conn() as conn:
            cursor = conn.execute(
                """
                DELETE FROM transactions
                WHERE statement_hash = ?
                   OR source_file = ?
                   OR (? != '' AND lower(source_file) LIKE ?)
                   OR (? != '' AND lower(original_filename) = lower(?))
                """,
                (
                    statement_hash,
                    source_file,
                    source_like,
                    source_like,
                    original_filename,
                    original_filename,
                ),
            )
        return int(cursor.rowcount or 0)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _hash(tx: ParsedTransaction) -> str:
        raw = f"{tx.date}|{tx.description.lower().strip()}|{tx.amount:.2f}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _statement_hash(text: str, bank_name: str) -> str:
        normalised = re.sub(r"\s+", " ", text).strip().lower()
        raw = f"{bank_name.lower().strip()}|{normalised}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _month_key(transactions: list[ParsedTransaction]) -> str:
        if not transactions:
            return datetime.now().strftime("%Y-%m")
        return transactions[0].date[:7]

    @staticmethod
    def _month_keys(transactions: list[ParsedTransaction]) -> list[str]:
        return sorted({tx.date[:7] for tx in transactions})

    @staticmethod
    def _date_range(transactions: list[ParsedTransaction]) -> dict[str, str]:
        dates = [tx.date for tx in transactions]
        return {"start": min(dates), "end": max(dates)}

    @classmethod
    def _totals(cls, transactions: list[ParsedTransaction]) -> dict[str, float]:
        income = cls._sum(tx.amount for tx in transactions if tx.amount > 0)
        spend = abs(cls._sum(tx.amount for tx in transactions if tx.amount < 0))
        return {
            "income": cls._money(income),
            "spend": cls._money(spend),
            "net": cls._money(income - spend),
        }

    @staticmethod
    def _validate_transactions(transactions: list[ParsedTransaction]) -> None:
        if not transactions:
            raise ValueError("No transactions could be parsed from the statement.")
        for tx in transactions:
            datetime.strptime(tx.date, "%Y-%m-%d")
            if not tx.description.strip():
                raise ValueError(f"Transaction on {tx.date} is missing a description.")
            amount = Decimal(str(tx.amount))
            if not amount.is_finite():
                raise ValueError(f"Transaction on {tx.date} has an invalid amount.")

    @staticmethod
    def _sum(values) -> Decimal:
        total = Decimal("0")
        for value in values:
            total += Decimal(str(value))
        return total

    @staticmethod
    def _money(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _normalise_date(value: str) -> str:
        value = value.strip()
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value[:10]
        sep = "." if "." in value else "/" if "/" in value else "-"
        day, month, year = value.split(sep)
        if len(year) == 2:
            year = f"20{year}"
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

    @staticmethod
    def _parse_amount(value: str) -> float:
        cleaned = value.strip().replace(" ", "")
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        return float(cleaned)


class IngestBankStatementTool(BaseTool):
    @property
    def name(self) -> str:
        return "ingest_bank_statement"

    @property
    def description(self) -> str:
        return (
            "Process a local bank statement PDF and store extracted transactions. "
            "Input JSON: {'file_path': '/path/to.pdf', 'bank_name': 'generic'}."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.ingestor = FinanceIngestor(db_path)

    async def execute(self, input_text: str) -> str:
        try:
            payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
            result = await self.ingestor.ingest_pdf(
                payload["file_path"],
                payload.get("bank_name", "generic"),
                hard_refresh=bool(payload.get("hard_refresh", False)),
                original_filename=payload.get("original_filename"),
            )
            action = "hard-refreshed" if result.get("hard_refresh") else "ingested"
            return (
                f"FinanceOS {action} {result['inserted_count']} transactions "
                f"for {', '.join(result.get('month_keys') or [result['month_key']])} "
                f"from {result['source_file']}. "
                f"Deleted {result.get('deleted_count', 0)} prior rows. "
                f"{result.get('uncategorised_count', 0)} need review."
            )
        except Exception as exc:
            return f"FinanceOS ingest error: {exc}"
