"""
FinanceOS budget, categorisation, and goal tools.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.finance_categoriser import FinanceCategoriser
from core.finance_engine import FinanceEngine
from core.finance_goals import FinanceGoals
from core.finance_setup import FinanceBudgetSetup
from tools.base import BaseTool, SafetyTier


class SetBudgetTargetTool(BaseTool):
    @property
    def name(self) -> str:
        return "set_budget_target"

    @property
    def description(self) -> str:
        return "Set or update a FinanceOS monthly category target. Input JSON: category, monthly_target, notes."

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.engine = FinanceEngine(db_path)

    async def execute(self, input_text: str) -> str:
        payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
        self.engine.set_budget_target(
            payload["category"],
            float(payload["monthly_target"]),
            payload.get("notes", ""),
        )
        return f"Budget target set: {payload['category']} = EUR {float(payload['monthly_target']):.2f}/month."


class CategoriseTransactionTool(BaseTool):
    @property
    def name(self) -> str:
        return "categorise_transaction"

    @property
    def description(self) -> str:
        return "Correct FinanceOS transaction categorisation and teach AIDE a merchant rule. Input JSON: pattern, category."

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.categoriser = FinanceCategoriser(db_path)

    async def execute(self, input_text: str) -> str:
        payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
        count = await self.categoriser.correct_transaction_category(
            payload["pattern"],
            payload["category"],
        )
        return f"Finance rule saved. Updated {count} historical transactions matching {payload['pattern']}."


class CreateFinanceGoalTool(BaseTool):
    @property
    def name(self) -> str:
        return "create_finance_goal"

    @property
    def description(self) -> str:
        return "Create a savings or project funding goal. Input JSON: title, target_amount, deadline, monthly_contribution, linked_project_id."

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.goals = FinanceGoals(db_path)

    async def execute(self, input_text: str) -> str:
        payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
        goal = self.goals.create_goal(
            title=payload["title"],
            target_amount=float(payload["target_amount"]),
            description=payload.get("description", ""),
            deadline=payload.get("deadline"),
            monthly_contribution=float(payload.get("monthly_contribution", 0)),
            linked_project_id=payload.get("linked_project_id"),
        )
        return f"Finance goal created: {goal['title']} ({goal['id']}) for EUR {goal['target_amount']:.2f}."


class SetupFinanceBudgetTool(BaseTool):
    @property
    def name(self) -> str:
        return "setup_finance_budget"

    @property
    def description(self) -> str:
        return (
            "Run the guided first-time FinanceOS budget setup conversation. "
            "Use this when the user wants to set up their finance budget, or when they answer a setup question. "
            "Input JSON: action=start|answer|status|reset, and for answer include value. "
            "The tool returns the next question and saves targets when complete."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.setup = FinanceBudgetSetup(db_path)

    async def execute(self, input_text: str) -> str:
        try:
            payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
        except Exception:
            payload = {"action": "answer", "value": input_text}

        action = str(payload.get("action") or "status").lower()
        if action == "start":
            result = self.setup.start(reset=bool(payload.get("reset", False)))
        elif action == "reset":
            result = self.setup.reset()
        elif action == "answer":
            try:
                result = self.setup.answer(payload.get("value"), step=payload.get("step"))
            except Exception as exc:
                status = self.setup.status()
                question = status.get("question") or "Please send a number for this budget step."
                return f"FinanceOS setup could not read that answer: {exc}. {question}"
        elif action == "status":
            result = self.setup.status()
        else:
            return "FinanceOS setup error: action must be start, answer, status, or reset."

        return self._format_result(result)

    def _format_result(self, result: dict) -> str:
        if result.get("status") == "completed":
            return result.get("message", "FinanceOS budget setup is complete.")
        if result.get("status") == "not_started":
            return "FinanceOS budget setup has not started. Ask me to start budget setup when ready."
        question = result.get("question", "")
        saved = result.get("saved")
        prefix = "Saved. " if saved is not None else ""
        suggestions = result.get("suggestions") or {}
        if suggestions:
            rendered = ", ".join(
                f"{category}: EUR {amount:.2f}" for category, amount in suggestions.items()
            )
            return f"{prefix}{question} I found these fixed-cost suggestions from your transactions: {rendered}."
        return f"{prefix}{question}"
