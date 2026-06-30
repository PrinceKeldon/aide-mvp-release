"""
Sprint 5: ProjectTool
AIDE's ReAct tool for managing long-horizon projects.

AIDE uses this tool when the user says things like:
  "Create a project to launch my podcast by June"
  "What's the status of my Berlin project?"
  "Mark the logo subtask as done"
  "Add Sarah as a contact on the podcast project"
  "Advance the podcast project"
"""

import json
from typing import Optional
from tools.base import BaseTool, SafetyTier
from core.project_manager import ProjectManager
from core.settings import settings


class ProjectTool(BaseTool):
    """
    Unified project management tool.
    One tool, action-dispatch pattern — keeps the tools list short.
    """

    @property
    def name(self) -> str:
        return "manage_project"

    @property
    def description(self) -> str:
        return (
            "Create, update, and track long-horizon projects. "
            "Each project has a goal, subtasks, deadline, notes, contacts, and linked emails. "
            "Use this for anything that spans multiple steps or days. "
            "Input: JSON with 'action' and relevant fields."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    def __init__(self, project_manager: Optional[ProjectManager] = None, db_path: str | None = None):
        self.pm = project_manager or ProjectManager(db_path=db_path or str(settings.memory_db_path))

    async def execute(self, input_text) -> str:
        """Async wrapper — called by AIDE's ReAct loop."""
        try:
            if isinstance(input_text, dict):
                kwargs = input_text
            else:
                kwargs = json.loads(str(input_text))
        except Exception:
            return "Error: input must be JSON with an 'action' field."
        return self.run(**kwargs)

    def run(self, **kwargs) -> str:
        action = kwargs.get("action")

        try:
            if action == "create":
                return self._create(kwargs)
            elif action == "list":
                return self.pm.format_all_projects_brief()
            elif action == "status":
                return self._status(kwargs)
            elif action == "add_subtask":
                return self._add_subtask(kwargs)
            elif action == "complete_subtask":
                return self._complete_subtask(kwargs)
            elif action == "block_subtask":
                return self._block_subtask(kwargs)
            elif action == "add_note":
                return self._add_note(kwargs)
            elif action == "add_contact":
                return self._add_contact(kwargs)
            elif action == "link_email":
                return self._link_email(kwargs)
            elif action == "log_advance":
                return self._log_advance(kwargs)
            elif action == "next_step":
                return self._next_step(kwargs)
            elif action == "delete":
                return self._delete(kwargs)
            else:
                return f"Unknown action: {action}. Valid actions: create, list, status, add_subtask, complete_subtask, block_subtask, add_note, add_contact, link_email, log_advance, next_step, delete"
        except Exception as e:
            return f"ProjectTool error: {e}"

    # ── Action handlers ────────────────────────

    def _create(self, kw: dict) -> str:
        title = kw.get("title")
        goal = kw.get("goal")
        if not title or not goal:
            return "Error: title and goal are required to create a project."
        p = self.pm.create_project(
            title=title,
            goal=goal,
            deadline=kw.get("deadline"),
            contacts=kw.get("contacts"),
        )
        return f"Project created: {p.title} (`{p.id}`)\nGoal: {p.goal}"

    def _status(self, kw: dict) -> str:
        pid = kw.get("project_id")
        if not pid:
            return "Error: project_id required."
        p = self.pm.get_project(pid)
        if not p:
            return f"No project found with ID {pid}."
        return self.pm.format_project_summary(p)

    def _add_subtask(self, kw: dict) -> str:
        pid = kw.get("project_id")
        title = kw.get("subtask_title")
        if not pid or not title:
            return "Error: project_id and subtask_title required."
        p = self.pm.add_subtask(
            pid,
            title=title,
            due_date=kw.get("subtask_due"),
            assigned_action=kw.get("subtask_action"),
        )
        if not p:
            return f"Project {pid} not found."
        return f"Subtask added to {p.title}: {title}"

    def _complete_subtask(self, kw: dict) -> str:
        pid = kw.get("project_id")
        sid = kw.get("subtask_id")
        if not pid or not sid:
            return "Error: project_id and subtask_id required."
        p = self.pm.complete_subtask(pid, sid)
        if not p:
            return "Project or subtask not found."
        return "Subtask marked done."

    def _block_subtask(self, kw: dict) -> str:
        pid = kw.get("project_id")
        sid = kw.get("subtask_id")
        reason = kw.get("block_reason", "No reason given")
        if not pid or not sid:
            return "Error: project_id and subtask_id required."
        self.pm.block_subtask(pid, sid, reason)
        return f"Subtask blocked: {reason}"

    def _add_note(self, kw: dict) -> str:
        pid = kw.get("project_id")
        note = kw.get("note")
        if not pid or not note:
            return "Error: project_id and note required."
        p = self.pm.add_note(pid, note)
        if not p:
            return f"Project {pid} not found."
        return f"Note added to {p.title}."

    def _add_contact(self, kw: dict) -> str:
        pid = kw.get("project_id")
        name = kw.get("contact_name")
        if not pid or not name:
            return "Error: project_id and contact_name required."
        p = self.pm.add_contact(
            pid,
            name=name,
            email=kw.get("contact_email", ""),
            role=kw.get("contact_role", ""),
        )
        if not p:
            return f"Project {pid} not found."
        return f"Contact added: {name} to {p.title}"

    def _link_email(self, kw: dict) -> str:
        pid = kw.get("project_id")
        email_id = kw.get("email_id")
        if not pid or not email_id:
            return "Error: project_id and email_id required."
        p = self.pm.link_email(pid, email_id)
        if not p:
            return f"Project {pid} not found."
        return f"Email thread linked to {p.title}."

    def _log_advance(self, kw: dict) -> str:
        pid = kw.get("project_id")
        note = kw.get("note", "Advanced by AIDE.")
        if not pid:
            return "Error: project_id required."
        p = self.pm.log_advance(pid, note)
        if not p:
            return f"Project {pid} not found."
        return f"Advance logged: {note}"

    def _next_step(self, kw: dict) -> str:
        pid = kw.get("project_id")
        if not pid:
            return "Error: project_id required."
        st = self.pm.get_next_pending_subtask(pid)
        if not st:
            p = self.pm.get_project(pid)
            title = p.title if p else pid
            return f"No pending subtasks in {title} — all done or blocked."
        action_hint = f"\nAssigned action: {st['assigned_action']}" if st.get("assigned_action") else ""
        due = f" (due {st['due_date']})" if st.get("due_date") else ""
        return f"Next step: {st['title']}{due}{action_hint}\nSubtask ID: {st['id']}"

    def _delete(self, kw: dict) -> str:
        pid = kw.get("project_id")
        if not pid:
            return "Error: project_id required."
        p = self.pm.get_project(pid)
        if not p:
            return f"Project {pid} not found."
        self.pm.delete_project(pid)
        return f"Project {p.title} deleted."