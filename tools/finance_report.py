"""
FinanceOS reporting tools.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.finance_goals import FinanceGoals
from core.finance_report import FinanceReportGenerator
from tools.base import BaseTool, SafetyTier


class GetMonthlyOverviewTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_monthly_overview"

    @property
    def description(self) -> str:
        return "Generate a FinanceOS monthly overview. Input JSON: month_key."

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.reports = FinanceReportGenerator(db_path)

    async def execute(self, input_text: str) -> str:
        payload = json.loads(input_text) if isinstance(input_text, str) else dict(input_text)
        report = self.reports.monthly_report(payload["month_key"])
        saved = report.get("saved_file", "")
        return f"{report['telegram']}\n\nSaved: {saved}"


class GetProjectFinanceTool(BaseTool):
    @property
    def name(self) -> str:
        return "get_project_finance"

    @property
    def description(self) -> str:
        return "Show active finance goals, optionally filtered by project. Input JSON: linked_project_id."

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, db_path: str | Path | None = None):
        self.goals = FinanceGoals(db_path)

    async def execute(self, input_text: str) -> str:
        payload = json.loads(input_text) if input_text else {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        goals = self.goals.project_finance(payload.get("linked_project_id"))
        if not goals:
            return "No active project finance goals found."
        lines = ["AIDE - Project Finance"]
        for goal in goals:
            lines.append(
                f"{goal['title']}: EUR {goal['current_amount']:.2f} of EUR {goal['target_amount']:.2f} "
                f"({goal['progress_pct']:.1f}%)"
            )
        return "\n".join(lines)
