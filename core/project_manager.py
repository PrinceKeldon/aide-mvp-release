"""
Sprint 5: Long-Horizon Project Manager
AIDE / AIDE — Persistent goals, autonomous advancement, Telegram nudges

Data model:
  Project → subtasks + deadline + notes + contacts + linked email thread IDs
  Stored in SQLite (same DB as memory manager for simplicity)
  All actions gated through existing safety.py (APPROVE tier)
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field, asdict

from core.settings import settings


# ─────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────

@dataclass
class Subtask:
    id: str
    title: str
    status: str = "pending"          # pending | in_progress | done | blocked
    due_date: Optional[str] = None   # ISO date string
    notes: str = ""
    assigned_action: Optional[str] = None  # tool call AIDE will make when advancing


@dataclass
class Project:
    id: str
    title: str
    goal: str
    status: str = "active"           # active | paused | completed | archived
    deadline: Optional[str] = None   # ISO date string
    subtasks: list = field(default_factory=list)        # list[Subtask]
    notes: list = field(default_factory=list)           # list[str]
    contacts: list = field(default_factory=list)        # list[dict] {name, email, role}
    linked_email_ids: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_advanced: Optional[str] = None  # ISO — when AIDE last worked on this
    advance_log: list = field(default_factory=list)     # list[str] — human-readable history


# ─────────────────────────────────────────────
#  ProjectManager
# ─────────────────────────────────────────────

class ProjectManager:
    """
    Manages long-horizon projects in SQLite.
    Called by:
      - ProjectTool  (AIDE's ReAct loop)
      - scheduler.py (proactive checks)
      - telegram_bot.py (status/nudge messages)
    """

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(settings.memory_db_path)
        self._init_db()

    # ── DB setup ──────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    updated_at TEXT NOT NULL
                )
            """)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # ── CRUD ──────────────────────────────────

    def create_project(
        self,
        title: str,
        goal: str,
        deadline: Optional[str] = None,
        contacts: Optional[list] = None,
    ) -> Project:
        p = Project(
            id=str(uuid.uuid4())[:8],
            title=title,
            goal=goal,
            deadline=deadline,
            contacts=contacts or [],
        )
        self._save(p)
        return p

    def get_project(self, project_id: str) -> Optional[Project]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if not row:
            return None
        return self._deserialize(row[0])

    def list_projects(self, status: str = "active") -> list[Project]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM projects WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        return [self._deserialize(r[0]) for r in rows]

    def update_project(self, project: Project) -> Project:
        project.updated_at = datetime.now(timezone.utc).isoformat()
        self._save(project)
        return project

    def delete_project(self, project_id: str):
        with self._conn() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    # ── Subtask helpers ───────────────────────

    def add_subtask(self, project_id: str, title: str, due_date: Optional[str] = None, assigned_action: Optional[str] = None) -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        subtask = Subtask(
            id=str(uuid.uuid4())[:6],
            title=title,
            due_date=due_date,
            assigned_action=assigned_action,
        )
        p.subtasks.append(asdict(subtask))
        return self.update_project(p)

    def complete_subtask(self, project_id: str, subtask_id: str) -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        for st in p.subtasks:
            if st["id"] == subtask_id:
                st["status"] = "done"
                break
        p.advance_log.append(f"[{_now()}] Subtask '{subtask_id}' marked done.")
        return self.update_project(p)

    def block_subtask(self, project_id: str, subtask_id: str, reason: str) -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        for st in p.subtasks:
            if st["id"] == subtask_id:
                st["status"] = "blocked"
                st["notes"] = reason
                break
        p.advance_log.append(f"[{_now()}] Subtask '{subtask_id}' blocked: {reason}")
        return self.update_project(p)

    # ── Notes / contacts / emails ─────────────

    def add_note(self, project_id: str, note: str) -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        p.notes.append(f"[{_now()}] {note}")
        return self.update_project(p)

    def add_contact(self, project_id: str, name: str, email: str, role: str = "") -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        p.contacts.append({"name": name, "email": email, "role": role})
        return self.update_project(p)

    def link_email(self, project_id: str, email_id: str) -> Optional[Project]:
        p = self.get_project(project_id)
        if not p:
            return None
        if email_id not in p.linked_email_ids:
            p.linked_email_ids.append(email_id)
        return self.update_project(p)

    # ── Advancement ───────────────────────────

    def log_advance(self, project_id: str, action_taken: str) -> Optional[Project]:
        """Called after AIDE autonomously advances a task step."""
        p = self.get_project(project_id)
        if not p:
            return None
        p.last_advanced = _now()
        p.advance_log.append(f"[{_now()}] {action_taken}")
        return self.update_project(p)

    def get_next_pending_subtask(self, project_id: str) -> Optional[dict]:
        p = self.get_project(project_id)
        if not p:
            return None
        for st in p.subtasks:
            if st["status"] == "pending":
                return st
        return None

    def get_stale_projects(self, days_stale: int = 2) -> list[Project]:
        """Return active projects AIDE hasn't advanced in N days — for proactive nudges."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days_stale)).isoformat()
        projects = self.list_projects(status="active")
        stale = []
        for p in projects:
            last = p.last_advanced or p.created_at
            if last < cutoff:
                stale.append(p)
        return stale

    # ── Serialization ─────────────────────────

    def _save(self, project: Project):
        data = json.dumps(asdict(project))
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO projects (id, data, status, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     data=excluded.data,
                     status=excluded.status,
                     updated_at=excluded.updated_at""",
                (project.id, data, project.status, project.updated_at),
            )

    def _deserialize(self, data_str: str) -> Project:
        d = json.loads(data_str)
        return Project(**d)

    # ── Summary formatting (for Telegram) ─────

    def format_project_summary(self, project: Project) -> str:
        done = sum(1 for st in project.subtasks if st["status"] == "done")
        total = len(project.subtasks)
        blocked = sum(1 for st in project.subtasks if st["status"] == "blocked")
        progress = f"{done}/{total}" if total else "no tasks"

        deadline_str = ""
        if project.deadline:
            try:
                dl = datetime.fromisoformat(project.deadline)
                days_left = (dl - datetime.now(timezone.utc)).days
                deadline_str = f" · ⏰ {days_left}d left" if days_left >= 0 else " · 🔴 OVERDUE"
            except Exception:
                deadline_str = f" · due {project.deadline}"

        lines = [
            f"📁 *{project.title}*{deadline_str}",
            f"🎯 {project.goal}",
            f"📊 Progress: {progress}" + (f" · {blocked} blocked" if blocked else ""),
        ]

        if project.subtasks:
            lines.append("")
            for st in project.subtasks:
                icon = {"pending": "⬜", "in_progress": "🔄", "done": "✅", "blocked": "🚫"}.get(st["status"], "⬜")
                lines.append(f"{icon} {st['title']}")

        if project.contacts:
            names = ", ".join(c["name"] for c in project.contacts)
            lines.append(f"\n👥 {names}")

        if project.last_advanced:
            lines.append(f"\n🤖 Last advanced: {project.last_advanced[:10]}")

        return "\n".join(lines)

    def format_all_projects_brief(self) -> str:
        projects = self.list_projects(status="active")
        if not projects:
            return "📁 No active projects."
        lines = ["📁 *Active Projects*\n"]
        for p in projects:
            done = sum(1 for st in p.subtasks if st["status"] == "done")
            total = len(p.subtasks)
            lines.append(f"• *{p.title}* [{done}/{total}] — `{p.id}`")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")