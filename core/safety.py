"""
AIDE — safety gate
Every action the agent wants to take passes through here first.

Three tiers — set once in plain language at onboarding:
  autonomous  — agent acts silently, logs it, you can review anytime
  notify      — agent acts AND tells you immediately
  approve     — agent waits for your one-tap approval before acting
"""
import asyncio
import inspect
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from loguru import logger
from pydantic import BaseModel
from memory.manager import MemoryManager
from core.settings import settings


class SafetyTier(str, Enum):
    AUTONOMOUS = "autonomous"
    NOTIFY     = "notify"
    APPROVE    = "approve"


class PendingAction(BaseModel):
    action_id: str
    tool_name: str
    description: str
    tier: SafetyTier
    reversible: bool = True
    payload: dict = {}


DEFAULT_TIER_RULES: list[tuple[str, SafetyTier]] = [
    ("web_search",      SafetyTier.AUTONOMOUS),
    ("recall_memory",   SafetyTier.AUTONOMOUS),
    ("send_message",    SafetyTier.NOTIFY),
    ("calendar_read",   SafetyTier.NOTIFY),
    ("mesh_coordinate", SafetyTier.NOTIFY),
    ("send_email",      SafetyTier.APPROVE),
    ("calendar_write",  SafetyTier.APPROVE),
    ("purchase",        SafetyTier.APPROVE),
    ("file_write",      SafetyTier.APPROVE),
    ("mesh_send_pii",   SafetyTier.APPROVE),
]


class SafetyGate:
    """
    Classifies every pending action into a tier and either:
    - executes it (autonomous)
    - executes it and notifies (notify)
    - queues it and requests approval (approve)
    """

    def __init__(self, memory: MemoryManager) -> None:
        self._memory = memory
        self._pending: dict[str, PendingAction] = {}
        self._approval_callbacks: dict[str, asyncio.Future] = {}
        self._approval_runner_tasks: dict[str, asyncio.Task] = {}
        self._notify_callback = None
        self._approval_request_handler = None
        self._approval_state_handler = None
        self._executor_builder = None
        self._stopped = False
        self._suppress_notify_depth = 0
        self._approval_timeout_seconds = settings.approval_timeout_seconds
        self._restore_persisted_pending()

    def register_notify_callback(self, callback) -> None:
        self._notify_callback = callback

    def register_approval_request_handler(self, callback) -> None:
        self._approval_request_handler = callback

    def register_approval_state_handler(self, callback) -> None:
        self._approval_state_handler = callback

    def register_executor_builder(self, callback) -> None:
        self._executor_builder = callback

    def classify(self, tool_name: str) -> SafetyTier:
        user_pref = self._memory.get_fact(f"safety_tier_{tool_name}")
        if user_pref and user_pref in SafetyTier.__members__.values():
            return SafetyTier(user_pref)

        for rule_tool, tier in DEFAULT_TIER_RULES:
            if tool_name == rule_tool or tool_name.startswith(rule_tool):
                return tier

        from core.settings import settings
        return SafetyTier(settings.default_safety_tier)

    @contextmanager
    def suppress_notify(self):
        self._suppress_notify_depth += 1
        try:
            yield
        finally:
            self._suppress_notify_depth = max(0, self._suppress_notify_depth - 1)

    async def process(self, action: PendingAction, executor) -> str:
        if self._stopped:
            return "All actions paused. Send /resume to continue."

        tier = action.tier

        if tier == SafetyTier.AUTONOMOUS:
            result = await executor()
            self._memory.log_action(
                action=action.description,
                tier=tier.value,
                outcome=result[:200],
            )
            return result

        elif tier == SafetyTier.NOTIFY:
            result = await executor()
            self._memory.log_action(
                action=action.description,
                tier=tier.value,
                outcome=result[:200],
            )
            if self._notify_callback and self._suppress_notify_depth == 0:
                logger.info(f"Done: {action.description}")
            return result

        elif tier == SafetyTier.APPROVE:
            return await self._request_approval(action, executor)

        return "Unknown safety tier."

    async def _request_approval(self, action: PendingAction, executor) -> str:
        action_id = action.action_id or str(uuid.uuid4())[:8]
        action.action_id = action_id
        self._pending[action_id] = action

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self._approval_timeout_seconds)
        self._persist_pending_action(action, created_at=now, expires_at=expires_at)

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._approval_callbacks[action_id] = future

        if self._approval_request_handler:
            await self._approval_request_handler(action_id, action, future)
        elif self._notify_callback:
            await self._notify_callback(
                f"approval_request:{action_id}:{action.description}"
            )

        await self._emit_approval_state(
            action_id=action_id,
            status="pending",
            description=action.description,
            tool_name=action.tool_name,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            resolved_by=None,
        )

        logger.info(f"Approval requested: {action_id} — {action.description}")

        try:
            approved = await asyncio.wait_for(future, timeout=self._approval_timeout_seconds)
        except asyncio.TimeoutError:
            self._expire_approval(action_id)
            return "Action timed out waiting for your approval."

        if approved:
            result = await executor()
            self._memory.log_action(
                action=action.description,
                tier=SafetyTier.APPROVE.value,
                outcome=result[:200],
            )
            return result
        else:
            self._memory.log_action(
                action=action.description,
                tier=SafetyTier.APPROVE.value,
                outcome="declined by user",
            )
            return "Action cancelled."

    def resolve_approval(
        self,
        action_id: str,
        approved: bool,
        resolved_by: str | None = None,
    ) -> bool:
        future = self._approval_callbacks.pop(action_id, None)
        action = self._pending.pop(action_id, None)
        if action:
            self._mark_resolved(action_id, approved, action=action, resolved_by=resolved_by)
        if future and not future.done():
            future.set_result(approved)
            return True
        if action and approved:
            runner = self._approval_runner_tasks.pop(action_id, None)
            if runner and not runner.done():
                return True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return True
            self._approval_runner_tasks[action_id] = loop.create_task(
                self._execute_restored_approval(action_id, action)
            )
            return True
        if action:
            return True
        return False

    def activate_pending_approvals(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        now = datetime.now(timezone.utc)
        for action_id, action in list(self._pending.items()):
            if action_id in self._approval_callbacks:
                continue
            expires_at = self._load_pending_expiry(action_id)
            if expires_at is None:
                continue
            remaining = max(0.0, (expires_at - now).total_seconds())
            if remaining <= 0:
                self._expire_approval(action_id)
                continue
            future = loop.create_future()
            self._approval_callbacks[action_id] = future
            self._approval_runner_tasks[action_id] = loop.create_task(
                self._await_restored_resolution(action_id, action, future, remaining)
            )

    async def reannounce_pending_approvals(self) -> None:
        for action_id, action in list(self._pending.items()):
            future = self._approval_callbacks.get(action_id)
            if future is None:
                continue
            if self._approval_request_handler:
                await self._approval_request_handler(action_id, action, future)
            elif self._notify_callback:
                await self._notify_callback(
                    f"approval_request:{action_id}:{action.description}"
                )

    def emergency_stop(self) -> None:
        self._stopped = True
        for future in self._approval_callbacks.values():
            if not future.done():
                future.set_result(False)
        for task in self._approval_runner_tasks.values():
            task.cancel()
        self._pending.clear()
        self._approval_callbacks.clear()
        self._approval_runner_tasks.clear()
        logger.warning("Emergency stop activated")

    def resume(self) -> None:
        self._stopped = False
        logger.info("Safety gate resumed")

    def _persist_pending_action(
        self,
        action: PendingAction,
        *,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        if not hasattr(self._memory, "save_pending_approval"):
            return
        self._memory.save_pending_approval(
            action_id=action.action_id,
            tool_name=action.tool_name,
            description=action.description,
            tier=(
                action.tier.value
                if isinstance(action.tier, SafetyTier)
                else getattr(action.tier, "value", str(action.tier))
            ),
            reversible=action.reversible,
            payload=action.payload or {},
            created_at=created_at.isoformat(),
            expires_at=expires_at.isoformat(),
        )

    def _restore_persisted_pending(self) -> None:
        if not hasattr(self._memory, "load_pending_approvals"):
            return

        now = datetime.now(timezone.utc)
        for row in self._memory.load_pending_approvals():
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at <= now:
                self._expire_approval(row["action_id"])
                continue
            self._pending[row["action_id"]] = PendingAction(
                action_id=row["action_id"],
                tool_name=row["tool_name"],
                description=row["description"],
                tier=SafetyTier(row["tier"]),
                reversible=row["reversible"],
                payload=row["payload"] or {},
            )

    def _load_pending_expiry(self, action_id: str) -> datetime | None:
        if not hasattr(self._memory, "load_pending_approvals"):
            return None
        for row in self._memory.load_pending_approvals():
            if row["action_id"] == action_id:
                return datetime.fromisoformat(row["expires_at"])
        return None

    def _expire_approval(self, action_id: str) -> None:
        action = self._pending.pop(action_id, None)
        self._approval_callbacks.pop(action_id, None)
        task = self._approval_runner_tasks.pop(action_id, None)
        if task and not task.done():
            task.cancel()
        if hasattr(self._memory, "mark_pending_approval_expired"):
            self._memory.mark_pending_approval_expired(action_id)
        if action:
            self._schedule_approval_state(
                action_id=action_id,
                status="expired",
                description=getattr(action, "description", "Owner approval required"),
                tool_name=getattr(action, "tool_name", ""),
                created_at="",
                expires_at="",
                resolved_by=None,
            )

    def _mark_resolved(
        self,
        action_id: str,
        approved: bool,
        *,
        action: PendingAction | None = None,
        resolved_by: str | None = None,
    ) -> None:
        if hasattr(self._memory, "mark_pending_approval_resolved"):
            self._memory.mark_pending_approval_resolved(
                action_id,
                approved,
                resolved_by=resolved_by,
            )
        if action:
            self._schedule_approval_state(
                action_id=action_id,
                status="approved" if approved else "denied",
                description=getattr(action, "description", "Owner approval required"),
                tool_name=getattr(action, "tool_name", ""),
                created_at="",
                expires_at="",
                resolved_by=resolved_by,
            )

    async def _emit_approval_state(
        self,
        *,
        action_id: str,
        status: str,
        description: str,
        tool_name: str,
        created_at: str,
        expires_at: str,
        resolved_by: str | None,
    ) -> None:
        if not self._approval_state_handler:
            return
        result = self._approval_state_handler(
            action_id=action_id,
            status=status,
            description=description,
            tool_name=tool_name,
            created_at=created_at,
            expires_at=expires_at,
            resolved_by=resolved_by,
        )
        if inspect.isawaitable(result):
            await result

    def _schedule_approval_state(self, **kwargs) -> None:
        if not self._approval_state_handler:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._emit_approval_state(**kwargs))

    async def _await_restored_resolution(
        self,
        action_id: str,
        action: PendingAction,
        future: asyncio.Future,
        remaining_seconds: float,
    ) -> None:
        try:
            approved = await asyncio.wait_for(future, timeout=remaining_seconds)
        except asyncio.TimeoutError:
            self._expire_approval(action_id)
            return
        except asyncio.CancelledError:
            return

        self._approval_runner_tasks.pop(action_id, None)
        if not approved:
            self._memory.log_action(
                action=action.description,
                tier=SafetyTier.APPROVE.value,
                outcome="declined by user after restart",
            )
            return
        await self._execute_restored_approval(action_id, action)

    async def _execute_restored_approval(self, action_id: str, action: PendingAction) -> None:
        if not self._executor_builder:
            logger.warning(f"No executor builder available for restored approval {action_id}")
            if self._notify_callback:
                await self._notify_callback(
                    f"Approved action could not resume after restart: {action.description}"
                )
            return

        try:
            result = await self._executor_builder(action)
            self._memory.log_action(
                action=action.description,
                tier=SafetyTier.APPROVE.value,
                outcome=str(result)[:200],
            )
            if self._notify_callback:
                logger.info(f"Done: {action.description}")
        except Exception as e:
            logger.exception(e)
            if self._notify_callback:
                await self._notify_callback(
                    f"Approved action failed after restart: {action.description}"
                )
