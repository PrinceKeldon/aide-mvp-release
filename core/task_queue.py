"""
AIDE -- Task Queue (Sprint 2a)
Turns AIDE from a chatbot into an autonomous agent.

A task is a complex request broken into ordered steps.
Each step knows:
  - what to do
  - which tool to use
  - whether it needs internet
  - the result when done

Tasks survive restarts -- stored in SQLite.
Steps run in sequence -- output of one feeds input to next.
Offline steps run immediately. Online steps queue if no internet.
"""

import asyncio
import json
import uuid
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field, asdict
from loguru import logger
from memory.manager import MemoryManager
from core.router import TaskType
from core.safety import PendingAction, SafetyTier as GateSafetyTier
from core.settings import settings
from core.self_corrector import SelfCorrector, RecoveryAction
from mesh.checkpoint import TaskCheckpoint, StepCheckpoint


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    QUEUED = "queued"  # waiting for internet
    REPLANNING = "replanning"
    HANDED_OFF = "handed_off"
    RECOVERED = "recovered"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Step:
    id: str
    description: str
    tool: str  # "tavily_search" | "web_search" | "llm" | "memory"
    input: str  # query or instruction
    requires_internet: bool = False
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    started_at: str = ""
    completed_at: str = ""


@dataclass
class Task:
    id: str
    description: str  # original user request
    steps: list[Step]
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = ""
    completed_at: str = ""
    final_reply: str = ""


class TaskQueue:
    """
    Manages the full task lifecycle:
    plan → queue → execute → report
    """

    INTERNAL_AUTONOMOUS_TOOLS = {"llm", "memory"}
    SAFE_AUTONOMOUS_TOOLS = {
        "tavily_search",
        "web_search",
        "brave_search",
        "browse_url",
        "read_email",
        "search_email",
        "get_monthly_overview",
        "get_project_finance",
        "generate_finance_report",
    }
    APPROVAL_REQUIRED_PREFIXES = (
        "send_email",
        "reply_email",
        "calendar_write",
        "purchase",
        "file_write",
        "browser_submit",
        "submit_form",
        "fill_form",
        "send_message",
        "mesh_send",
        "mesh_send_pii",
        "delegate_mesh_task",
    )

    def __init__(
        self,
        memory: MemoryManager,
        llm,
        tools: dict,
        corrector: SelfCorrector = None,
        coordinator=None,
        safety_gate=None,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._tools = tools
        self._corrector = corrector or SelfCorrector()
        self._coordinator = coordinator
        self._safety = safety_gate
        self._active_tasks: dict[str, Task] = {}
        self._init_db()

    def _init_db(self) -> None:
        with self._memory._get_db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    steps TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    final_reply TEXT
                );
            """)
        logger.info("Task queue DB ready")

    async def plan(self, user_request: str) -> Task:
        """
        Ask the LLM to break a complex request into steps.
        Returns a Task ready to execute.
        """
        logger.info(f"Planning task: {user_request[:60]!r}")
        safe_tools = sorted(
            self.INTERNAL_AUTONOMOUS_TOOLS
            | (self.SAFE_AUTONOMOUS_TOOLS & set(self._tools.keys()))
        )
        approval_tools = sorted(
            name
            for name in self._tools
            if self._tool_safety_tier(name) == GateSafetyTier.APPROVE
        )
        tool_names = safe_tools + [
            name for name in approval_tools if name not in safe_tools
        ]

        planning_prompt = f"""Break this request into clear sequential steps for an AI agent.

Request: {user_request}

Respond with ONLY a JSON array of steps. Each step must have:
- "description": what this step does (plain English)
- "tool": one of {json.dumps(tool_names)}
- "input": the exact query or instruction for this step
- "requires_internet": true or false

Rules:
- Safe autonomous tools may run without approval: {", ".join(safe_tools)}
- Approval-required tools must only be used when the user explicitly requested the side effect: {", ".join(approval_tools) if approval_tools else "none available"}
- Search and browsing tools require internet (set requires_internet: true)
- llm, memory, read_email, and search_email do NOT require internet unless their configured backend needs it
- Maximum 5 steps
- For llm steps that use previous results, use {{previous_result}} as placeholder
- Use "llm" for summarising, writing, reasoning, drafting
- Use search tools for finding current information
- Do not stop after planning. The final step should produce the completed response or report.

Example:
[
  {{"description": "Search for topic", "tool": "tavily_search", "input": "topic query here", "requires_internet": true}},
  {{"description": "Summarise findings", "tool": "llm", "input": "Summarise in plain text:\\n{{previous_result}}", "requires_internet": false}},
  {{"description": "Write report", "tool": "llm", "input": "Write a report based on:\\n{{previous_result}}", "requires_internet": false}}
]

JSON only, no explanation:"""

        result = await self._llm.chat(
            messages=[{"role": "user", "content": planning_prompt}],
            tools=None,
            temperature=0.1,
            task_type=TaskType.REASONING,
            max_tokens=1024,
        )

        content = result.get("content", "")

        # Parse the steps JSON
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Extract JSON array from response
                import re

                match = re.search(r"\[.*\]", content, re.DOTALL)
                if match:
                    steps_data = json.loads(match.group())
                else:
                    steps_data = json.loads(content)
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.warning(
                        f"Could not parse plan after {max_retries} attempts: {e} — using single step"
                    )
                    steps_data = [
                        {
                            "description": user_request,
                            "tool": "tavily_search",
                            "input": user_request,
                            "requires_internet": True,
                        }
                    ]
                else:
                    logger.info(
                        f"Plan parse failed (attempt {attempt+1}), asking LLM to fix JSON..."
                    )
                    fix_prompt = f"Your previous response was not valid JSON. Please return ONLY the JSON array of steps. Error: {e}\n\nPrevious response:\n{content}"
                    result = await self._llm.chat(
                        messages=[{"role": "user", "content": fix_prompt}],
                        tools=None,
                        temperature=0.1,
                        task_type=TaskType.REASONING,
                        max_tokens=1024,
                    )
                    content = result.get("content", "")

        steps = [
            Step(
                id=str(uuid.uuid4())[:8],
                description=s.get("description", ""),
                tool=s.get("tool", "llm"),
                input=s.get("input", ""),
                requires_internet=s.get("requires_internet", False),
            )
            for s in steps_data[:5]  # max 5 steps
        ]

        task = Task(
            id=str(uuid.uuid4())[:8],
            description=user_request,
            steps=steps,
            created_at=datetime.utcnow().isoformat(),
        )

        self._save_task(task)
        logger.info(f"Task {task.id} planned with {len(steps)} steps")
        return task

    def _tool_safety_tier(self, tool_name: str) -> GateSafetyTier:
        if tool_name in self.INTERNAL_AUTONOMOUS_TOOLS:
            return GateSafetyTier.AUTONOMOUS
        if tool_name in self.SAFE_AUTONOMOUS_TOOLS:
            return GateSafetyTier.AUTONOMOUS
        if any(
            tool_name == prefix or tool_name.startswith(prefix)
            for prefix in self.APPROVAL_REQUIRED_PREFIXES
        ):
            return GateSafetyTier.APPROVE

        tool = self._tools.get(tool_name)
        if getattr(tool, "requires_confirmation", False):
            return GateSafetyTier.APPROVE

        if self._safety is not None:
            tier = self._safety.classify(tool_name)
            if isinstance(tier, GateSafetyTier):
                return tier
            return GateSafetyTier(str(tier))

        tool_tier = getattr(tool, "safety_tier", None)
        if tool_tier is not None:
            value = getattr(tool_tier, "value", tool_tier)
            if value in {tier.value for tier in GateSafetyTier}:
                return GateSafetyTier(value)

        return GateSafetyTier.APPROVE

    def _step_safety_tier(self, tool_name: str, step_input: str) -> GateSafetyTier:
        if tool_name == "google_calendar":
            try:
                data = (
                    step_input
                    if isinstance(step_input, dict)
                    else json.loads(str(step_input or "{}"))
                )
            except Exception:
                data = {}
            action = str(data.get("action", "")).strip().lower()
            if action == "list":
                return GateSafetyTier.AUTONOMOUS
            return GateSafetyTier.APPROVE

        if tool_name == "obsidian_vault":
            try:
                data = (
                    step_input
                    if isinstance(step_input, dict)
                    else json.loads(str(step_input or "{}"))
                )
            except Exception:
                data = {}
            action = str(data.get("action", "")).strip().lower()
            if action in {"list", "search", "read"}:
                return GateSafetyTier.AUTONOMOUS
            return GateSafetyTier.APPROVE

        return self._tool_safety_tier(tool_name)

    async def _execute_registered_tool(self, tool_name: str, step_input: str) -> str:
        tool = self._tools.get(tool_name)
        if tool is None:
            return f"Unknown tool: {tool_name}"

        async def executor():
            payload = step_input
            if tool_name == "google_calendar" and isinstance(step_input, str):
                try:
                    payload = json.loads(step_input)
                except Exception:
                    payload = {"action": step_input}
            result = tool.execute(payload)
            if asyncio.iscoroutine(result):
                return await result
            return result

        tier = self._step_safety_tier(tool_name, step_input)
        if self._safety is None:
            if tier == GateSafetyTier.APPROVE:
                return (
                    f"Approval required before running {tool_name}. "
                    "This task step was not executed."
                )
            return await executor()

        action = PendingAction(
            action_id="",
            tool_name=tool_name,
            description=f"Run workflow step with {tool_name}",
            tier=tier,
            payload={"input": step_input},
        )
        return await self._safety.process(action, executor)

    async def _notify_progress(self, notify_callback, message: str) -> None:
        if not notify_callback:
            return
        try:
            result = notify_callback(message)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.warning(f"Task progress notification failed: {exc}")

    async def _refine_search_query(self, user_request: str, raw_query: str) -> str:
        """Refine a raw tool input into a concise, keyword-based search query."""
        if len(raw_query) < 20:
            return raw_query

        refine_prompt = f"""Refine the following search query into a concise, keyword-based string for a web search engine.
Remove conversational filler, pronouns, and politeness. Keep only the core factual search terms.

Original Request: {user_request}
Raw Query: {raw_query}

Refined Query:"""

        result = await self._llm.chat(
            messages=[{"role": "user", "content": refine_prompt}],
            tools=None,
            temperature=0.1,
        )
        return result.get("content", raw_query).strip().strip("\"'")

    async def execute(self, task: Task, notify_callback=None) -> str:
        """
        Execute all steps in sequence.
        Passes output of each step as input to the next.
        Returns final reply for the user.
        """
        task.status = TaskStatus.RUNNING
        self._active_tasks[task.id] = task
        self._save_task(task)

        previous_result = ""
        online = await self._check_internet()
        start_time = datetime.utcnow()
        last_notification_time = start_time
        await self._notify_progress(
            notify_callback,
            f"Started task: {task.description[:120]}",
        )

        for i, step in enumerate(task.steps):
            # Proactive status update every 30s
            if notify_callback:
                now = datetime.utcnow()
                elapsed = (now - last_notification_time).total_seconds()
                if elapsed > 30:
                    await self._notify_progress(
                        notify_callback,
                        f"Still working: step {i+1}/{len(task.steps)} — {step.description[:120]}",
                    )
                    last_notification_time = now

            # Skip internet steps if offline
            if step.requires_internet and not online:
                step.status = StepStatus.SKIPPED
                logger.warning(f"Step {i+1} skipped — no internet: {step.description}")
                continue
            step.status = StepStatus.RUNNING
            step.started_at = datetime.utcnow().isoformat()
            logger.info(f"Executing step {i+1}/{len(task.steps)}: {step.description}")

            # Inject previous result into input
            # Inject results from previous steps
            step_input = step.input
            step_input = step_input.replace("{previous_result}", previous_result)
            # Also replace any {stepN_result} placeholders
            for j, completed_step in enumerate(task.steps[:i], 1):
                if completed_step.result:
                    step_input = step_input.replace(
                        f"{{step{j}_result}}", completed_step.result[:500]
                    )

            try:
                if step.tool in ("tavily_search", "web_search", "brave_search", "browse_url"):
                    # Refine query before searching to avoid 400 errors and improve results
                    refined_input = await self._refine_search_query(
                        task.description, step_input
                    )
                    logger.info(f"Refined search query: {refined_input!r}")

                    tool = (
                        self._tools.get(step.tool)
                        or self._tools.get("tavily_search")
                        or self._tools.get("web_search")
                    )
                    if tool:
                        result = await tool.execute(refined_input)
                    else:
                        # --- Sprint 6: Handoff Trigger (Capability Gap) ---
                        if self._coordinator:
                            logger.info(
                                f"Tool {step.tool} missing. Attempting handoff..."
                            )
                            checkpoint = self.create_checkpoint(
                                task,
                                working_context={},
                                reason=f"Missing tool: {step.tool}",
                                created_by=self._coordinator._node.identity.device_id,
                            )
                            # Try to find a capable peer. For now, we'll just try the first available peer
                            # or a specific target if we had one.
                            peer_id = None
                            if self._coordinator._peer_registry:
                                peers = self._coordinator._peer_registry.list_all()
                                if peers:
                                    peer_id = peers[0]["device_id"]

                            if peer_id:
                                status = await self._coordinator.send_handoff(
                                    peer_id, checkpoint
                                )
                                if status == "accepted":
                                    task.status = TaskStatus.HANDED_OFF
                                    self._save_task(task)
                                    return f"Task handed off to peer {peer_id} due to missing tool {step.tool}."

                            result = "Search tool not available"

                    # Result Validation: Don't mark as complete if it's an error or empty
                    if not result or any(
                        x in str(result).lower()
                        for x in [
                            "error",
                            "failed",
                            "400 bad request",
                            "no results found",
                        ]
                    ):
                        raise RuntimeError(
                            f"Search tool returned invalid result: {result}"
                        )

                elif step.tool == "llm":
                    task_type = (
                        TaskType.RESEARCH
                        if any(
                            completed.tool in ("tavily_search", "web_search")
                            for completed in task.steps[:i]
                        )
                        else self._llm.classify_task(step_input)
                    )
                    llm_result = await self._llm.chat(
                        messages=[{"role": "user", "content": step_input}],
                        tools=None,
                        temperature=0.3,
                        task_type=task_type,
                        max_tokens=settings.ollama_reasoning_max_tokens,
                    )
                    result = llm_result.get("content", "")
                    if not result:
                        raise RuntimeError("LLM returned empty result")
                elif step.tool == "memory":
                    result = await self._memory.recall(step_input)
                    if not result:
                        result = "No relevant memory found"
                else:
                    result = await self._execute_registered_tool(step.tool, step_input)
                    if (
                        self._step_safety_tier(step.tool, step_input)
                        == GateSafetyTier.APPROVE
                        and str(result).strip().lower()
                        in {
                            "action cancelled.",
                            "action timed out waiting for your approval.",
                        }
                    ):
                        raise RuntimeError(result)

                step.result = result
                step.status = StepStatus.COMPLETE
                step.completed_at = datetime.utcnow().isoformat()
                previous_result = result

                logger.info(f"Step {i+1} complete: {step.description[:40]}")

                # Notify progress
                if notify_callback:
                    await self._notify_progress(
                        notify_callback,
                        f"Step {i+1}/{len(task.steps)} done: {step.description[:120]}",
                    )

            except Exception as e:
                # --- Sprint 6: Step Failure Recovery ---
                logger.error(f"Step {i+1} failed: {e}. Attempting recovery...")

                recovery = await asyncio.to_thread(
                    self._corrector.handle_step_failure, step, e, self._tools, task
                )

                if recovery.should_replan:
                    task.status = TaskStatus.REPLANNING
                    self._save_task(task)
                    step.status = StepStatus.FAILED
                    step.result = f"Error: {e}. Recovery suggested replan."
                    logger.info(f"Recovery suggested replan for step {i+1}")
                elif recovery.alternative_tool:
                    logger.info(
                        f"Recovery suggests alternative tool: {recovery.alternative_tool}"
                    )
                    step.status = StepStatus.FAILED
                    step.result = f"Error: {e}. Alternative tool suggested: {recovery.alternative_tool}"
                else:
                    step.status = StepStatus.FAILED
                    step.result = f"Error: {e}"
                # ---------------------------------------

        # Final response synthesized from every completed step.
        completed_steps = [s for s in task.steps if s.status == StepStatus.COMPLETE]

        if completed_steps:
            step_summaries = []
            for idx, step in enumerate(task.steps, 1):
                result = (step.result or "").strip()
                if len(result) > 3500:
                    result = result[:3500].rsplit(" ", 1)[0] + "..."
                step_summaries.append(
                    f"Step {idx}: {step.description}\n"
                    f"Tool: {step.tool}\n"
                    f"Status: {step.status.value}\n"
                    f"Result:\n{result or 'No result'}"
                )
            final_prompt = (
                "You are finishing an autonomous task for the user. "
                "Do not describe a future plan. Do not say you will research or write later. "
                "Use the step results below to produce the completed answer now. "
                "If some steps failed, state the limitation briefly and still provide the best useful deliverable. "
                "Use plain text with clean paragraphs. No markdown, no bold, no asterisks.\n\n"
                f"Original user request:\n{task.description}\n\n"
                f"Completed workflow:\n\n{chr(10).join(step_summaries)}\n\n"
                "Final completed response:"
            )
            final_task_type = (
                TaskType.RESEARCH
                if any(s.tool in ("tavily_search", "web_search") for s in completed_steps)
                else TaskType.REASONING
            )
            summary_result = await self._llm.chat(
                messages=[{"role": "user", "content": final_prompt}],
                tools=None,
                temperature=0.1,
                task_type=final_task_type,
                max_tokens=settings.ollama_reasoning_max_tokens,
            )
            task.final_reply = (
                summary_result.get("content") or completed_steps[-1].result
            ).strip()
        else:
            task.final_reply = "I could not complete this task. Please try again."

        task.status = TaskStatus.COMPLETE
        task.completed_at = datetime.utcnow().isoformat()
        self._save_task(task)
        self._active_tasks.pop(task.id, None)

        logger.info(f"Task {task.id} complete")
        await self._notify_progress(
            notify_callback,
            f"Completed task: {task.description[:120]}",
        )
        return task.final_reply

    def _save_task(self, task: Task) -> None:
        with self._memory._get_db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tasks
                   (id, description, steps, status, created_at, completed_at, final_reply)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.id,
                    task.description,
                    json.dumps([asdict(s) for s in task.steps]),
                    task.status,
                    task.created_at,
                    task.completed_at,
                    task.final_reply,
                ),
            )

    def get_active_tasks(self) -> list[Task]:
        return list(self._active_tasks.values())

    async def _check_internet(self) -> bool:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3) as client:
                await client.get("https://1.1.1.1")
            return True
        except Exception:
            return False

    def is_complex_task(self, message: str) -> bool:
        msg = message.lower()
        complexity_signals = [
            " and write",
            " and then ",
            " and also ",
            " after that ",
            "research and",
            "research the",
            "research ",
            "look up ",
            "investigate ",
            "find out ",
            "find and",
            "search and",
            "search online",
            "online research",
            "provide a response",
            "come back with",
            "deliver a",
            "report on",
            "summarise and",
            "summarize and",
            "write a report",
            "write a summary",
            "write me",
            "draft a",
            "create a",
            "make a",
            "compare",
            "analyse",
            "analyze",
            "step by step",
            "then finally",
            "and summarise",
            "and summarize",
            "and create",
            "and draft",
            "then write",
            "then summarise",
            "then summarize",
            "followed by",
            "after finding",
            "after searching",
        ]
        return any(s in msg for s in complexity_signals)

    def create_checkpoint(
        self, task: Task, working_context: dict, reason: str, created_by: str
    ) -> TaskCheckpoint:
        """Creates a serialisable snapshot of the current task state."""
        completed = [
            StepCheckpoint(
                description=s.description,
                tool=s.tool,
                result=s.result,
                completed_at=s.completed_at,
            )
            for s in task.steps
            if s.status == StepStatus.COMPLETE
        ]
        remaining = [
            {"description": s.description, "tool": s.tool, "input": s.input}
            for s in task.steps
            if s.status != StepStatus.COMPLETE
        ]
        return TaskCheckpoint(
            task_id=task.id,
            original_goal=task.description,
            completed_steps=completed,
            remaining_steps=remaining,
            working_context=working_context,
            created_at=datetime.utcnow().isoformat(),
            created_by=created_by,
            reason=reason,
        )

    def update_status(self, task_id: str, status: TaskStatus, result: str = None):
        """Updates the status of a task in the DB."""
        with self._memory._get_db() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, final_reply = ? WHERE id = ?",
                (status.value, result, task_id),
            )
        # Also update in memory if active
        if task_id in self._active_tasks:
            self._active_tasks[task_id].status = status
            if result:
                self._active_tasks[task_id].final_reply = result
