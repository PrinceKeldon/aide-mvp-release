"""
FinanceOS goal tracking and project finance helpers.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.settings import settings


class FinanceGoals:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or settings.memory_db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS finance_goals (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_amount REAL NOT NULL,
                    current_amount REAL NOT NULL DEFAULT 0,
                    monthly_contribution REAL NOT NULL DEFAULT 0,
                    deadline TEXT,
                    linked_project_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                )
                """
            )

    def create_goal(
        self,
        title: str,
        target_amount: float,
        description: str = "",
        deadline: str | None = None,
        monthly_contribution: float = 0,
        linked_project_id: str | None = None,
    ) -> dict[str, Any]:
        goal = {
            "id": str(uuid.uuid4())[:8],
            "title": title.strip(),
            "description": description.strip(),
            "target_amount": float(target_amount),
            "current_amount": 0.0,
            "monthly_contribution": float(monthly_contribution),
            "deadline": deadline,
            "linked_project_id": linked_project_id,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO finance_goals
                (id, title, description, target_amount, current_amount, monthly_contribution,
                 deadline, linked_project_id, status, created_at)
                VALUES (:id, :title, :description, :target_amount, :current_amount,
                        :monthly_contribution, :deadline, :linked_project_id, :status, :created_at)
                """,
                goal,
            )
        return goal

    def list_goals(self, status: str = "active") -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM finance_goals WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        return [dict(row) for row in rows]

    def project_finance(self, project_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM finance_goals WHERE status = 'active'"
        params: tuple[Any, ...] = ()
        if project_id:
            query += " AND linked_project_id = ?"
            params = (project_id,)
        query += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            target = float(item["target_amount"] or 0)
            current = float(item["current_amount"] or 0)
            item["progress_pct"] = round((current / target * 100.0) if target else 0.0, 1)
            item["remaining_amount"] = round(max(target - current, 0.0), 2)
            result.append(item)
        return result
