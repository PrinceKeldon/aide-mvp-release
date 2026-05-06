"""
FinanceOS budget comparison engine.
"""

from __future__ import annotations

import sqlite3
import uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any

from core.finance_categoriser import DEFAULT_CATEGORIES
from core.settings import settings


class FinanceEngine:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or settings.memory_db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
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
                    statement_hash TEXT,
                    original_filename TEXT,
                    created_at TEXT NOT NULL
                );
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

    def set_budget_target(self, category: str, monthly_target: float, notes: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO budget_targets (id, category, monthly_target, notes, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(category) DO UPDATE SET
                    monthly_target = excluded.monthly_target,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                (str(uuid.uuid4()), category.strip(), float(monthly_target), notes, now),
            )

    def comparison(self, month_key: str) -> dict[str, Any]:
        transactions = self._transactions_for_month(month_key)
        comparison = self._comparison_from_transactions(
            transactions,
            label=month_key,
            target_multiplier=Decimal("1"),
        )
        comparison["vs_last_month"] = self._trend(month_key, 1)
        comparison["vs_3month_avg"] = self._trend(month_key, 3)
        return comparison

    def comparison_for_period(
        self,
        period: str = "all",
        *,
        month_key: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        start_date, end_date = self._period_bounds(
            period,
            month_key=month_key,
            start=start,
            end=end,
        )
        transactions = self._transactions_between(start_date, end_date)
        multiplier = self._target_multiplier(start_date, end_date, period)
        label = self._period_label(period, start_date, end_date, month_key)
        comparison = self._comparison_from_transactions(
            transactions,
            label=label,
            target_multiplier=multiplier,
        )
        comparison["period"] = period
        comparison["start"] = start_date
        comparison["end"] = end_date
        comparison["target_periods"] = self._money(multiplier)
        comparison["vs_last_month"] = {}
        comparison["vs_3month_avg"] = {}
        return comparison

    def _comparison_from_transactions(
        self,
        transactions: list[dict[str, Any]],
        *,
        label: str,
        target_multiplier: Decimal,
    ) -> dict[str, Any]:
        targets = self._targets()
        categories = sorted({*targets.keys(), *(tx["category"] or "Uncategorised" for tx in transactions)})
        income = self._sum_amounts(tx["amount"] for tx in transactions if tx["amount"] > 0)
        spend = abs(self._sum_amounts(tx["amount"] for tx in transactions if tx["amount"] < 0))
        savings = abs(
            self._sum_amounts(
                tx["amount"]
                for tx in transactions
                if (tx["category"] or "").lower() == "savings" and tx["amount"] < 0
            )
        )
        date_values = [tx["date"] for tx in transactions]
        variances = []
        for category in categories:
            actual_dec = self._actual_for_category_decimal(transactions, category)
            target_dec = self._to_decimal(targets.get(category, 0.0)) * target_multiplier
            actual = self._money(actual_dec)
            target = self._money(target_dec)
            variance = actual - target
            variance_pct = (variance / target * 100.0) if target else 0.0
            status = self._status(category, actual, target)
            variances.append(
                {
                    "category": category,
                    "target": round(target, 2),
                    "actual": round(actual, 2),
                    "variance": round(variance, 2),
                    "variance_pct": round(variance_pct, 1),
                    "status": status,
                }
            )
        overspend = [
            row for row in variances if row["status"] == "over" and row["category"] != "Income"
        ]
        underspend = [
            row for row in variances if row["status"] == "under" and row["category"] != "Income"
        ]
        uncategorised_total = abs(
            self._sum_amounts(
                tx["amount"]
                for tx in transactions
                if (tx["category"] or "Uncategorised") == "Uncategorised" and tx["amount"] < 0
            )
        )
        return {
            "month_key": label,
            "transaction_count": len(transactions),
            "date_range": {
                "start": min(date_values) if date_values else None,
                "end": max(date_values) if date_values else None,
            },
            "total_income": self._money(income),
            "total_spend": self._money(spend),
            "net_position": self._money(income - spend),
            "savings_rate": self._percent((savings / income * Decimal("100")) if income else Decimal("0")),
            "category_variances": variances,
            "top_overspend": sorted(overspend, key=lambda r: r["variance"], reverse=True)[:3],
            "top_underspend": sorted(underspend, key=lambda r: r["variance"])[:3],
            "uncategorised_total": self._money(uncategorised_total),
            "uncategorised_count": sum(1 for tx in transactions if (tx["category"] or "Uncategorised") == "Uncategorised"),
        }

    def dashboard_insights(self, month_key: str) -> dict[str, Any]:
        return {
            "categories": self.categories(),
            "review_queue": self.review_queue(month_key, limit=50),
            "recurring": self.recurring_transactions(limit=12),
            "target_suggestions": self.target_suggestions(month_key),
            "alerts": self.alerts(month_key),
            "range_summary": self.summary_for_period("month", month_key=month_key),
        }

    def dashboard_insights_for_period(
        self,
        comparison: dict[str, Any],
        *,
        month_key: str | None = None,
    ) -> dict[str, Any]:
        start = comparison["date_range"].get("start")
        end = comparison["date_range"].get("end")
        anchor_month = month_key or (end[:7] if end else None)
        return {
            "categories": self.categories(),
            "review_queue": self.review_queue_between(start, end, limit=50) if start and end else [],
            "recurring": self.recurring_transactions(limit=12),
            "target_suggestions": self.target_suggestions(anchor_month) if anchor_month else [],
            "alerts": self.alerts_for_comparison(comparison),
            "range_summary": self.summary_for_comparison(comparison),
        }

    def categories(self) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT category FROM budget_targets ORDER BY category"
            ).fetchall()
        existing = {row["category"] for row in rows}
        return sorted({*existing, *DEFAULT_CATEGORIES.keys(), "Uncategorised"})

    def review_queue(self, month_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        params: list[Any] = []
        query = """
            SELECT id, date, description, amount, currency, category, month_key
            FROM transactions
            WHERE (category IS NULL OR category = '' OR category = 'Uncategorised')
        """
        if month_key:
            query += " AND month_key = ?"
            params.append(month_key)
        query += " ORDER BY date ASC, created_at ASC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def review_queue_between(self, start: str, end: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, date, description, amount, currency, category, month_key
                FROM transactions
                WHERE date >= ? AND date <= ?
                  AND (category IS NULL OR category = '' OR category = 'Uncategorised')
                ORDER BY date ASC, created_at ASC
                LIMIT ?
                """,
                (start, end, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_transaction_category(
        self,
        transaction_id: str,
        category: str,
        *,
        learn_rule: bool = True,
    ) -> dict[str, Any]:
        category = category.strip()
        if category not in self.categories():
            raise ValueError(f"Unknown finance category: {category}")
        with self._conn() as conn:
            tx = conn.execute(
                "SELECT id, description FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if tx is None:
                raise ValueError("Transaction not found.")
            cursor = conn.execute(
                "UPDATE transactions SET category = ? WHERE id = ?",
                (category, transaction_id),
            )
            updated = int(cursor.rowcount or 0)
            pattern = self._merchant_pattern(tx["description"])
            rule_updates = 0
            if learn_rule and pattern:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO merchant_rules
                    (id, pattern, category, match_type, confidence, use_count, created_at)
                    VALUES (?, ?, ?, 'keyword', 1.0, 0, ?)
                    """,
                    (str(uuid.uuid4()), pattern, category, now),
                )
                cursor = conn.execute(
                    """
                    UPDATE transactions
                    SET category = ?
                    WHERE lower(description) LIKE lower(?)
                      AND (category IS NULL OR category = '' OR category = 'Uncategorised')
                    """,
                    (category, f"%{pattern}%"),
                )
                rule_updates = int(cursor.rowcount or 0)
        return {
            "transaction_id": transaction_id,
            "category": category,
            "learned_pattern": pattern if learn_rule else "",
            "updated_count": updated + rule_updates,
        }

    def recurring_transactions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT date, description, amount, currency, category, month_key
                FROM transactions
                ORDER BY date ASC
                """
            ).fetchall()
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            shaped = dict(row)
            pattern = self._merchant_pattern(shaped["description"])
            sign = "income" if shaped["amount"] > 0 else "expense"
            groups.setdefault((pattern, sign), []).append(shaped)
        recurring = []
        for (pattern, sign), txs in groups.items():
            months = sorted({tx["month_key"] for tx in txs})
            if len(txs) < 2 or len(months) < 2:
                continue
            amounts = [abs(self._to_decimal(tx["amount"])) for tx in txs]
            avg_amount = self._money(sum(amounts, Decimal("0")) / Decimal(len(amounts)))
            category_counts: dict[str, int] = {}
            for tx in txs:
                category = tx["category"] or "Uncategorised"
                category_counts[category] = category_counts.get(category, 0) + 1
            category = sorted(category_counts.items(), key=lambda item: item[1], reverse=True)[0][0]
            recurring.append(
                {
                    "merchant": pattern,
                    "type": sign,
                    "category": category,
                    "count": len(txs),
                    "months": months,
                    "average_amount": avg_amount,
                    "last_seen": max(tx["date"] for tx in txs),
                    "cadence": "monthly",
                }
            )
        recurring.sort(key=lambda row: (row["type"] != "income", -row["average_amount"], row["merchant"]))
        return recurring[:limit]

    def target_suggestions(self, month_key: str, months: int = 3) -> list[dict[str, Any]]:
        keys = [month_key, *self._previous_month_keys(month_key, months - 1)]
        targets = self._targets()
        by_category: dict[str, list[float]] = {}
        for key in keys:
            totals = self._category_totals(key)
            for category, amount in totals.items():
                if amount > 0:
                    by_category.setdefault(category, []).append(amount)
        suggestions = []
        for category, amounts in sorted(by_category.items()):
            average = self._money(
                sum((self._to_decimal(value) for value in amounts), Decimal("0"))
                / Decimal(len(amounts))
            )
            current = self._money(self._to_decimal(targets.get(category, 0.0)))
            if average <= 0:
                continue
            suggestions.append(
                {
                    "category": category,
                    "suggested_target": average,
                    "current_target": current,
                    "months_used": len(amounts),
                    "confidence": "medium" if len(amounts) >= 2 else "low",
                }
            )
        return suggestions

    def alerts(self, month_key: str) -> list[dict[str, Any]]:
        return self.alerts_for_comparison(self.comparison(month_key))

    def alerts_for_comparison(self, comparison: dict[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        if comparison["uncategorised_count"]:
            alerts.append(
                {
                    "level": "review",
                    "title": "Uncategorised transactions",
                    "message": (
                        f"{comparison['uncategorised_count']} transactions totalling "
                        f"EUR {comparison['uncategorised_total']:.2f} need review."
                    ),
                }
            )
        for row in comparison["top_overspend"]:
            alerts.append(
                {
                    "level": "warning",
                    "title": f"{row['category']} over target",
                    "message": (
                        f"Actual spend is EUR {row['actual']:.2f} against a "
                        f"EUR {row['target']:.2f} target."
                    ),
                }
            )
        income_row = next(
            (row for row in comparison["category_variances"] if row["category"] == "Income"),
            None,
        )
        if income_row and income_row["target"] > 0 and income_row["actual"] < income_row["target"] * 0.8:
            alerts.append(
                {
                    "level": "warning",
                    "title": "Income below target",
                    "message": (
                        f"Income is EUR {income_row['actual']:.2f} against a "
                        f"EUR {income_row['target']:.2f} target."
                    ),
                }
            )
        alerts.extend(self._goal_alerts())
        return alerts[:8]

    def summary_for_period(
        self,
        period: str,
        *,
        month_key: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        comparison = self.comparison_for_period(
            period,
            month_key=month_key,
            start=start,
            end=end,
        )
        return self.summary_for_comparison(comparison)

    @staticmethod
    def summary_for_comparison(comparison: dict[str, Any]) -> dict[str, Any]:
        return {
            "period": comparison.get("period", "month"),
            "start": comparison["date_range"].get("start"),
            "end": comparison["date_range"].get("end"),
            "transaction_count": comparison["transaction_count"],
            "total_income": comparison["total_income"],
            "total_spend": comparison["total_spend"],
            "net_position": comparison["net_position"],
            "categories": [
                {"category": row["category"], "actual": row["actual"]}
                for row in sorted(
                    comparison["category_variances"],
                    key=lambda row: row["actual"],
                    reverse=True,
                )
                if row["actual"] > 0
            ],
        }

    def _transactions_for_month(self, month_key: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE month_key = ? ORDER BY date ASC",
                (month_key,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _transactions_between(self, start: str, end: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transactions
                WHERE date >= ? AND date <= ?
                ORDER BY date ASC
                """,
                (start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def _targets(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute("SELECT category, monthly_target FROM budget_targets").fetchall()
        targets = {category: 0.0 for category in [*DEFAULT_CATEGORIES.keys(), "Uncategorised"]}
        targets.update({row["category"]: float(row["monthly_target"]) for row in rows})
        return targets

    @classmethod
    def _actual_for_category(cls, transactions: list[dict[str, Any]], category: str) -> float:
        return cls._money(cls._actual_for_category_decimal(transactions, category))

    @classmethod
    def _actual_for_category_decimal(
        cls,
        transactions: list[dict[str, Any]],
        category: str,
    ) -> Decimal:
        if category == "Income":
            return cls._sum_amounts(tx["amount"] for tx in transactions if tx["amount"] > 0)
        return abs(
            cls._sum_amounts(
                tx["amount"]
                for tx in transactions
                if (tx["category"] or "Uncategorised") == category and tx["amount"] < 0
            )
        )

    @staticmethod
    def _status(category: str, actual: float, target: float) -> str:
        if target <= 0:
            return "tracking" if actual else "unset"
        if category in {"Income", "Savings"}:
            return "under" if actual < target else "on_track"
        if actual > target * 1.05:
            return "over"
        if actual < target * 0.8:
            return "under"
        return "on_track"

    def _trend(self, month_key: str, months_back: int) -> dict[str, float]:
        keys = self._previous_month_keys(month_key, months_back)
        current = self._category_totals(month_key)
        previous = [self._category_totals(key) for key in keys]
        if not previous:
            return {}
        categories = set(current)
        for bucket in previous:
            categories.update(bucket)
        result = {}
        for category in categories:
            baseline = sum(bucket.get(category, 0.0) for bucket in previous) / len(previous)
            result[category] = round(current.get(category, 0.0) - baseline, 2)
        return result

    def _category_totals(self, month_key: str) -> dict[str, float]:
        totals: dict[str, Decimal] = {}
        for tx in self._transactions_for_month(month_key):
            category = tx["category"] or "Uncategorised"
            if tx["amount"] < 0:
                totals[category] = totals.get(category, Decimal("0")) + abs(self._to_decimal(tx["amount"]))
            elif category == "Income":
                totals[category] = totals.get(category, Decimal("0")) + self._to_decimal(tx["amount"])
        return {category: self._money(amount) for category, amount in totals.items()}

    def _goal_alerts(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT title, target_amount, current_amount
                    FROM finance_goals
                    WHERE status = 'active' AND current_amount >= target_amount
                    ORDER BY created_at ASC
                    LIMIT 3
                    """
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        return [
            {
                "level": "success",
                "title": f"{row['title']} reached",
                "message": f"Goal balance EUR {row['current_amount']:.2f} reached target EUR {row['target_amount']:.2f}.",
            }
            for row in rows
        ]

    @staticmethod
    def _previous_month_keys(month_key: str, months_back: int) -> list[str]:
        year, month = [int(part) for part in month_key.split("-")]
        keys = []
        for offset in range(1, months_back + 1):
            m = month - offset
            y = year
            while m <= 0:
                m += 12
                y -= 1
            keys.append(f"{y:04d}-{m:02d}")
        return keys

    def _period_bounds(
        self,
        period: str,
        *,
        month_key: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[str, str]:
        if period == "all":
            if start and end:
                self._validate_date(start)
                self._validate_date(end)
                if start > end:
                    raise ValueError("Start date must be before end date.")
                return start, end
            return self.all_data_bounds()
        if period == "custom":
            if not start or not end:
                raise ValueError("Custom date range requires start and end.")
            self._validate_date(start)
            self._validate_date(end)
            if start > end:
                raise ValueError("Start date must be before end date.")
            return start, end
        if not month_key:
            raise ValueError("Month key is required.")
        year, month = [int(part) for part in month_key.split("-")]
        if period == "quarter":
            quarter_start = ((month - 1) // 3) * 3 + 1
            quarter_end = quarter_start + 2
            return (
                f"{year:04d}-{quarter_start:02d}-01",
                self._month_end(year, quarter_end),
            )
        return f"{year:04d}-{month:02d}-01", self._month_end(year, month)

    def data_date_range(self) -> dict[str, str | None]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MIN(date) AS start, MAX(date) AS end FROM transactions"
            ).fetchone()
        return {"start": row["start"] if row else None, "end": row["end"] if row else None}

    def all_data_bounds(self) -> tuple[str, str]:
        date_range = self.data_date_range()
        start = date_range["start"]
        end = date_range["end"]
        if not start or not end:
            today = date.today().isoformat()
            return today, today
        return start, end

    def comparison_for_all_data(self) -> dict[str, Any]:
        start, end = self.all_data_bounds()
        return self.comparison_for_period("all", start=start, end=end)

    @staticmethod
    def _target_multiplier(start: str, end: str, period: str) -> Decimal:
        if period == "month":
            return Decimal("1")
        if period == "quarter":
            return Decimal("3")
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        days = max(1, (end_date - start_date).days + 1)
        return Decimal(days) / Decimal("30.4375")

    @staticmethod
    def _period_label(period: str, start: str, end: str, month_key: str | None) -> str:
        if period == "month" and month_key:
            return month_key
        if period == "quarter" and month_key:
            year, month = [int(part) for part in month_key.split("-")]
            quarter = ((month - 1) // 3) + 1
            return f"{year} Q{quarter}"
        if period == "all":
            return "All Ingested Data"
        return f"{start} to {end}"

    @staticmethod
    def _month_end(year: int, month: int) -> str:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        last_day = date.fromordinal(next_month.toordinal() - 1)
        return last_day.isoformat()

    @staticmethod
    def _validate_date(value: str) -> None:
        datetime.strptime(value, "%Y-%m-%d")

    @staticmethod
    def _merchant_pattern(description: str) -> str:
        words = []
        for raw in description.split():
            word = raw.strip(".,;:()[]{}")
            if not word:
                continue
            if sum(ch.isdigit() for ch in word) >= max(3, len(word) // 2):
                continue
            words.append(word)
        return " ".join(words[:4])[:80].strip() or description[:80].strip()

    @classmethod
    def _sum_amounts(cls, values) -> Decimal:
        total = Decimal("0")
        for value in values:
            total += cls._to_decimal(value)
        return total

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        return Decimal(str(value or 0))

    @staticmethod
    def _money(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _percent(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
