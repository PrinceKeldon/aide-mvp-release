"""
Guided first-time FinanceOS budget setup.

This module keeps the setup as a persistent state machine so AIDE can ask one
question at a time, save each answer, and resume after a restart.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.finance_engine import FinanceEngine
from core.settings import settings


SETUP_ID = "default"


class FinanceBudgetSetup:
    STEPS = [
        "income",
        "fixed_costs",
        "savings",
        "groceries",
        "dining_out",
        "entertainment",
    ]

    CATEGORY_BY_STEP = {
        "income": "Income",
        "savings": "Savings",
        "groceries": "Groceries",
        "dining_out": "Dining Out",
        "entertainment": "Entertainment",
    }

    QUESTIONS = {
        "income": "What is your monthly take-home income?",
        "fixed_costs": (
            "What are your fixed monthly costs? You can answer as a total, or as items like "
            "rent 1200, utilities 180, insurance 90."
        ),
        "savings": "How much do you want to save each month?",
        "groceries": "What monthly target do you want for groceries?",
        "dining_out": "What monthly target do you want for dining out?",
        "entertainment": "What monthly target do you want for entertainment?",
    }

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or settings.memory_db_path)
        self.engine = FinanceEngine(self.db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_budget_setup (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    current_step TEXT NOT NULL DEFAULT 'income',
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def start(self, reset: bool = False) -> dict[str, Any]:
        state = self.state()
        if reset or state["status"] == "not_started":
            now = self._now()
            state = {
                "id": SETUP_ID,
                "status": "in_progress",
                "current_step": self.STEPS[0],
                "data": {
                    "suggestions": self._fixed_cost_suggestions(),
                    "answers": {},
                    "targets": {},
                },
                "created_at": now,
                "updated_at": now,
            }
            self._save(state)
        return self._response(state)

    def status(self) -> dict[str, Any]:
        return self._response(self.state())

    def reset(self) -> dict[str, Any]:
        with self._conn() as conn:
            conn.execute("DELETE FROM finance_budget_setup WHERE id = ?", (SETUP_ID,))
        return self.start(reset=True)

    def answer(self, value: Any, step: str | None = None) -> dict[str, Any]:
        state = self.state()
        if state["status"] in {"not_started", "completed"}:
            state = self.start(reset=state["status"] == "not_started")
            if "question" in state:
                state = self.state()

        current_step = step or state["current_step"]
        if current_step not in self.STEPS:
            raise ValueError(f"Unknown setup step: {current_step}")

        parsed = self._parse_answer(current_step, value)
        data = state["data"]
        data.setdefault("answers", {})[current_step] = parsed
        data.setdefault("targets", {}).update(self._targets_for_answer(current_step, parsed))

        next_step = self._next_step(current_step)
        state["data"] = data
        state["updated_at"] = self._now()
        if next_step:
            state["current_step"] = next_step
            state["status"] = "in_progress"
            self._save(state)
            return self._response(state, saved=parsed)

        state["current_step"] = "complete"
        state["status"] = "completed"
        self._apply_targets(data.get("targets", {}), data.get("answers", {}))
        self._save(state)
        return self._response(state, saved=parsed)

    def state(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM finance_budget_setup WHERE id = ?",
                (SETUP_ID,),
            ).fetchone()
        if not row:
            now = self._now()
            return {
                "id": SETUP_ID,
                "status": "not_started",
                "current_step": "income",
                "data": {},
                "created_at": now,
                "updated_at": now,
            }
        data = dict(row)
        data["data"] = json.loads(data["data"] or "{}")
        return data

    def _save(self, state: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO finance_budget_setup
                (id, status, current_step, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    current_step = excluded.current_step,
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    state.get("id") or str(uuid.uuid4()),
                    state["status"],
                    state["current_step"],
                    json.dumps(state.get("data", {})),
                    state.get("created_at") or self._now(),
                    state["updated_at"],
                ),
            )

    def _response(self, state: dict[str, Any], saved: Any | None = None) -> dict[str, Any]:
        payload = {
            "status": state["status"],
            "current_step": state["current_step"],
            "data": state.get("data", {}),
            "saved": saved,
        }
        if state["status"] == "not_started":
            payload["message"] = "FinanceOS budget setup has not started."
            payload["next_action"] = "start"
        elif state["status"] == "completed":
            payload["message"] = self._completion_message(state)
            payload["targets"] = state.get("data", {}).get("targets", {})
        else:
            payload["question"] = self.QUESTIONS[state["current_step"]]
            if state["current_step"] == "fixed_costs":
                suggestions = state.get("data", {}).get("suggestions", {})
                if suggestions:
                    payload["suggestions"] = suggestions
        return payload

    def _completion_message(self, state: dict[str, Any]) -> str:
        targets = state.get("data", {}).get("targets", {})
        if not targets:
            return "FinanceOS budget setup is complete."
        ordered = [
            f"{category}: EUR {amount:.2f}"
            for category, amount in targets.items()
        ]
        return "FinanceOS budget setup is complete. I saved these targets: " + "; ".join(ordered) + "."

    def _next_step(self, step: str) -> str | None:
        index = self.STEPS.index(step)
        if index + 1 >= len(self.STEPS):
            return None
        return self.STEPS[index + 1]

    def _targets_for_answer(self, step: str, parsed: Any) -> dict[str, float]:
        if step == "fixed_costs":
            if isinstance(parsed, dict):
                return {
                    self._category_for_fixed_item(name): float(amount)
                    for name, amount in parsed.items()
                    if float(amount) >= 0
                }
            return {"Housing": float(parsed)}
        category = self.CATEGORY_BY_STEP.get(step)
        return {category: float(parsed)} if category else {}

    def _apply_targets(self, targets: dict[str, float], answers: dict[str, Any]) -> None:
        notes = "Set during guided first-time FinanceOS budget setup."
        for category, amount in targets.items():
            self.engine.set_budget_target(category, float(amount), notes)

        fixed_answer = answers.get("fixed_costs")
        if isinstance(fixed_answer, dict):
            total_fixed = sum(float(amount) for amount in fixed_answer.values())
            self.engine.set_budget_target(
                "Bills",
                total_fixed,
                "Total fixed monthly costs from guided setup.",
            )

    def _parse_answer(self, step: str, value: Any) -> Any:
        if isinstance(value, dict):
            if "items" in value and isinstance(value["items"], dict):
                return {
                    str(key).strip(): self._amount(amount)
                    for key, amount in value["items"].items()
                }
            if "amount" in value and len(value) <= 3:
                return self._amount(value["amount"])
            return {
                str(key).strip(): self._amount(amount)
                for key, amount in value.items()
                if key not in {"step", "notes"}
            }
        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()
        if not text:
            raise ValueError("Budget setup answer cannot be empty.")
        if step == "fixed_costs" and self._looks_like_item_list(text):
            return self._parse_itemised_costs(text)
        return self._amount(text)

    def _parse_itemised_costs(self, text: str) -> dict[str, float]:
        items: dict[str, float] = {}
        for part in re.split(r"[,;\n]+", text):
            part = part.strip()
            if not part:
                continue
            match = re.search(r"(?P<label>[A-Za-z ]+?)\s*(?:=|:)?\s*(?P<amount>[€$]?\s*\d[\d.,]*)", part)
            if not match:
                continue
            label = re.sub(r"\s+", " ", match.group("label")).strip().lower()
            items[label] = self._amount(match.group("amount"))
        if not items:
            return {"fixed_costs": self._amount(text)}
        return items

    @staticmethod
    def _looks_like_item_list(text: str) -> bool:
        return bool(re.search(r"[A-Za-z]\s+\d", text) and re.search(r"[,;\n]", text))

    @staticmethod
    def _amount(value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace("EUR", "").replace("€", "")
        match = re.search(r"-?\d[\d.,]*", text)
        if not match:
            raise ValueError(f"Could not read amount from: {value}")
        raw = match.group(0)
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            raw = raw.replace(",", ".")
        return float(raw)

    @staticmethod
    def _category_for_fixed_item(name: str) -> str:
        lowered = name.lower()
        if any(token in lowered for token in ["rent", "miete", "mortgage", "housing"]):
            return "Housing"
        if any(token in lowered for token in ["utility", "utilities", "internet", "electric", "gas", "strom"]):
            return "Utilities"
        return "Bills"

    def _fixed_cost_suggestions(self) -> dict[str, float]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT category, ROUND(AVG(monthly_total), 2) AS suggested
                FROM (
                    SELECT month_key, category, SUM(ABS(amount)) AS monthly_total
                    FROM transactions
                    WHERE amount < 0
                      AND category IN ('Housing', 'Utilities', 'Bills')
                    GROUP BY month_key, category
                )
                GROUP BY category
                """
            ).fetchall()
        return {row["category"]: float(row["suggested"]) for row in rows if row["suggested"] is not None}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
