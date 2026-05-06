import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class StepCheckpoint:
    description: str
    tool: str
    result: str
    completed_at: str


@dataclass
class TaskCheckpoint:
    """
    Sprint 6: Task Checkpoint for Mesh Handoff.
    Captures the full state of a task for seamless transfer between devices.
    """

    task_id: str
    original_goal: str
    completed_steps: List[StepCheckpoint]
    remaining_steps: List[Dict[str, Any]]  # [{description, tool, input}]
    working_context: Dict[str, Any]  # Extracted facts/working memory
    created_at: str
    created_by: str  # device_id of sender
    reason: str  # why handoff was initiated

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskCheckpoint":
        # Convert completed_steps list of dicts back to StepCheckpoint objects
        completed = [StepCheckpoint(**s) for s in data.get("completed_steps", [])]
        return cls(
            task_id=data["task_id"],
            original_goal=data["original_goal"],
            completed_steps=completed,
            remaining_steps=data.get("remaining_steps", []),
            working_context=data.get("working_context", {}),
            created_at=data["created_at"],
            created_by=data["created_by"],
            reason=data["reason"],
        )
