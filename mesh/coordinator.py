"""
AIDE Mesh Coordinator
Registers owner-mesh intent handlers on the mesh node.
"""

import asyncio
import uuid
from contextlib import nullcontext
from time import monotonic, perf_counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from loguru import logger

from core.logging_utils import LogRateLimiter
from core.safety import PendingAction, SafetyTier
from mesh.peer_registry import SovereignPeerRegistry
from mesh.task_engine import OwnerMeshTaskEngine, MeshTaskState
from mesh.workflows import (
    build_execution_prompt,
    format_workflow_result,
    normalize_workflow_type,
)
from mesh.checkpoint import TaskCheckpoint
from mesh.state_manager import MeshStateManager


class MeshCoordinator:
    def __init__(
        self,
        mesh_node,
        agent,
        memory,
        message_router,
        task_engine=None,
        safety_gate=None,
        registry=None,
    ):
        self._node = mesh_node
        self._agent = agent
        self._memory = memory
        self._router = message_router
        self._task_engine = task_engine or OwnerMeshTaskEngine(memory)
        self._safety = safety_gate
        self._registry = registry
        self._peer_registry = (
            SovereignPeerRegistry(registry) if registry is not None else None
        )
        self._peer_execution_tasks: set[asyncio.Task] = set()
        self._state_manager = MeshStateManager(
            self._node.identity.device_id, coordinator=self
        )
        self._health_log_limiter = LogRateLimiter()
        self._last_peer_revalidation: dict[str, float] = {}
        self._peer_was_reachable: dict[str, bool] = {}

    def register_all_handlers(self):
        handlers = {
            "PING": self._handle_ping,
            "PONG": self._handle_pong,
            "TASK_REQUEST": self._handle_task_request,
            "DELEGATE_TASK": self._handle_delegate_task,
            "TASK_UPDATE": self._handle_task_update,
            "TASK_RESULT": self._handle_task_result,
            "APPROVAL_REQUEST": self._handle_approval_request,
            "APPROVAL_RESPONSE": self._handle_approval_response,
            "STATUS": self._handle_status,
            "MEMORY_SYNC": self._handle_memory_sync,
            "ASK_PEER": self._handle_ask_peer,
            "PEER_RESPONSE": self._handle_peer_response,
            "PEER_TASK_REQUEST": self._handle_peer_task_request,
            "PEER_TASK_UPDATE": self._handle_peer_task_update,
            "PEER_TASK_RESULT": self._handle_peer_task_result,
            "PEER_TASK_CANCEL": self._handle_peer_task_cancel,
            "HANDOFF_REQUEST": self._handle_handoff_request,
            "HANDOFF_ACCEPT": self._handle_handoff_accept,
            "HANDOFF_DECLINE": self._handle_handoff_decline,
            "HANDOFF_RESULT": self._handle_handoff_result,
            "STATE_UPDATE": self._handle_state_update,
            "STATE_SYNC": self._handle_state_sync,
        }
        for intent_type, handler in handlers.items():
            self._node.register_handler(intent_type, handler)
        logger.info("Mesh coordinator handlers registered")

    async def start_health_monitor(self, interval: int = 60):
        """Starts the background peer health check loop."""
        logger.info(f"Starting mesh health monitor (interval={interval}s)")
        asyncio.create_task(self._health_monitor_loop(interval))

    async def _health_monitor_loop(self, interval: int):
        while True:
            try:
                if self._registry:
                    trusted_peers = self._registry.list_trusted_peers()
                    for peer in trusted_peers:
                        peer_id = peer["device_id"]
                        if peer_id == self._node.identity.device_id:
                            continue
                        endpoint = self._registry.get_mesh_endpoint(peer_id)
                        if endpoint:
                            host, port = endpoint
                            reachable = await self._node.verify_peer_reachability(host, port)
                            if not reachable:
                                message = f"Peer {peer['device_name']} ({peer_id}) unreachable at {host}:{port}."
                                if self._health_log_limiter.should_log(f"peer_unreachable:{peer_id}", 600):
                                    logger.bind(operational=True).warning(
                                        f"{message} Revalidation is rate-limited."
                                    )
                                else:
                                    logger.debug(message)
                                if self._should_revalidate_peer(peer_id):
                                    await self.revalidate_peer(peer_id)
                                self._peer_was_reachable[peer_id] = False
                            else:
                                if self._peer_was_reachable.get(peer_id) is False:
                                    logger.bind(operational=True).info(
                                        f"Peer {peer['device_name']} ({peer_id}) is reachable again at {host}:{port}."
                                    )
                                self._peer_was_reachable[peer_id] = True
                                # Periodic Heartbeat: ensure session is alive
                                # Construct a peer object compatible with send_intent
                                heartbeat_peer = {
                                    "agent_id": peer_id,
                                    "address": host,
                                    "port": port,
                                    "device_name": peer.get("device_name", "Unknown")
                                }
                                await self._node.send_intent(
                                    heartbeat_peer, 
                                    "PING", 
                                    {"timestamp": datetime.utcnow().isoformat()}
                                )
                
                # Also trigger a fresh mDNS scan by updating the discovery callback
                if hasattr(self._node, "_discovery") and self._node._discovery:
                    # We don't need to do much here as Discovery's browser is active,
                    # but we can manually call revalidate on any known but session-less peers.
                    pass
                    
            except Exception as e:
                logger.error(f"Health monitor loop error: {e}")
            await asyncio.sleep(interval)

    def _should_revalidate_peer(self, peer_id: str, interval_seconds: int = 300) -> bool:
        now = monotonic()
        last_attempt = self._last_peer_revalidation.get(peer_id)
        if last_attempt is not None and now - last_attempt < interval_seconds:
            return False
        self._last_peer_revalidation[peer_id] = now
        return True

    async def revalidate_peer(self, peer_id: str):
        """
        Attempts to refresh a peer's network location.
        In a real mDNS setup, this would trigger a discovery scan.
        Here, we ensure the session is cleared to force a fresh connection attempt.
        """
        logger.bind(operational=True).debug(f"Revalidating peer {peer_id}...")
        await self._node.clear_outbound_session(peer_id)
        # If we have a discovery object, we could force a refresh here.
        # For now, clearing the session ensures the next send attempt uses the latest registry info.

    async def _handle_ping(self, intent: dict) -> dict:
        sender_id = intent.get("sender_id", "unknown")
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        self._memory.log_action(
            action=f"mesh_ping_from:{sender_id}",
            tier="notify",
            outcome="pong_ready",
        )
        return {
            "status": "pong",
            "device_id": self._node.identity.device_id,
            "device_name": self._node.identity.device_name,
        }

    async def _handle_pong(self, intent: dict) -> dict:
        sender_id = intent.get("sender_id", "unknown")
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        logger.info(f"Pong received from {sender_id}")
        self._memory.log_action(
            action=f"mesh_pong_from:{sender_id}",
            tier="notify",
            outcome="acknowledged",
        )
        return {"status": "acknowledged", "sender_id": sender_id}

    async def _handle_task_request(self, intent: dict) -> dict:
        return await self._execute_mesh_task(intent, delegated=False)

    async def _handle_delegate_task(self, intent: dict) -> dict:
        return await self._execute_mesh_task(intent, delegated=True)

    async def _execute_mesh_task(self, intent: dict, delegated: bool) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        task_payload = params.get("payload", {})
        task_text = (
            task_payload.get("task")
            or params.get("task")
            or params.get("description")
            or ""
        )
        workflow_type = normalize_workflow_type(task_payload.get("workflow_type"))
        context_notes = task_payload.get("context_notes")
        requires_approval = bool(task_payload.get("requires_approval"))
        task_id = task_payload.get("task_id") or params.get("task_id")
        sender_id = intent.get("sender_id", "")

        if not task_text:
            return {"status": "error", "error": "no task provided"}

        if not task_id:
            task_id = self._task_engine.create_task(
                description=task_text,
                origin_device_id=sender_id or "unknown",
                assigned_device_id=self._node.identity.device_id,
                payload={
                    **(task_payload or {"task": task_text}),
                    "workflow_type": workflow_type,
                    "context_notes": context_notes,
                    "requires_approval": requires_approval,
                },
                state=MeshTaskState.ROUTED,
            )
        self._task_engine.transition(
            task_id,
            MeshTaskState.EXECUTING,
            assigned_device_id=self._node.identity.device_id,
            payload_update={
                "delegated": delegated,
                "workflow_type": workflow_type,
                "context_notes": context_notes,
                "requires_approval": requires_approval,
            },
        )

        origin_device_id = (
            task_payload.get("origin_device_id")
            or params.get("from_device_id")
            or sender_id
        )
        if delegated and self._router and origin_device_id:
            await self._router.send(
                origin_device_id,
                "TASK_UPDATE",
                {
                    "task_id": task_id,
                    "state": MeshTaskState.EXECUTING.value,
                    "assigned_device_id": self._node.identity.device_id,
                },
                from_device_id=self._node.identity.device_id,
            )

        if not self._agent:
            self._task_engine.transition(
                task_id, MeshTaskState.FAILED, result="agent unavailable"
            )
            return {
                "status": "failed",
                "task_id": task_id,
                "result": "agent unavailable",
            }

        execution_prompt = build_execution_prompt(
            workflow_type,
            task_text,
            context_notes=context_notes,
            requires_approval=requires_approval,
        )
        raw_result = await self._agent.run(execution_prompt)
        formatted = format_workflow_result(
            workflow_type,
            task_text,
            raw_result,
            context_notes=context_notes,
        )
        result = formatted["result"]
        self._task_engine.transition(
            task_id,
            MeshTaskState.COMPLETED,
            result=result,
            payload_update={
                "workflow_type": formatted["workflow_type"],
                "workflow_display_name": formatted["workflow_display_name"],
                "result_kind": formatted["result_kind"],
                "result_preview": formatted["result_preview"],
                "context_notes": context_notes,
                "requires_approval": requires_approval,
            },
        )
        if delegated and self._router and origin_device_id:
            await self._router.send(
                origin_device_id,
                "TASK_RESULT",
                {
                    "task_id": task_id,
                    "result": result,
                    "result_preview": formatted["result_preview"],
                    "result_kind": formatted["result_kind"],
                    "workflow_type": formatted["workflow_type"],
                    "workflow_display_name": formatted["workflow_display_name"],
                    "from_device_id": self._node.identity.device_id,
                },
                from_device_id=self._node.identity.device_id,
            )
        return {
            "status": "completed",
            "task_id": task_id,
            "executor_device_id": self._node.identity.device_id,
            "result": result,
            "result_preview": formatted["result_preview"],
            "result_kind": formatted["result_kind"],
            "workflow_type": formatted["workflow_type"],
            "workflow_display_name": formatted["workflow_display_name"],
        }

    async def _handle_task_update(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        task_id = params.get("task_id")
        state = params.get("state")
        if not task_id or not state:
            return {"status": "error", "error": "task_id and state are required"}

        try:
            mesh_state = MeshTaskState(state)
        except ValueError:
            return {"status": "error", "error": f"invalid state {state}"}

        record = self._task_engine.transition(
            task_id,
            mesh_state,
            assigned_device_id=params.get("assigned_device_id"),
            result=params.get("result"),
        )
        if not record:
            return {"status": "error", "error": "unknown task"}
        return {"status": "updated", "task_id": task_id, "state": record.state}

    async def _handle_task_result(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        task_id = params.get("task_id")
        if not task_id:
            return {"status": "error", "error": "task_id required"}
        existing = self._task_engine.get_task(task_id)
        if existing and existing.state == MeshTaskState.CANCELLED.value:
            return {"status": "ignored", "task_id": task_id}
        payload_update = {}
        if "result_preview" in params:
            payload_update["result_preview"] = params.get("result_preview")
        if "result_kind" in params:
            payload_update["result_kind"] = params.get("result_kind")
        if "workflow_type" in params:
            payload_update["workflow_type"] = normalize_workflow_type(
                params.get("workflow_type")
            )
        if "workflow_display_name" in params:
            payload_update["workflow_display_name"] = params.get(
                "workflow_display_name"
            )

        record = self._task_engine.transition(
            task_id,
            MeshTaskState.COMPLETED,
            result=params.get("result", ""),
            assigned_device_id=params.get("from_device_id"),
            payload_update=payload_update or None,
        )
        if not record:
            return {"status": "error", "error": "unknown task"}
        return {"status": "recorded", "task_id": task_id}

    async def _handle_approval_request(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        task_id = payload.get("task_id") or params.get("task_id")
        approval_id = payload.get("approval_id") or params.get("approval_id")
        description = (
            payload.get("action_description")
            or params.get("description")
            or "Owner mesh action"
        )
        if task_id:
            self._task_engine.transition(
                task_id,
                MeshTaskState.WAITING_APPROVAL,
                approval_device_id=params.get("to_device_id"),
            )
        return {
            "status": "awaiting_approval",
            "approval_id": approval_id,
            "task_id": task_id,
            "description": description,
        }

    async def _handle_approval_response(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability="can_approve")
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        approval_id = params.get("approval_id")
        approved = bool(params.get("approved"))
        task_id = params.get("task_id")

        if self._safety and approval_id:
            self._safety.resolve_approval(
                approval_id,
                approved,
                resolved_by=intent.get("sender_id"),
            )

        if task_id:
            self._task_engine.transition(
                task_id,
                MeshTaskState.ROUTED if approved else MeshTaskState.FAILED,
                result="approved" if approved else "denied",
            )
        return {
            "status": "approval_recorded",
            "approval_id": approval_id,
            "approved": approved,
            "task_id": task_id,
        }

    async def _handle_status(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        active_tasks = self._task_engine.list_active()
        return {
            "status": "online",
            "device_id": self._node.identity.device_id,
            "device_name": self._node.identity.device_name,
            "device_type": self._node.identity.device_type.value,
            "active_tasks": len(active_tasks),
            "handlers": sorted(list(self._node._intent_handlers.keys())),
        }

    async def _handle_memory_sync(self, intent: dict) -> dict:
        blocked = self._authorize_sender(
            intent, require_capability="can_receive_memory"
        )
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        snippet = payload.get("snippet") or params.get("snippet") or ""
        metadata = payload.get("metadata") or {}
        if not snippet:
            return {"status": "error", "error": "snippet required"}
        self._memory.add_to_semantic(
            snippet, metadata={"type": "owner_mesh_sync", **metadata}
        )
        return {"status": "stored", "bytes": len(snippet)}

    async def _handle_ask_peer(self, intent: dict) -> dict:
        blocked = self._authorize_sender(
            intent, require_capability="can_receive_context"
        )
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        prompt = params.get("payload", {}).get("prompt") or params.get("prompt") or ""
        correlation_id = params.get("payload", {}).get("correlation_id") or params.get(
            "correlation_id"
        )
        sender_id = intent.get("sender_id", "")
        if not prompt:
            return {"status": "error", "error": "prompt required"}
        if not self._agent:
            return {"status": "error", "error": "agent unavailable"}
        if (
            self._registry
            and sender_id
            and hasattr(self._registry, "append_peer_conversation_log")
        ):
            try:
                self._registry.append_peer_conversation_log(
                    sender_id,
                    role="peer",
                    text=prompt,
                    via_transport="mesh",
                    correlation_id=correlation_id,
                    metadata={"source": "mesh_inbound_ask"},
                )
            except Exception:
                logger.exception("Could not log inbound peer question")
        result = await self._agent.run(prompt)
        if (
            self._registry
            and sender_id
            and hasattr(self._registry, "append_peer_conversation_log")
        ):
            try:
                self._registry.append_peer_conversation_log(
                    sender_id,
                    role="local",
                    text=result,
                    via_transport="mesh",
                    correlation_id=correlation_id,
                    metadata={"source": "mesh_local_reply"},
                )
            except Exception:
                logger.exception("Could not log outbound peer answer")
        return {
            "status": "completed",
            "response": result,
            "correlation_id": correlation_id,
        }

    async def _handle_peer_response(self, intent: dict) -> dict:
        blocked = self._authorize_sender(intent, require_capability=None)
        if blocked:
            return blocked
        params = intent.get("parameters", {})
        response = params.get("response") or params.get("payload", {}).get(
            "response", ""
        )
        return {"status": "received", "response": response}

    async def _handle_peer_task_request(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        peer_agent_id = intent.get("sender_id", "")
        task_type = payload.get("task_type") or params.get("task_type") or ""
        context_type = (
            payload.get("context_type")
            or params.get("context_type")
            or "explicit_facts"
        )
        request_id = payload.get("request_id") or params.get("request_id")

        if not self._peer_registry:
            return {"status": "error", "error": "peer registry unavailable"}
        if not request_id:
            return {"status": "error", "error": "request_id required"}

        validation = self._peer_registry.validate_task_request(
            peer_agent_id=peer_agent_id,
            task_type=task_type,
            context_type=context_type,
            context_payload=payload.get("context", {}),
        )
        if not validation.allowed:
            if self._registry and hasattr(self._registry, "create_peer_task_log"):
                try:
                    self._registry.create_peer_task_log(
                        request_id=request_id,
                        requesting_peer_id=peer_agent_id,
                        responding_peer_id=self._node.identity.device_id,
                        task_type=task_type or "unknown",
                        current_state="rejected",
                        title=payload.get("title"),
                        instruction=payload.get("instruction"),
                        context_provided=payload.get("context", {}),
                        rejection_reason=validation.reason,
                    )
                except Exception:
                    pass
                if hasattr(self._registry, "update_peer_task_log_state"):
                    self._registry.update_peer_task_log_state(
                        request_id,
                        current_state="rejected",
                        rejection_reason=validation.reason,
                        result_summary=validation.reason,
                    )
            return {"status": "rejected", "error": validation.reason}

        accepted_at = self._timestamp()
        requires_local_approval = validation.requires_local_approval
        if self._registry and hasattr(self._registry, "create_peer_task_log"):
            self._registry.create_peer_task_log(
                request_id=request_id,
                requesting_peer_id=peer_agent_id,
                responding_peer_id=self._node.identity.device_id,
                task_type=task_type,
                current_state="pending_approval"
                if requires_local_approval
                else "accepted",
                title=payload.get("title"),
                instruction=payload.get("instruction"),
                context_provided=validation.sanitized_context or {},
                accepted_at=None if requires_local_approval else accepted_at,
                requires_requesting_approval=False,
                requesting_approval_state="not_required",
                requires_responding_approval=requires_local_approval,
                responding_approval_state="pending"
                if requires_local_approval
                else "not_required",
                scope_validated=True,
            )

        if requires_local_approval:
            await self._emit_peer_task_update(
                peer_agent_id=peer_agent_id,
                request_id=request_id,
                state="pending_approval",
                summary="Waiting for local owner approval.",
            )
            self._queue_peer_task_approval(
                request_id=request_id,
                peer_agent_id=peer_agent_id,
                payload=payload,
                validation=validation,
            )
            return {
                "status": "awaiting_local_approval",
                "request_id": request_id,
                "scope_id": validation.scope.scope_id if validation.scope else "",
                "sandbox_mode": validation.sandbox_mode.value
                if validation.sandbox_mode
                else "",
            }

        return await self._execute_peer_task_request(
            request_id=request_id,
            peer_agent_id=peer_agent_id,
            payload=payload,
            validation=validation,
            accepted_at=accepted_at,
        )

    async def _handle_peer_task_update(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        sender_id = intent.get("sender_id", "")
        request_id = payload.get("request_id") or params.get("request_id")
        state = payload.get("state") or params.get("state")
        if not request_id or not state:
            return {"status": "error", "error": "request_id and state are required"}
        if not self._registry or not hasattr(self._registry, "get_peer_task_log"):
            return {"status": "error", "error": "peer task log unavailable"}
        record = self._registry.get_peer_task_log(request_id)
        if not record:
            return {"status": "error", "error": "unknown peer task"}
        if record.get("responding_peer_id") != sender_id:
            return {"status": "error", "error": "peer task update sender mismatch"}
        validation = (
            self._peer_registry.validate_task_update(peer_agent_id=sender_id)
            if self._peer_registry
            else None
        )
        if validation and not validation.allowed:
            return {"status": "error", "error": validation.reason}

        started_at = self._timestamp() if state == "executing" else None
        self._registry.update_peer_task_log_state(
            request_id,
            current_state=state,
            started_at=started_at,
            result_summary=payload.get("summary") or params.get("summary"),
        )
        return {"status": "updated", "request_id": request_id, "state": state}

    async def _handle_peer_task_result(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        sender_id = intent.get("sender_id", "")
        request_id = payload.get("request_id") or params.get("request_id")
        if not request_id:
            return {"status": "error", "error": "request_id required"}
        if not self._registry or not hasattr(self._registry, "get_peer_task_log"):
            return {"status": "error", "error": "peer task log unavailable"}
        record = self._registry.get_peer_task_log(request_id)
        if not record:
            return {"status": "error", "error": "unknown peer task"}
        if record.get("responding_peer_id") != sender_id:
            return {"status": "error", "error": "peer task result sender mismatch"}
        validation = (
            self._peer_registry.validate_task_result(peer_agent_id=sender_id)
            if self._peer_registry
            else None
        )
        if validation and not validation.allowed:
            return {"status": "error", "error": validation.reason}
        self._registry.update_peer_task_log_state(
            request_id,
            current_state="completed",
            result_type=payload.get("result_type") or params.get("result_type"),
            result_summary=payload.get("result_summary")
            or params.get("result_summary"),
            result_data=payload.get("result_data") or params.get("result_data") or {},
        )
        return {"status": "recorded", "request_id": request_id}

    async def _handle_peer_task_cancel(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        payload = params.get("payload", {})
        sender_id = intent.get("sender_id", "")
        request_id = payload.get("request_id") or params.get("request_id")
        reason = (
            payload.get("reason")
            or params.get("reason")
            or "Cancelled by requesting peer"
        )
        if not request_id:
            return {"status": "error", "error": "request_id required"}
        if not self._registry or not hasattr(self._registry, "get_peer_task_log"):
            return {"status": "error", "error": "peer task log unavailable"}
        record = self._registry.get_peer_task_log(request_id)
        if not record:
            return {"status": "error", "error": "unknown peer task"}
        if record.get("requesting_peer_id") != sender_id:
            return {"status": "error", "error": "peer task cancel sender mismatch"}
        validation = (
            self._peer_registry.validate_task_update(peer_agent_id=sender_id)
            if self._peer_registry
            else None
        )
        if validation and not validation.allowed:
            return {"status": "error", "error": validation.reason}
        if record.get("current_state") in {
            "completed",
            "rejected",
            "failed",
            "cancelled",
        }:
            return {"status": "error", "error": "peer task already finished"}

        self._registry.update_peer_task_log_state(
            request_id,
            current_state="cancelled",
            rejection_reason=reason,
            result_summary=reason,
        )
        return {"status": "cancelled", "request_id": request_id}

    async def _execute_peer_task_request(
        self,
        *,
        request_id: str,
        peer_agent_id: str,
        payload: dict,
        validation,
        accepted_at: str,
    ) -> dict:
        sandbox_mode = validation.sandbox_mode.value if validation.sandbox_mode else ""
        if not self._agent:
            if self._registry and hasattr(self._registry, "update_peer_task_log_state"):
                self._registry.update_peer_task_log_state(
                    request_id,
                    current_state="failed",
                    accepted_at=accepted_at,
                    rejection_reason="agent unavailable",
                    result_summary="agent unavailable",
                )
            return {
                "status": "failed",
                "request_id": request_id,
                "scope_id": validation.scope.scope_id if validation.scope else "",
                "sandbox_mode": sandbox_mode,
                "error": "agent unavailable",
            }

        started_at = self._timestamp()
        if self._registry and hasattr(self._registry, "update_peer_task_log_state"):
            self._registry.update_peer_task_log_state(
                request_id,
                current_state="executing",
                accepted_at=accepted_at,
                started_at=started_at,
                responding_approval_state="approved"
                if validation.requires_local_approval
                else "not_required",
                scope_validated=True,
            )
        await self._emit_peer_task_update(
            peer_agent_id=peer_agent_id,
            request_id=request_id,
            state="executing",
            summary="Peer task is now executing.",
        )

        started = perf_counter()
        instruction = payload.get("instruction") or ""
        notify_context = (
            self._safety.suppress_notify()
            if self._safety is not None
            and not requires_approval
            and hasattr(self._safety, "suppress_notify")
            else nullcontext()
        )
        with notify_context:
            result = await self._agent.run(instruction)
        execution_time_ms = int((perf_counter() - started) * 1000)
        result_summary = self._summarize_peer_result(result)
        result_data = (
            {"response": result}
            if validation.scope and validation.scope.can_receive_results
            else {}
        )

        if self._registry and hasattr(self._registry, "update_peer_task_log_state"):
            self._registry.update_peer_task_log_state(
                request_id,
                current_state="completed",
                result_type="text_reply",
                result_summary=result_summary,
                result_data=result_data,
                sandbox_executed=True,
                execution_time_ms=execution_time_ms,
            )
        await self._emit_peer_task_result(
            peer_agent_id=peer_agent_id,
            request_id=request_id,
            result_type="text_reply",
            result_summary=result_summary,
            result_data=result_data,
        )

        response = {
            "status": "completed",
            "request_id": request_id,
            "scope_id": validation.scope.scope_id if validation.scope else "",
            "sandbox_mode": sandbox_mode,
            "sanitized_context": validation.sanitized_context or {},
            "result_summary": result_summary,
        }
        if result_data:
            response["result_data"] = result_data
        return response

    def _queue_peer_task_approval(
        self, *, request_id: str, peer_agent_id: str, payload: dict, validation
    ) -> None:
        if not self._safety:
            return

        peer = (
            self._peer_registry.get_peer(peer_agent_id) if self._peer_registry else None
        )
        peer_name = (peer or {}).get("display_name") or peer_agent_id
        title = payload.get("title") or payload.get("task_type") or "peer task"
        description = f"Allow {peer_name} to run '{title}'"
        action = PendingAction(
            action_id=f"peer_{request_id}",
            tool_name="peer_task_request",
            description=description,
            tier=SafetyTier.APPROVE,
            reversible=True,
            payload={
                "request_id": request_id,
                "peer_agent_id": peer_agent_id,
                "scope_id": validation.scope.scope_id if validation.scope else "",
            },
        )

        async def runner() -> None:
            async def executor() -> str:
                response = await self._execute_peer_task_request(
                    request_id=request_id,
                    peer_agent_id=peer_agent_id,
                    payload=payload,
                    validation=validation,
                    accepted_at=self._timestamp(),
                )
                return response.get(
                    "result_summary", response.get("status", "completed")
                )

            try:
                result = await self._safety.process(action, executor)
                if result in {
                    "Action cancelled.",
                    "Action timed out waiting for your approval.",
                }:
                    if self._registry and hasattr(
                        self._registry, "update_peer_task_log_state"
                    ):
                        self._registry.update_peer_task_log_state(
                            request_id,
                            current_state="rejected",
                            responding_approval_state="denied",
                            rejection_reason=result,
                            result_summary=result,
                        )
                    await self._emit_peer_task_result(
                        peer_agent_id=peer_agent_id,
                        request_id=request_id,
                        result_type="rejected",
                        result_summary=result,
                        result_data={},
                    )
            finally:
                current = asyncio.current_task()
                if current:
                    self._peer_execution_tasks.discard(current)

        task = asyncio.create_task(runner())
        self._peer_execution_tasks.add(task)

    def _timestamp(self) -> str:
        return (
            self._registry._now()
            if self._registry and hasattr(self._registry, "_now")
            else ""
        )

    async def broadcast_state_update(self, key: str, entry: Any):
        """Broadcast a state change to all trusted peers."""
        payload = {
            "key": key,
            "entry": vars(entry) if hasattr(entry, "__dict__") else entry,
        }
        await self._router.broadcast(
            "STATE_UPDATE", payload, capability_filter="can_receive_state"
        )

    async def request_full_sync(self):
        """Request a full state snapshot from any available peer."""
        await self._router.broadcast(
            "STATE_SYNC",
            {"request": "full_snapshot"},
            capability_filter="can_receive_state",
        )

    async def send_handoff(self, peer_id: str, checkpoint: TaskCheckpoint) -> str:
        """Initiates a live handoff of a task to a peer device."""
        logger.info(f"Initiating handoff to {peer_id} for task {checkpoint.task_id}")
        result = await self._router.send_with_response(
            to_device_id=peer_id,
            message_type="HANDOFF_REQUEST",
            payload={"checkpoint": checkpoint.to_dict()},
            from_device_id=self._node.identity.device_id,
        )
        return (
            "accepted" if result and result.get("status") == "accepted" else "declined"
        )

    async def send_handoff_result(self, origin_device_id: str, result: str):
        """Sends the final result of a handed-off task back to the originator."""
        await self._router.send(
            to_device_id=origin_device_id,
            message_type="HANDOFF_RESULT",
            payload={"result": result},
            from_device_id=self._node.identity.device_id,
        )

    async def _handle_handoff_request(self, intent: dict) -> dict:
        sender_id = intent.get("sender_id", "unknown")
        params = intent.get("parameters", {})
        checkpoint_data = params.get("checkpoint")
        if not checkpoint_data:
            return {"status": "error", "error": "missing checkpoint data"}

        checkpoint = TaskCheckpoint.from_dict(checkpoint_data)
        logger.info(
            f"Received handoff request for task {checkpoint.task_id} from {sender_id}"
        )

        # Simple capability check: can we run the remaining tools?
        can_execute = True
        for step in checkpoint.remaining_steps:
            tool = step.get("tool")
            if tool and tool not in self._agent._tools:
                can_execute = False
                break

        if not can_execute:
            logger.warning(
                f"Cannot accept handoff for task {checkpoint.task_id}: missing tools"
            )
            await self._router.send(
                to_device_id=sender_id,
                message_type="HANDOFF_DECLINE",
                payload={
                    "task_id": checkpoint.task_id,
                    "reason": "missing required tools",
                },
                from_device_id=self._node.identity.device_id,
            )
            return {"status": "declined", "reason": "missing tools"}

        # Accept handoff
        await self._router.send(
            to_device_id=sender_id,
            message_type="HANDOFF_ACCEPT",
            payload={"task_id": checkpoint.task_id},
            from_device_id=self._node.identity.device_id,
        )

        # Resume execution
        asyncio.create_task(self._resume_handoff_task(checkpoint))

        return {"status": "accepted", "task_id": checkpoint.task_id}

    async def _resume_handoff_task(self, checkpoint: TaskCheckpoint):
        """Rebuilds a task from a checkpoint and executes it."""
        from core.task_queue import Task, Step, StepStatus, TaskStatus

        # Rebuild Task object
        steps = []
        for s in checkpoint.completed_steps:
            steps.append(
                Step(
                    id=s.id,
                    description=s.description,
                    tool=s.tool,
                    input="",
                    result=s.result,
                    status=StepStatus.COMPLETE,
                    completed_at=s.completed_at,
                )
            )
        for s in checkpoint.remaining_steps:
            steps.append(
                Step(
                    id=str(uuid.uuid4())[:8],
                    description=s["description"],
                    tool=s["tool"],
                    input=s["input"],
                    status=StepStatus.PENDING,
                )
            )

        task = Task(
            id=checkpoint.task_id,
            description=checkpoint.original_goal,
            steps=steps,
            status=TaskStatus.RUNNING,
            created_at=checkpoint.created_at,
        )

        logger.info(f"Resuming handed-off task {task.id}")
        try:
            # execute() will start from the first PENDING step
            result = await self._agent._task_queue.execute(task)
            await self.send_handoff_result(checkpoint.created_by, result)
        except Exception as e:
            logger.error(f"Error resuming handed-off task {task.id}: {e}")

    async def _handle_handoff_accept(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        task_id = params.get("task_id")
        if self._agent and self._agent._task_queue:
            from core.task_queue import TaskStatus

            self._agent._task_queue.update_status(task_id, TaskStatus.HANDED_OFF)
        return {"status": "acknowledged"}

    async def _handle_handoff_decline(self, intent: dict) -> dict:
        logger.info(
            f"Handoff declined for task {intent.get('parameters', {}).get('task_id')}"
        )
        return {"status": "acknowledged"}

    async def _handle_handoff_result(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        task_id = params.get("task_id")
        result = params.get("result")
        if self._agent and self._agent._task_queue:
            from core.task_queue import TaskStatus

            self._agent._task_queue.update_status(
                task_id, TaskStatus.COMPLETE, result=result
            )
        return {"status": "recorded"}

    async def _handle_state_update(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        key = params.get("key")
        entry = params.get("entry")
        if not key or not entry:
            return {"status": "error", "error": "missing key or entry"}

        await self._state_manager.update_from_peer(key, entry)
        return {"status": "updated"}

    async def _handle_state_sync(self, intent: dict) -> dict:
        params = intent.get("parameters", {})
        if params.get("request") == "full_snapshot":
            full_state = self._state_manager.get_full_state()
            sender_id = intent.get("sender_id")
            if sender_id:
                await self._router.send(
                    sender_id,
                    "STATE_SYNC",
                    {"snapshot": full_state},
                    from_device_id=self._node.identity.device_id,
                )
        elif "snapshot" in params:
            await self._state_manager.sync_full_state(params["snapshot"])

        return {"status": "synced"}

    def _authorize_sender(
        self, intent: dict, require_capability: str | None
    ) -> dict | None:
        if self._registry is None:
            return None

        sender_id = intent.get("sender_id", "")
        intent_type = intent.get("intent_type", "mesh_intent")
        if not sender_id:
            return {"status": "error", "error": "missing sender_id"}

        record = self._registry.get_by_device_id(sender_id)
        if not record or not self._registry.is_trusted(sender_id):
            logger.warning(f"Rejected {intent_type} from untrusted peer {sender_id}")
            return {"status": "error", "error": "peer not trusted"}

        if require_capability == "can_receive_context":
            context_level = (record.get("can_receive_context") or "").strip().lower()
            if context_level in {"", "none", "false"}:
                logger.warning(
                    f"Rejected {intent_type} from {sender_id} (context access disabled)"
                )
                return {"status": "error", "error": "context access denied"}
        elif require_capability and not record.get(require_capability):
            logger.warning(
                f"Rejected {intent_type} from {sender_id} (missing {require_capability})"
            )
            return {
                "status": "error",
                "error": f"missing capability {require_capability}",
            }

        self._registry.update_last_seen(sender_id)
        return None
