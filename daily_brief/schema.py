"""
AIDE Daily Brief - Data Schemas
Output data models for morning screen
"""
from enum import Enum
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime


class ItemType(Enum):
    CALENDAR_AGENDA = "calendar_agenda"
    EMAIL_DIGEST = "email_digest"
    DRAFT_MESSAGE = "draft_message"
    SCHEDULE_SUGGESTION = "schedule_suggestion"
    PREPARED_TASK = "prepared_task"
    APPROVAL_REQUEST = "approval_request"


class Priority(Enum):
    HIGH = "high"        # Time-sensitive or externally relevant
    MEDIUM = "medium"    # Useful today
    LOW = "low"          # Optional improvement


@dataclass
class DailyBriefItem:
    id: str
    type: ItemType
    priority: Priority
    title: str
    reason: str
    content: Dict
    actions: List[str]
    created_at: datetime
    state: str = "new"
    
    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type.value,
            "priority": self.priority.value,
            "title": self.title,
            "reason": self.reason,
            "content": self.content,
            "actions": self.actions,
            "created_at": self.created_at.isoformat(),
            "state": self.state,
        }


@dataclass
class DailyBriefSummary:
    headline: str
    counts: Dict[str, int]


@dataclass
class DailyBrief:
    date: str
    summary: DailyBriefSummary
    items: List[DailyBriefItem]
    generated_at: datetime
    
    def to_dict(self):
        return {
            "date": self.date,
            "summary": {
                "headline": self.summary.headline,
                "counts": self.summary.counts
            },
            "items": [item.to_dict() for item in self.items],
            "generated_at": self.generated_at.isoformat()
        }
