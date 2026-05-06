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

    def __init__(
        self,
        memory: MemoryManager,
        llm,
        tools: dict,
        corrector: SelfCorrector = None,
        coordinator=None,
    ) -> None:
        self._memory = memory
        self._llm = llm
        self._tools = tools
        self._corrector = corrector or SelfCorrector()
        self._coordinator = coordinator
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

        planning_prompt = f"""Break this request into clear sequential steps for an AI agent.

Request: {user_request}

Respond with ONLY a JSON array of steps. Each step must have:
- "description": what this step does (plain English)
- "tool": one of ["tavily_search", "web_search", "llm", "memory"]
- "input": the exact query or instruction for this step
- "requires_internet": true or false

Rules:
- tavily_search and web_search require internet (set requires_internet: true)
- llm and memory do NOT require internet (set requires_internet: false)
- Maximum 5 steps
- For llm steps that use previous results, use {{previous_result}} as placeholder
- Use "llm" for summarising, writing, reasoning, drafting
- Use "tavily_search" for finding current information

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

        for i, step in enumerate(task.steps):
            # Proactive status update every 30s
            if notify_callback:
                now = datetime.utcnow()
                elapsed = (now - last_notification_time).total_seconds()
                if elapsed > 30:
                    logger.info(
                        f"Still working: {step.description[:60]} — {i+1}/{len(task.steps)} steps done"
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
                if step.tool in ("tavily_search", "web_search"):
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
                    from core.router import TaskType

                    task_type = self._llm.classify_task(step_input)
                    llm_result = await self._llm.chat(
                        messages=[{"role": "user", "content": step_input}],
                        tools=None,
                        temperature=0.3,
                        task_type=task_type,
                    )
                    result = llm_result.get("content", "")
                    if not result:
                        raise RuntimeError("LLM returned empty result")
                elif step.tool == "memory":
                    result = await self._memory.recall(step_input)
                    if not result:
                        result = "No relevant memory found"
                else:
                    result = f"Unknown tool: {step.tool}"

                step.result = result
                step.status = StepStatus.COMPLETE
                step.completed_at = datetime.utcnow().isoformat()
                previous_result = result

                logger.info(f"Step {i+1} complete: {step.description[:40]}")

                # Notify progress
                if notify_callback:
                    logger.info(
                        f"Step {i+1}/{len(task.steps)} done: {step.description}"
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

        # Final summary from last completed step
        completed_steps = [s for s in task.steps if s.status == StepStatus.COMPLETE]

        if completed_steps:
            last_result = completed_steps[-1].result
            # If last step was a search, summarise it
            if completed_steps[-1].tool in ("tavily_search", "web_search"):
                summary_result = await self._llm.chat(
                    messages=[
                        {
                            "role": "user",
                            "content": f"Summarise this clearly in plain conversational text. No markdown, no bold, no bullet points, no headers. Just clean paragraphs:\n{last_result}",
                        }
                    ],
                    tools=None,
                    temperature=0.1,
                )
                task.final_reply = summary_result.get("content", last_result)
            else:
                task.final_reply = last_result
        else:
            task.final_reply = "I could not complete this task. Please try again."

        task.status = TaskStatus.COMPLETE
        task.completed_at = datetime.utcnow().isoformat()
        self._save_task(task)
        self._active_tasks.pop(task.id, None)

        logger.info(f"Task {task.id} complete")
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
            "find and",
            "search and",
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
