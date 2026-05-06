"""
AIDE Owner Mesh — orchestration control plane.

Builds on the device registry, router, safety gate, and mesh task engine to provide:
- approval routing with escalation across active owner devices
- delegated execution across executor-capable nodes
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from loguru import logger

from mesh.task_engine import MeshTaskState
from mesh.workflows import get_workflow_definition, normalize_workflow_type


class OwnerMeshOrchestrator:
    EXECUTOR_STALE_AFTER_SECONDS = 900

    def __init__(
        self,
        *,
        local_device_id: str,
        registry,
        router,
        task_engine,
        notify_callback=None,
        approval_stage_timeout: float = 45.0,
    ) -> None:
        self._local_device_id = local_device_id
        self._registry = registry
        self._router = router
        self._task_engine = task_engine
        self._notify = notify_callback
        self._approval_stage_timeout = approval_stage_timeout
        self._approval_watchers: dict[str, asyncio.Task] = {}

    def set_notify_callback(self, callback) -> None:
        self._notify = callback

    def candidate_approvers(self, preferred_device_id: str | None = None) -> list[str]:
        devices = self._registry.list_active_devices("can_approve")
        ranked = []
        seen = set()

        def push(device_id: str):
            if not device_id or device_id in seen or not self._registry.is_trusted(device_id):
                return
            seen.add(device_id)
            ranked.append(device_id)

        if preferred_device_id and preferred_device_id != self._local_device_id:
            push(preferred_device_id)
        for device in devices:
            if device["device_id"] != self._local_device_id:
                push(device["device_id"])
        return ranked

    def candidate_executors(self, preferred_device_id: str | None = None) -> list[str]:
        devices = self._registry.list_active_devices("can_execute")
        remote_fresh = []
        remote_stale = []
        local = []
        seen = set()

        def is_fresh(device: dict) -> bool:
            last_seen = device.get("last_seen_at")
            if not last_seen:
                return False
            try:
                seen_at = datetime.fromisoformat(last_seen)
            except Exception:
                return False
            return (datetime.now(timezone.utc) - seen_at.astimezone(timezone.utc)).total_seconds() <= self.EXECUTOR_STALE_AFTER_SECONDS

        def push(device_id: str):
            if not device_id or device_id in seen or not self._registry.is_trusted(device_id):
                return
            device = self._registry.get_by_device_id(device_id)
            if not device:
                return
            if device_id != self._local_device_id and device.get("transport_type") not in {"mesh", "local"}:
                return
            seen.add(device_id)
            if device_id == self._local_device_id:
                local.append(device_id)
                return
            if is_fresh(device):
                remote_fresh.append(device_id)
            else:
                remote_stale.append(device_id)

        if preferred_device_id:
            preferred = self._registry.get_by_device_id(preferred_device_id)
            if preferred and preferred.get("can_execute"):
                push(preferred_device_id)

        for device in devices:
            push(device["device_id"])

        return remote_fresh + local + remote_stale

    async def route_approval_request(self, action_id: str, action, future: asyncio.Future) -> bool:
        task_id = None
        if isinstance(action.payload, dict):
            task_id = action.payload.get("task_id")
        if not task_id:
            task_id = self._task_engine.create_task(
                description=action.description,
                origin_device_id=self._local_device_id,
                payload={
                    "approval_id": action_id,
                    "approval_description": action.description,
                    "approval_tool": action.tool_name,
                },
                state=MeshTaskState.WAITING_APPROVAL,
            )

        candidates = self.candidate_approvers()
        if not candidates:
            if self._notify:
                await self._notify(f"approval_request:{action_id}:{action.description}")
            return False

        record = self._task_engine.transition(
            task_id,
            MeshTaskState.WAITING_APPROVAL,
            approval_device_id=candidates[0],
            payload_update={
                "approval_id": action_id,
                "approval_candidates": candidates,
                "approval_attempt_index": 0,
                "approval_tool": action.tool_name,
            },
        )
        if not record:
            return False

        first_sent = await self._send_approval_to_device(
            task_id,
            action_id,
            action.description,
            candidates[0],
            attempt_index=0,
        )
        watcher = asyncio.create_task(
            self._monitor_approval_escalation(
                task_id=task_id,
                action_id=action_id,
                description=action.description,
                candidates=candidates,
                future=future,
                start_index=0,
            )
        )
        self._approval_watchers[action_id] = watcher
        return first_sent

    async def delegate_task(
        self,
        description: str,
        *,
        payload: dict | None = None,
        preferred_device_id: str | None = None,
        origin_device_id: str | None = None,
        existing_task_id: str | None = None,
    ) -> dict:
        origin = origin_device_id or self._local_device_id
        workflow_type = normalize_workflow_type((payload or {}).get("workflow_type"))
        workflow = get_workflow_definition(workflow_type)
        candidates = self.candidate_executors(preferred_device_id=preferred_device_id)
        if not candidates:
            return {
                "status": "failed",
                "error": "no executor devices available",
            }

        if existing_task_id:
            task_id = existing_task_id
            self._task_engine.transition(
                task_id,
                MeshTaskState.PLANNED,
                assigned_device_id=None,
                result="",
                payload_update={
                    **(payload or {}),
                    "task": description,
                    "workflow_type": workflow.workflow_type,
                    "workflow_display_name": workflow.display_name,
                    "delegate_candidates": candidates,
                },
            )
        else:
            task_id = self._task_engine.create_task(
                description=description,
                origin_device_id=origin,
                assigned_device_id=None,
                payload={
                    **(payload or {}),
                    "task": description,
                    "workflow_type": workflow.workflow_type,
                    "workflow_display_name": workflow.display_name,
                    "delegate_candidates": candidates,
                },
                state=MeshTaskState.PLANNED,
            )

        last_error = "no executor devices available"
        for attempt_index, device_id in enumerate(candidates):
            self._task_engine.transition(
                task_id,
                MeshTaskState.ROUTED,
                assigned_device_id=device_id,
                retries=attempt_index,
                payload_update={"delegate_attempt_index": attempt_index},
            )
            self._task_engine.append_payload_list(task_id, "delegate_attempts", device_id)

            response = await self._router.send_with_response(
                to_device_id=device_id,
                message_type="DELEGATE_TASK",
                payload={
                    **(payload or {}),
                    "task_id": task_id,
                    "task": description,
                    "origin_device_id": origin,
                    "from_device_id": self._local_device_id,
                },
                from_device_id=self._local_device_id,
            )
            if not response:
                self._task_engine.record_retry(task_id)
                last_error = f"delivery failed to {device_id}"
                continue

            status = response.get("status")
            if status == "completed":
                self._task_engine.transition(
                    task_id,
                    MeshTaskState.COMPLETED,
                    assigned_device_id=device_id,
                    result=response.get("result", ""),
                    payload_update={
                        "executor_device_id": device_id,
                        "workflow_type": normalize_workflow_type(response.get("workflow_type", workflow.workflow_type)),
                        "workflow_display_name": response.get("workflow_display_name", workflow.display_name),
                        "result_kind": response.get("result_kind", workflow.result_kind),
                        "result_preview": response.get("result_preview"),
                    },
                )
                return {
                    "status": "completed",
                    "task_id": task_id,
                    "executor_device_id": device_id,
                    "result": response.get("result", ""),
                    "result_preview": response.get("result_preview"),
                    "result_kind": response.get("result_kind", workflow.result_kind),
                    "workflow_type": normalize_workflow_type(response.get("workflow_type", workflow.workflow_type)),
                    "workflow_display_name": response.get("workflow_display_name", workflow.display_name),
                }

            if status in {"accepted", "executing", "routed"}:
                self._task_engine.transition(
                    task_id,
                    MeshTaskState.EXECUTING,
                    assigned_device_id=device_id,
                    payload_update={"executor_device_id": device_id},
                )
                return {
                    "status": status,
                    "task_id": task_id,
                    "executor_device_id": device_id,
                }

            self._task_engine.record_retry(task_id)
            last_error = response.get("error", f"{status} on {device_id}")

        self._task_engine.transition(
            task_id,
            MeshTaskState.FAILED,
            result=last_error,
        )
        return {
            "status": "failed",
            "task_id": task_id,
            "error": last_error,
        }

    async def _monitor_approval_escalation(
        self,
        *,
        task_id: str,
        action_id: str,
        description: str,
        candidates: list[str],
        future: asyncio.Future,
        start_index: int,
    ) -> None:
        try:
            index = start_index
            while not future.done() and index + 1 < len(candidates):
                await asyncio.sleep(self._approval_stage_timeout)
                if future.done():
                    return
                index += 1
                device_id = candidates[index]
                logger.info(f"Escalating approval {action_id} to {device_id}")
                self._task_engine.transition(
                    task_id,
                    MeshTaskState.WAITING_APPROVAL,
                    approval_device_id=device_id,
                    payload_update={"approval_attempt_index": index},
                )
                await self._send_approval_to_device(
                    task_id,
                    action_id,
                    description,
                    device_id,
                    attempt_index=index,
                )

            if not future.done() and self._notify:
                await self._notify(
                    f"Approval for '{description}' is still pending after owner-mesh escalation."
                )
        finally:
            self._approval_watchers.pop(action_id, None)

    async def _send_approval_to_device(
        self,
        task_id: str,
        approval_id: str,
        description: str,
        device_id: str,
        *,
        attempt_index: int,
    ) -> bool:
        sent = await self._router.send(
            to_device_id=device_id,
            message_type="APPROVAL_REQUEST",
            payload={
                "task_id": task_id,
                "approval_id": approval_id,
                "action_description": description,
                "attempt_index": attempt_index,
            },
            from_device_id=self._local_device_id,
        )
        if sent:
            self._task_engine.append_payload_list(task_id, "approval_attempts", device_id)
        return sent
