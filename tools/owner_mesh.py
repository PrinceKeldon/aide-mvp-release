"""
AIDE Tools — owner mesh orchestration.
"""
import json

from tools.base import BaseTool, SafetyTier


class DelegateMeshTaskTool(BaseTool):
    def __init__(self, orchestrator) -> None:
        self._orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "delegate_mesh_task"

    @property
    def description(self) -> str:
        return (
            "Delegate a task to another executor-capable device in the owner mesh. "
            "Input: JSON with 'task' and optional 'target_device_id'."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    async def execute(self, input_text: str) -> str:
        try:
            payload = input_text if isinstance(input_text, dict) else json.loads(str(input_text))
        except Exception:
            return "Error: input must be JSON with 'task' and optional 'target_device_id'."

        task = payload.get("task", "").strip()
        target_device_id = payload.get("target_device_id")
        extra_payload = payload.get("payload", {})

        if not task:
            return "Error: 'task' is required."

        result = await self._orchestrator.delegate_task(
            task,
            payload=extra_payload,
            preferred_device_id=target_device_id,
        )
        if result.get("status") == "completed":
            return (
                f"Delegated task completed on {result.get('executor_device_id')}: "
                f"{result.get('result', '')}"
            )
        if result.get("status") == "failed":
            return f"Delegation failed: {result.get('error', 'unknown error')}"
        return (
            f"Delegated task routed to {result.get('executor_device_id', 'mesh executor')} "
            f"(task_id: {result.get('task_id', 'unknown')})."
        )
