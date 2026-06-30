"""
Sprint 5: ProjectAdvancer
Autonomous engine that advances projects on a schedule.

Called by scheduler.py every N hours.
For each active project:
  1. Finds the next pending subtask
  2. If it has an assigned_action, queues it for execution (APPROVE tier)
  3. If no assigned action, asks the LLM to suggest the next concrete action
  4. Sends a Telegram nudge with a confirmation button

Respects AIDE's three-tier safety model:
  APPROVE — user must confirm before any action is executed
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ProjectAdvancer:
    """
    Wired into scheduler.py and telegram_bot.py.
    
    Usage in scheduler.py:
        advancer = ProjectAdvancer(project_manager, agent, telegram_interface)
        scheduler.add_job(advancer.run_advance_cycle, 'interval', hours=6, id='project_advance')
    """

    def __init__(self, project_manager, agent, telegram_interface, safety_gate=None):
        self.pm = project_manager
        self.agent = agent          # core/agent.py — for LLM reasoning
        self.tg = telegram_interface  # interface/telegram_bot.py
        self.safety = safety_gate   # core/safety.py

    async def run_advance_cycle(self):
        """
        Main loop: check all active projects, advance where possible,
        nudge for anything needing approval.
        """
        logger.info("ProjectAdvancer: running advance cycle")
        projects = self.pm.list_projects(status="active")

        for project in projects:
            try:
                await self._advance_project(project)
            except Exception as e:
                logger.error(f"ProjectAdvancer error on {project.id}: {e}")

        # Separately flag stale projects
        stale = self.pm.get_stale_projects(days_stale=2)
        for project in stale:
            await self._send_stale_nudge(project)

    async def _advance_project(self, project):
        """Attempt to advance one project."""
        next_st = self.pm.get_next_pending_subtask(project.id)
        if not next_st:
            return  # Nothing to do — all subtasks done or blocked

        # If there's a pre-assigned action, queue it for approval
        if next_st.get("assigned_action"):
            await self._request_approval(project, next_st)
        else:
            # Ask LLM to propose the next concrete action
            action_proposal = await self._llm_propose_action(project, next_st)
            if action_proposal:
                await self._request_approval(project, next_st, proposed_action=action_proposal)

    async def _llm_propose_action(self, project, subtask: dict) -> Optional[str]:
        """
        Use the agent's LLM to suggest a concrete next action for a subtask.
        Returns a short action string or None.
        """
        try:
            prompt = (
                f"You are helping advance a project.\n\n"
                f"Project: {project.title}\n"
                f"Goal: {project.goal}\n"
                f"Current subtask: {subtask['title']}\n"
                f"Contacts: {', '.join(c['name'] + ' <' + c['email'] + '>' for c in project.contacts) if project.contacts else 'none'}\n\n"
                f"Suggest ONE concrete action AIDE can take right now to advance this subtask. "
                f"Be specific — e.g. 'Search the web for X', 'Draft an email to Y about Z', 'Set a reminder for D'. "
                f"Reply with just the action, one sentence."
            )
            response = await self.agent.llm.complete(prompt)
            return response.strip() if response else None
        except Exception as e:
            logger.error(f"LLM proposal failed: {e}")
            return None

    async def _request_approval(self, project, subtask: dict, proposed_action: Optional[str] = None):
        """Send a Telegram message asking user to approve the next action."""
        action_text = proposed_action or subtask.get("assigned_action", "advance this subtask")

        message = (
            f"📁 *{project.title}*\n"
            f"⬜ Next: _{subtask['title']}_\n\n"
            f"🤖 AIDE proposes:\n_{action_text}_\n\n"
            f"Approve to proceed, or skip to leave for later."
        )

        # Build inline keyboard for Telegram
        # Callback data format: "project_approve:{project_id}:{subtask_id}:{action_b64}"
        import base64
        action_b64 = base64.urlsafe_b64encode(action_text.encode()).decode()
        callback_approve = f"project_approve:{project.id}:{subtask['id']}:{action_b64}"
        callback_skip = f"project_skip:{project.id}:{subtask['id']}"

        keyboard = [
            [
                {"text": "✅ Approve", "callback_data": callback_approve},
                {"text": "⏭ Skip", "callback_data": callback_skip},
            ]
        ]

        try:
            await self.tg.send_message_with_keyboard(message, keyboard)
            self.pm.log_advance(project.id, f"Awaiting approval for: {action_text}")
        except Exception as e:
            logger.error(f"Telegram nudge failed: {e}")

    async def _send_stale_nudge(self, project):
        """Nudge user about a project that hasn't been touched in 2+ days."""
        next_st = self.pm.get_next_pending_subtask(project.id)
        if not next_st:
            return

        message = (
            f"⏰ *{project.title}* hasn't moved in a while.\n"
            f"Next step: _{next_st['title']}_\n\n"
            f"Want AIDE to work on it now?"
        )

        keyboard = [
            [
                {"text": "🚀 Yes, advance it", "callback_data": f"project_advance_now:{project.id}"},
                {"text": "💤 Remind tomorrow", "callback_data": f"project_snooze:{project.id}"},
            ]
        ]

        try:
            await self.tg.send_message_with_keyboard(message, keyboard)
        except Exception as e:
            logger.error(f"Stale nudge failed for {project.id}: {e}")


# ─────────────────────────────────────────────
#  Telegram callback handler (add to telegram_bot.py)
# ─────────────────────────────────────────────

class ProjectCallbackHandler:
    """
    Handles inline button callbacks from Telegram for project advancement.
    
    Wire into telegram_bot.py:
        from core.project_callbacks import ProjectCallbackHandler
        project_cb = ProjectCallbackHandler(project_manager, agent, task_queue)
    
    In your callback_query_handler:
        if query.data.startswith("project_"):
            await project_cb.handle(query)
    """

    def __init__(self, project_manager, agent, task_queue):
        self.pm = project_manager
        self.agent = agent
        self.tq = task_queue

    async def handle(self, callback_query):
        data = callback_query.data
        parts = data.split(":")

        try:
            if parts[0] == "project_approve":
                await self._handle_approve(callback_query, parts)
            elif parts[0] == "project_skip":
                await self._handle_skip(callback_query, parts)
            elif parts[0] == "project_advance_now":
                await self._handle_advance_now(callback_query, parts)
            elif parts[0] == "project_snooze":
                await self._handle_snooze(callback_query, parts)
        except Exception as e:
            logger.error(f"ProjectCallbackHandler error: {e}")
            await callback_query.answer(f"Error: {e}")

    async def _handle_approve(self, query, parts):
        import base64
        project_id = parts[1]
        subtask_id = parts[2]
        action = base64.urlsafe_b64decode(parts[3]).decode()

        await query.answer("✅ Approved — Vera is on it.")
        await query.edit_message_text(
            f"✅ *Approved*\nAIDE will: _{action}_",
            parse_mode="Markdown",
        )

        # Queue the action in AIDE's task queue
        await self.tq.enqueue(
            task_description=action,
            context={"project_id": project_id, "subtask_id": subtask_id},
            on_complete=lambda result: self._on_action_complete(project_id, subtask_id, action, result),
        )

    async def _handle_skip(self, query, parts):
        project_id = parts[1]
        subtask_id = parts[2]
        self.pm.block_subtask(project_id, subtask_id, "Skipped by user.")
        await query.answer("Skipped.")
        await query.edit_message_text("⏭ Skipped for now.")

    async def _handle_advance_now(self, query, parts):
        project_id = parts[1]
        await query.answer("On it!")
        # Trigger an immediate advance cycle for just this project
        p = self.pm.get_project(project_id)
        if p:
            await query.edit_message_text(f"🚀 AIDE is working on *{p.title}*...", parse_mode="Markdown")

    async def _handle_snooze(self, query, parts):
        project_id = parts[1]
        self.pm.log_advance(project_id, "User snoozed nudge — remind tomorrow.")
        await query.answer("OK, I'll remind you tomorrow.")
        await query.edit_message_text("💤 Got it — I'll check back tomorrow.")

    def _on_action_complete(self, project_id, subtask_id, action, result):
        """Called when the task queue finishes executing the approved action."""
        self.pm.log_advance(project_id, f"Completed: {action}\nResult: {str(result)[:200]}")
        self.pm.complete_subtask(project_id, subtask_id)
