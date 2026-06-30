import sqlite3

import pytest

from core.finance_categoriser import FinanceCategoriser
from core.finance_chat import FinanceChatService
from core.finance_engine import FinanceEngine
from core.finance_goals import FinanceGoals
from core.finance_memory import FinanceMemoryVault
from core.finance_report import FinanceReportGenerator
from core.finance_setup import FinanceBudgetSetup
from tools.finance_budget import SetupFinanceBudgetTool
from tools.finance_ingest import FinanceIngestor, ParsedTransaction


def test_generic_statement_parser_extracts_transactions(tmp_path):
    ingestor = FinanceIngestor(tmp_path / "finance.db")
    text = """
    01.04.2026 REWE SAGT DANKE 14032026 -42,50 EUR
    02.04.2026 GEHALT APRIL 3.200,00 EUR
    03.04.2026 LIEFERANDO BERLIN -28,90 EUR
    """

    transactions = ingestor.parse_text(text)

    assert [tx.description for tx in transactions] == [
        "REWE SAGT DANKE 14032026",
        "GEHALT APRIL",
        "LIEFERANDO BERLIN",
    ]
    assert transactions[0].date == "2026-04-01"
    assert transactions[0].amount == -42.50
    assert transactions[1].amount == 3200.00


def test_store_transactions_deduplicates_by_hash(tmp_path):
    ingestor = FinanceIngestor(tmp_path / "finance.db")
    transactions = [
        ParsedTransaction("2026-04-01", "REWE", -42.5),
        ParsedTransaction("2026-04-01", "REWE", -42.5),
    ]

    inserted_once = ingestor._store_transactions(transactions, "2026-04", "statement.pdf")
    inserted_twice = ingestor._store_transactions(transactions, "2026-04", "statement.pdf")

    assert inserted_once == 1
    assert inserted_twice == 0


def test_store_transactions_uses_each_transaction_month(tmp_path):
    ingestor = FinanceIngestor(tmp_path / "finance.db")
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-03-31", "Salary March", 3000.0),
            ParsedTransaction("2026-04-01", "REWE", -42.5),
        ],
        "2026-03",
        "statement.pdf",
    )

    with sqlite3.connect(tmp_path / "finance.db") as conn:
        rows = dict(
            conn.execute(
                "SELECT description, month_key FROM transactions ORDER BY date"
            ).fetchall()
        )

    assert rows == {"Salary March": "2026-03", "REWE": "2026-04"}


@pytest.mark.asyncio
async def test_ingest_pdf_hard_refresh_rewrites_same_statement(tmp_path, monkeypatch):
    db_path = tmp_path / "finance.db"
    pdf_path = tmp_path / "statement.pdf"
    pdf_path.write_bytes(b"%PDF test placeholder")
    statement_text = """
    01.04.2026 UNKNOWN MERCHANT -10,00 EUR
    02.04.2026 GEHALT 100,00 EUR
    """
    ingestor = FinanceIngestor(db_path)
    monkeypatch.setattr(ingestor, "_extract_text", lambda path: statement_text)

    first = await ingestor.ingest_pdf(
        pdf_path,
        hard_refresh=False,
        original_filename="statement.pdf",
        rewrite_reports=False,
    )
    second_without_refresh = await ingestor.ingest_pdf(
        pdf_path,
        hard_refresh=False,
        original_filename="statement.pdf",
        rewrite_reports=False,
    )
    refreshed = await ingestor.ingest_pdf(
        pdf_path,
        hard_refresh=True,
        original_filename="statement.pdf",
        rewrite_reports=False,
    )

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        statement_hashes = {
            row[0] for row in conn.execute("SELECT statement_hash FROM transactions")
        }

    assert first["inserted_count"] == 2
    assert second_without_refresh["inserted_count"] == 0
    assert refreshed["deleted_count"] == 2
    assert refreshed["inserted_count"] == 2
    assert refreshed["hard_refresh"] is True
    assert count == 2
    assert statement_hashes == {refreshed["statement_hash"]}


@pytest.mark.asyncio
async def test_categoriser_uses_keywords_and_corrections(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [ParsedTransaction("2026-04-01", "ZALANDO ORDER", -89.0)],
        "2026-04",
        "statement.pdf",
    )
    categoriser = FinanceCategoriser(db_path)

    result = await categoriser.categorise("REWE SAGT DANKE", -12.0)
    updated = await categoriser.correct_transaction_category("ZALANDO", "Clothing")

    assert result.category == "Groceries"
    assert updated == 1
    with sqlite3.connect(db_path) as conn:
        category = conn.execute(
            "SELECT category FROM transactions WHERE description = 'ZALANDO ORDER'"
        ).fetchone()[0]
    assert category == "Clothing"


@pytest.mark.asyncio
async def test_categorise_month_does_not_call_llm_by_default(tmp_path):
    class FailingLLM:
        async def chat(self, *args, **kwargs):
            raise AssertionError("Finance ingestion must not call the LLM by default")

    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "UNKNOWN MERCHANT ONE", -12.0),
            ParsedTransaction("2026-04-02", "REWE SAGT DANKE", -42.5),
        ],
        "2026-04",
        "statement.pdf",
    )
    categoriser = FinanceCategoriser(db_path, llm=FailingLLM())

    result = await categoriser.categorise_month("2026-04")

    assert result == {"updated": 2, "uncategorised": 1, "llm_used": 0}
    with sqlite3.connect(db_path) as conn:
        rows = dict(
            conn.execute(
                "SELECT description, category FROM transactions ORDER BY date"
            ).fetchall()
        )
    assert rows["UNKNOWN MERCHANT ONE"] == "Uncategorised"
    assert rows["REWE SAGT DANKE"] == "Groceries"


def test_budget_engine_comparison(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "GEHALT", 3200.0),
            ParsedTransaction("2026-04-02", "REWE", -420.0),
            ParsedTransaction("2026-04-03", "LIEFERANDO", -180.0),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE amount > 0")
        conn.execute("UPDATE transactions SET category = 'Groceries' WHERE description = 'REWE'")
        conn.execute("UPDATE transactions SET category = 'Dining Out' WHERE description = 'LIEFERANDO'")
    engine = FinanceEngine(db_path)
    engine.set_budget_target("Groceries", 350)
    engine.set_budget_target("Dining Out", 100)

    comparison = engine.comparison("2026-04")

    assert comparison["total_income"] == 3200.0
    assert comparison["total_spend"] == 600.0
    assert comparison["net_position"] == 2600.0
    assert comparison["top_overspend"][0]["category"] == "Dining Out"


def test_budget_engine_uses_decimal_precision(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "Salary", 1.0),
            ParsedTransaction("2026-04-02", "Tiny one", -0.1),
            ParsedTransaction("2026-04-03", "Tiny two", -0.2),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE amount > 0")
        conn.execute("UPDATE transactions SET category = 'Uncategorised' WHERE amount < 0")

    comparison = FinanceEngine(db_path).comparison("2026-04")

    assert comparison["total_spend"] == 0.3
    assert comparison["net_position"] == 0.7
    assert comparison["date_range"] == {"start": "2026-04-01", "end": "2026-04-03"}


def test_review_queue_category_update_learns_rule(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "BIO COMPANY 123456", -22.0),
            ParsedTransaction("2026-04-08", "BIO COMPANY 789000", -18.0),
        ],
        "2026-04",
        "statement.pdf",
    )

    engine = FinanceEngine(db_path)
    queue = engine.review_queue("2026-04")
    result = engine.update_transaction_category(queue[0]["id"], "Groceries")

    assert result["learned_pattern"] == "BIO COMPANY"
    assert result["updated_count"] == 2
    assert engine.review_queue("2026-04") == []
    with sqlite3.connect(db_path) as conn:
        rule = conn.execute(
            "SELECT pattern, category FROM merchant_rules"
        ).fetchone()
    assert tuple(rule) == ("BIO COMPANY", "Groceries")


def test_recurring_targets_alerts_and_range_summary(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-03-01", "GEHALT", 3000.0),
            ParsedTransaction("2026-03-03", "Rent", -1000.0),
            ParsedTransaction("2026-03-05", "Netflix", -15.0),
            ParsedTransaction("2026-04-01", "GEHALT", 3100.0),
            ParsedTransaction("2026-04-03", "Rent", -1000.0),
            ParsedTransaction("2026-04-05", "Netflix", -15.0),
            ParsedTransaction("2026-04-06", "Unknown kiosk", -9.0),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE description = 'GEHALT'")
        conn.execute("UPDATE transactions SET category = 'Housing' WHERE description = 'Rent'")
        conn.execute("UPDATE transactions SET category = 'Entertainment' WHERE description = 'Netflix'")
        conn.execute("UPDATE transactions SET category = 'Uncategorised' WHERE description = 'Unknown kiosk'")

    engine = FinanceEngine(db_path)
    engine.set_budget_target("Entertainment", 10)

    recurring = engine.recurring_transactions()
    suggestions = engine.target_suggestions("2026-04")
    alerts = engine.alerts("2026-04")
    summary = engine.summary_for_period("custom", start="2026-04-01", end="2026-04-30")

    assert any(row["merchant"] == "Rent" and row["cadence"] == "monthly" for row in recurring)
    assert any(row["category"] == "Housing" and row["suggested_target"] == 1000.0 for row in suggestions)
    assert any(alert["title"] == "Entertainment over target" for alert in alerts)
    assert any(alert["title"] == "Uncategorised transactions" for alert in alerts)
    assert summary["total_income"] == 3100.0
    assert summary["total_spend"] == 1024.0
    assert summary["transaction_count"] == 4


def test_range_comparison_drives_full_statistics(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-02-09", "GEHALT FEB", 1000.0),
            ParsedTransaction("2026-02-10", "REWE FEB", -200.0),
            ParsedTransaction("2026-05-05", "GEHALT MAY", 2000.0),
            ParsedTransaction("2026-05-05", "REWE MAY", -300.0),
        ],
        "2026-05",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE amount > 0")
        conn.execute("UPDATE transactions SET category = 'Groceries' WHERE amount < 0")
    engine = FinanceEngine(db_path)
    engine.set_budget_target("Groceries", 100)

    all_data = engine.comparison_for_period("all")
    custom = engine.comparison_for_period(
        "custom",
        start="2026-05-01",
        end="2026-05-31",
    )

    assert all_data["month_key"] == "All Ingested Data"
    assert all_data["date_range"] == {"start": "2026-02-09", "end": "2026-05-05"}
    assert all_data["total_income"] == 3000.0
    assert all_data["total_spend"] == 500.0
    assert all_data["transaction_count"] == 4
    assert custom["date_range"] == {"start": "2026-05-05", "end": "2026-05-05"}
    assert custom["total_income"] == 2000.0
    assert custom["total_spend"] == 300.0
    assert custom["transaction_count"] == 2


def test_report_and_goals_output(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "GEHALT", 3200.0),
            ParsedTransaction("2026-04-02", "REWE", -300.0),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE amount > 0")
        conn.execute("UPDATE transactions SET category = 'Groceries' WHERE amount < 0")
    FinanceEngine(db_path).set_budget_target("Groceries", 350)
    goal = FinanceGoals(db_path).create_goal(
        "Japan trip",
        1800,
        monthly_contribution=180,
    )

    report = FinanceReportGenerator(db_path, reports_dir=tmp_path).monthly_report(
        "2026-04",
        save=False,
    )

    assert "AIDE - 2026-04 Overview" in report["telegram"]
    assert "The Month in Numbers" in report["markdown"]
    assert "Transactions checked: 2" in report["markdown"]
    assert goal["title"] == "Japan trip"


def test_finance_memory_vault_writes_reports_ingestions_and_chat(tmp_path):
    vault = FinanceMemoryVault(tmp_path / "vault")

    report_path = vault.write_report(
        "2026-04",
        "# Report\n",
        {"month_key": "2026-04", "total_income": 100.0},
    )
    ingestion_path = vault.write_ingestion_summary(
        {
            "source_file": "statement.pdf",
            "bank_name": "generic",
            "month_key": "2026-04",
            "month_keys": ["2026-03", "2026-04"],
            "date_range": {"start": "2026-03-31", "end": "2026-04-01"},
            "totals": {"income": 100.0, "spend": 25.0, "net": 75.0},
            "parsed_count": 2,
            "inserted_count": 2,
            "categorised_count": 1,
            "uncategorised_count": 1,
        }
    )
    chat_path = vault.append_chat(
        month_key="2026-04",
        user_message="What changed?",
        assistant_reply="Spend increased.",
        timestamp="2026-04-02T10:00:00+00:00",
    )

    assert report_path.exists()
    assert (vault.data_dir / "2026-04.json").exists()
    assert "Months detected: 2026-03, 2026-04" in ingestion_path.read_text()
    assert "AIDE Finance" in chat_path.read_text()


def test_guided_budget_setup_saves_targets(tmp_path):
    db_path = tmp_path / "finance.db"
    setup = FinanceBudgetSetup(db_path)

    started = setup.start()
    assert started["status"] == "in_progress"
    assert started["question"] == "What is your monthly take-home income?"

    assert setup.answer("3200")["current_step"] == "fixed_costs"
    assert setup.answer("rent 1200, utilities 180, insurance 90")["current_step"] == "savings"
    assert setup.answer("500")["current_step"] == "groceries"
    assert setup.answer("350")["current_step"] == "dining_out"
    assert setup.answer("180")["current_step"] == "entertainment"
    completed = setup.answer("120")

    assert completed["status"] == "completed"
    with sqlite3.connect(db_path) as conn:
        targets = dict(
            conn.execute(
                "SELECT category, monthly_target FROM budget_targets WHERE monthly_target > 0"
            ).fetchall()
        )
    assert targets["Income"] == 3200
    assert targets["Housing"] == 1200
    assert targets["Utilities"] == 180
    assert targets["Bills"] == 1470
    assert targets["Savings"] == 500
    assert targets["Groceries"] == 350
    assert targets["Dining Out"] == 180
    assert targets["Entertainment"] == 120


@pytest.mark.asyncio
async def test_setup_finance_budget_tool_formats_next_question(tmp_path):
    tool = SetupFinanceBudgetTool(tmp_path / "finance.db")

    started = await tool.execute('{"action": "start"}')
    answered = await tool.execute('{"action": "answer", "value": 3200}')

    assert "What is your monthly take-home income?" in started
    assert "Saved. What are your fixed monthly costs?" in answered


def test_budget_setup_uses_fixed_cost_suggestions(tmp_path):
    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "Rent", -1200.0),
            ParsedTransaction("2026-04-02", "Telekom", -80.0),
            ParsedTransaction("2026-04-03", "Insurance", -90.0),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Housing' WHERE description = 'Rent'")
        conn.execute("UPDATE transactions SET category = 'Utilities' WHERE description = 'Telekom'")
        conn.execute("UPDATE transactions SET category = 'Bills' WHERE description = 'Insurance'")

    setup = FinanceBudgetSetup(db_path)
    setup.start()
    fixed_step = setup.answer("3200")

    assert fixed_step["current_step"] == "fixed_costs"
    assert fixed_step["suggestions"] == {
        "Bills": 90.0,
        "Housing": 1200.0,
        "Utilities": 80.0,
    }


@pytest.mark.asyncio
async def test_finance_chat_uses_private_context_and_persists(tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, **kwargs):
            self.calls.append({"messages": messages, "kwargs": kwargs})
            return {"content": "Income is EUR 1000.00 and spend is EUR 200.00."}

    db_path = tmp_path / "finance.db"
    ingestor = FinanceIngestor(db_path)
    ingestor._store_transactions(
        [
            ParsedTransaction("2026-04-01", "Salary", 1000.0),
            ParsedTransaction("2026-04-02", "REWE", -200.0),
        ],
        "2026-04",
        "statement.pdf",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE transactions SET category = 'Income' WHERE amount > 0")
        conn.execute("UPDATE transactions SET category = 'Groceries' WHERE amount < 0")

    fake_llm = FakeLLM()
    service = FinanceChatService(
        db_path,
        llm=fake_llm,
        vault=FinanceMemoryVault(tmp_path / "vault"),
    )
    result = await service.ask("How did April look?", month_key="2026-04")

    assert result["reply"] == "Income is EUR 1000.00 and spend is EUR 200.00."
    assert fake_llm.calls[0]["kwargs"]["offline"] is True
    assert fake_llm.calls[0]["kwargs"]["temperature"] == 0
    assert '"month_key": "2026-04"' in fake_llm.calls[0]["messages"][1]["content"]
    history = service.history("2026-04")
    assert [row["role"] for row in history] == ["user", "assistant"]
