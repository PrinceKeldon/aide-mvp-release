import sqlite3

import pytest


@pytest.mark.asyncio
async def test_finance_ingestor_parses_and_stores_statement(tmp_path, monkeypatch):
    from tools import finance_ingest
    from tools.finance_ingest import FinanceIngestor

    db_path = tmp_path / "finance.db"
    statement_path = tmp_path / "statement.pdf"
    statement_path.write_bytes(b"%PDF test placeholder")

    class FakeVault:
        def write_ingestion_summary(self, result):
            path = tmp_path / "ingestion.md"
            path.write_text(result["month_key"], encoding="utf-8")
            return path

    monkeypatch.setattr(finance_ingest, "FinanceMemoryVault", FakeVault)
    monkeypatch.setattr(
        FinanceIngestor,
        "_extract_text",
        lambda self, path: "\n".join(
            [
                "01.05.2026 REWE MARKET -42,15 EUR",
                    "02.05.2026 Salary ACME 2500.00 EUR",
            ]
        ),
    )

    result = await FinanceIngestor(db_path).ingest_pdf(
        statement_path,
        bank_name="generic",
        rewrite_reports=False,
    )

    assert result["parsed_count"] == 2
    assert result["inserted_count"] == 2
    assert result["month_key"] == "2026-05"
    assert result["totals"] == {"income": 2500.0, "spend": 42.15, "net": 2457.85}

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT description, amount, category, month_key FROM transactions ORDER BY date"
        ).fetchall()

    assert rows == [
        ("REWE MARKET", -42.15, "Groceries", "2026-05"),
        ("Salary ACME", 2500.0, "Income", "2026-05"),
    ]


@pytest.mark.asyncio
async def test_finance_categoriser_applies_keywords_and_updates_month(tmp_path):
    from core.finance_categoriser import FinanceCategoriser

    db_path = tmp_path / "finance.db"
    categoriser = FinanceCategoriser(db_path)

    direct = await categoriser.categorise("LIDL Berlin", -18.9)
    assert direct.category == "Groceries"
    assert direct.source == "keyword"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE transactions (
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
        conn.execute(
            """
            INSERT INTO transactions
            (id, date, description, amount, currency, category, source_file, month_key, hash, created_at)
            VALUES ('tx-1', '2026-05-03', 'BVG Ticket', -3.5, 'EUR', NULL, 'statement.pdf', '2026-05', 'hash-1', 'now')
            """
        )

    summary = await categoriser.categorise_month("2026-05", use_llm=False)

    assert summary == {"updated": 1, "uncategorised": 0, "llm_used": 0}
    with sqlite3.connect(db_path) as conn:
        category = conn.execute(
            "SELECT category FROM transactions WHERE id = 'tx-1'"
        ).fetchone()[0]

    assert category == "Transport"
