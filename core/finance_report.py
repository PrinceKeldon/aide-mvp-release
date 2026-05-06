"""
FinanceOS monthly report generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.finance_engine import FinanceEngine
from core.finance_goals import FinanceGoals
from core.finance_memory import FinanceMemoryVault


class FinanceReportGenerator:
    def __init__(self, db_path: str | Path | None = None, reports_dir: str | Path | None = None):
        self.engine = FinanceEngine(db_path)
        self.goals = FinanceGoals(db_path)
        self.reports_dir = Path(reports_dir or Path.home() / ".aide" / "reports" / "finance")

    def monthly_report(self, month_key: str, save: bool = True) -> dict[str, Any]:
        comparison = self.engine.comparison(month_key)
        report = {
            "month_key": month_key,
            "comparison": comparison,
            "telegram": self.telegram_summary(comparison),
            "markdown": self.markdown_report(comparison),
            "goals": self.goals.list_goals(),
            "insights": self.engine.dashboard_insights(month_key),
        }
        if save:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            path = self.reports_dir / f"{month_key}.md"
            path.write_text(report["markdown"], encoding="utf-8")
            report["saved_file"] = str(path)
            obsidian_path = FinanceMemoryVault().write_report(
                month_key,
                report["markdown"],
                comparison,
            )
            report["obsidian_file"] = str(obsidian_path)
        return report

    def telegram_summary(self, c: dict[str, Any]) -> str:
        things = []
        if c["top_overspend"]:
            top = c["top_overspend"][0]
            things.append(
                f"{top['category']} ran EUR {abs(top['variance']):.0f} over target."
            )
        if c["top_underspend"]:
            top = c["top_underspend"][0]
            things.append(
                f"{top['category']} came in EUR {abs(top['variance']):.0f} under target."
            )
        if c["uncategorised_count"]:
            things.append(
                f"{c['uncategorised_count']} transactions (EUR {c['uncategorised_total']:.0f}) need review."
            )
        while len(things) < 3:
            things.append("No additional finance attention item.")
        return (
            f"AIDE - {c['month_key']} Overview\n"
            f"Income: EUR {c['total_income']:.0f} · Spend: EUR {c['total_spend']:.0f} · "
            f"Surplus: EUR {c['net_position']:.0f} · Savings rate: {c['savings_rate']:.1f}%\n\n"
            "3 things to know:\n"
            f"1. {things[0]}\n2. {things[1]}\n3. {things[2]}\n\n"
            f"Full report: http://localhost:3000/finance/{c['month_key']}"
        )

    def markdown_report(self, c: dict[str, Any]) -> str:
        lines = [
            f"# AIDE Finance Overview - {c['month_key']}",
            "",
            "## The Month in Numbers",
            f"- Date range: {c['date_range'].get('start') or 'n/a'} to {c['date_range'].get('end') or 'n/a'}",
            f"- Transactions checked: {c['transaction_count']}",
            f"- Income: EUR {c['total_income']:.2f}",
            f"- Spend: EUR {c['total_spend']:.2f}",
            f"- Net position: EUR {c['net_position']:.2f}",
            f"- Savings rate: {c['savings_rate']:.1f}%",
            "",
            "## Where the Money Went",
        ]
        by_spend = sorted(
            [row for row in c["category_variances"] if row["actual"] > 0],
            key=lambda row: row["actual"],
            reverse=True,
        )[:5]
        for row in by_spend:
            lines.append(
                f"- {row['category']}: EUR {row['actual']:.2f} vs EUR {row['target']:.2f} target - {row['status']}"
            )
        lines.extend(["", "## Patterns Worth Noting"])
        for category, delta in sorted(c["vs_3month_avg"].items())[:8]:
            if delta:
                direction = "up" if delta > 0 else "down"
                lines.append(f"- {category} is {direction} EUR {abs(delta):.2f} vs the three-month average.")
        if len(lines) and lines[-1] == "## Patterns Worth Noting":
            lines.append("- Not enough history yet for reliable trend analysis.")
        lines.extend(["", "## Recommendations"])
        recommendations = self.recommendations(c)
        lines.extend([f"- {item}" for item in recommendations])
        lines.extend(["", "## What Needs Your Attention"])
        if c["uncategorised_count"]:
            lines.append(f"- Review {c['uncategorised_count']} uncategorised transactions totalling EUR {c['uncategorised_total']:.2f}.")
        for row in c["top_overspend"]:
            if row["target"] and row["variance_pct"] > 30:
                lines.append(f"- {row['category']} is {row['variance_pct']:.1f}% over target.")
        if lines[-1] == "## What Needs Your Attention":
            lines.append("- Nothing needs immediate review.")
        return "\n".join(lines) + "\n"

    def recommendations(self, c: dict[str, Any]) -> list[str]:
        recs = []
        if c["top_overspend"]:
            top = c["top_overspend"][0]
            recs.append(
                f"Reduce {top['category']} by EUR {min(abs(top['variance']), 50):.0f}/month to move back toward target."
            )
        if c["top_underspend"]:
            top = c["top_underspend"][0]
            if top["target"] > 0:
                recs.append(
                    f"Consider moving unused {top['category']} budget into savings or an active goal."
                )
        if c["net_position"] > 0:
            recs.append(
                f"Your EUR {c['net_position']:.0f} surplus can fund active finance goals before discretionary spend."
            )
        if c["uncategorised_count"]:
            recs.append("Clean up uncategorised transactions before making target changes.")
        return recs[:5] or ["Keep current targets until more transaction history is available."]
