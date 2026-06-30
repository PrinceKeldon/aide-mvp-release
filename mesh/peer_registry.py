from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from mesh.peer_trust_scope import ContextPolicy, PeerTrustScope, SandboxMode


@dataclass
class PeerValidationResult:
    allowed: bool
    reason: str = ""
    requires_local_approval: bool = False
    scope: PeerTrustScope | None = None
    sandbox_mode: SandboxMode | None = None
    sanitized_context: dict | None = None


class SovereignPeerRegistry:
    """
    Policy-oriented facade for sovereign peer records.
    Keeps peer task validation logic out of the generic device registry.
    """

    def __init__(self, registry) -> None:
        self._registry = registry

    def get_peer(self, peer_agent_id: str) -> dict | None:
        if not peer_agent_id:
            return None
        if not hasattr(self._registry, "get_peer_agent"):
            return None
        return self._registry.get_peer_agent(peer_agent_id)

    def get_scope(self, peer_agent_id: str) -> PeerTrustScope | None:
        peer = self.get_peer(peer_agent_id)
        if not peer:
            return None
        scope_id = peer.get("trust_scope_id")
        if not scope_id or not hasattr(self._registry, "get_trust_scope"):
            return None
        record = self._registry.get_trust_scope(scope_id)
        if not record:
            return None
        return PeerTrustScope.from_record(record)

    def is_peer_paused(self, peer_agent_id: str) -> bool:
        peer = self.get_peer(peer_agent_id)
        if not peer:
            return False
        raw_metadata = peer.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
        else:
            try:
                import json

                metadata = json.loads(raw_metadata or "{}")
            except Exception:
                metadata = {}
        return bool(metadata.get("paused"))

    def validate_task_request(
        self,
        *,
        peer_agent_id: str,
        task_type: str,
        context_type: str,
        context_payload: dict | None = None,
    ) -> PeerValidationResult:
        peer = self.get_peer(peer_agent_id)
        if not peer:
            return PeerValidationResult(False, reason="unknown peer")
        if peer.get("revoked_at"):
            return PeerValidationResult(False, reason="peer revoked")
        if self.is_peer_paused(peer_agent_id):
            return PeerValidationResult(False, reason="peer paused")

        scope = self.get_scope(peer_agent_id)
        if not scope:
            return PeerValidationResult(False, reason="missing trust scope")

        if not scope.can_request_tasks:
            return PeerValidationResult(False, reason="scope does not permit task requests", scope=scope)
        if not scope.validate_task_type(task_type):
            return PeerValidationResult(False, reason=f"task type '{task_type}' is not allowed", scope=scope)
        if not scope.validate_context_type(context_type):
            return PeerValidationResult(False, reason=f"context type '{context_type}' is not allowed", scope=scope)

        sanitized_context, context_error = self._sanitize_context(
            scope=scope,
            context_type=context_type,
            context_payload=context_payload or {},
        )
        if context_error:
            return PeerValidationResult(False, reason=context_error, scope=scope)

        return PeerValidationResult(
            True,
            reason="allowed",
            requires_local_approval=scope.requires_local_approval,
            scope=scope,
            sandbox_mode=scope.sandbox_mode,
            sanitized_context=sanitized_context,
        )

    def validate_task_update(
        self,
        *,
        peer_agent_id: str,
    ) -> PeerValidationResult:
        peer = self.get_peer(peer_agent_id)
        if not peer:
            return PeerValidationResult(False, reason="unknown peer")
        if peer.get("revoked_at"):
            return PeerValidationResult(False, reason="peer revoked")
        if self.is_peer_paused(peer_agent_id):
            return PeerValidationResult(False, reason="peer paused")

        scope = self.get_scope(peer_agent_id)
        if not scope:
            return PeerValidationResult(False, reason="missing trust scope")
        if not scope.can_send_updates:
            return PeerValidationResult(False, reason="scope does not permit task updates", scope=scope)
        return PeerValidationResult(True, reason="allowed", scope=scope, sandbox_mode=scope.sandbox_mode)

    def validate_task_result(
        self,
        *,
        peer_agent_id: str,
    ) -> PeerValidationResult:
        peer = self.get_peer(peer_agent_id)
        if not peer:
            return PeerValidationResult(False, reason="unknown peer")
        if peer.get("revoked_at"):
            return PeerValidationResult(False, reason="peer revoked")
        if self.is_peer_paused(peer_agent_id):
            return PeerValidationResult(False, reason="peer paused")

        scope = self.get_scope(peer_agent_id)
        if not scope:
            return PeerValidationResult(False, reason="missing trust scope")
        if not scope.can_receive_results:
            return PeerValidationResult(False, reason="scope does not permit result delivery", scope=scope)
        return PeerValidationResult(True, reason="allowed", scope=scope, sandbox_mode=scope.sandbox_mode)

    def _sanitize_context(
        self,
        *,
        scope: PeerTrustScope,
        context_type: str,
        context_payload: dict,
    ) -> tuple[dict, str | None]:
        payload = deepcopy(context_payload or {})

        if scope.can_receive_context == ContextPolicy.NONE:
            if payload:
                return {}, "scope does not permit any context payload"
            return {}, None

        # Never allow memory to cross unless the scope explicitly says so.
        if not scope.can_receive_memory:
            for key in ("memory", "memory_snippets", "memory_context", "recall"):
                payload.pop(key, None)

        if scope.can_receive_context == ContextPolicy.EXPLICIT_ONLY:
            contextual_keys = {"related_context", "background", "inferred_context", "contextual_notes"}
            for key in contextual_keys:
                payload.pop(key, None)

        return {
            "context_type": context_type,
            "data": payload,
        }, None
