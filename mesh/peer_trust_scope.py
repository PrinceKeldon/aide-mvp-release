from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SandboxMode(str, Enum):
    STRICT = "strict"
    LIMITED = "limited"
    TRUSTED = "trusted"


class ContextPolicy(str, Enum):
    EXPLICIT_ONLY = "explicit_only"
    CONTEXTUAL = "contextual"
    NONE = "none"


@dataclass
class PeerTrustScope:
    scope_id: str
    scope_name: str
    description: str = ""
    can_request_tasks: bool = False
    can_receive_results: bool = False
    can_send_updates: bool = False
    can_request_approvals: bool = False
    can_receive_context: ContextPolicy = ContextPolicy.EXPLICIT_ONLY
    can_receive_memory: bool = False
    allowed_task_types: list[str] = field(default_factory=list)
    allowed_context_types: list[str] = field(default_factory=list)
    sandbox_mode: SandboxMode = SandboxMode.STRICT
    requires_local_approval: bool = False
    risk_level: str = "low"
    is_template: bool = False

    def validate_task_type(self, task_type: str) -> bool:
        return bool(task_type and task_type in self.allowed_task_types)

    def validate_context_type(self, context_type: str) -> bool:
        return bool(context_type and context_type in self.allowed_context_types)

    def to_record(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "scope_name": self.scope_name,
            "description": self.description,
            "can_request_tasks": int(self.can_request_tasks),
            "can_receive_results": int(self.can_receive_results),
            "can_send_updates": int(self.can_send_updates),
            "can_request_approvals": int(self.can_request_approvals),
            "can_receive_context": self.can_receive_context.value,
            "can_receive_memory": int(self.can_receive_memory),
            "allowed_task_types": json.dumps(self.allowed_task_types),
            "allowed_context_types": json.dumps(self.allowed_context_types),
            "sandbox_mode": self.sandbox_mode.value,
            "requires_local_approval": int(self.requires_local_approval),
            "risk_level": self.risk_level,
            "is_template": int(self.is_template),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | Any) -> "PeerTrustScope":
        row = dict(record)
        return cls(
            scope_id=row["scope_id"],
            scope_name=row["scope_name"],
            description=row.get("description") or "",
            can_request_tasks=bool(row.get("can_request_tasks")),
            can_receive_results=bool(row.get("can_receive_results")),
            can_send_updates=bool(row.get("can_send_updates")),
            can_request_approvals=bool(row.get("can_request_approvals")),
            can_receive_context=ContextPolicy(row.get("can_receive_context") or ContextPolicy.EXPLICIT_ONLY.value),
            can_receive_memory=bool(row.get("can_receive_memory")),
            allowed_task_types=_json_list(row.get("allowed_task_types")),
            allowed_context_types=_json_list(row.get("allowed_context_types")),
            sandbox_mode=SandboxMode(row.get("sandbox_mode") or SandboxMode.STRICT.value),
            requires_local_approval=bool(row.get("requires_local_approval")),
            risk_level=row.get("risk_level") or "low",
            is_template=bool(row.get("is_template")),
        )


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except Exception:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]
