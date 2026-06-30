"""
AIDE — base tool interface
All agent tools inherit from BaseTool.
The agent knows tools only by name + description — implementation is hidden.
"""
from abc import ABC, abstractmethod
from enum import Enum


class SafetyTier(Enum):
    """
    Every tool action is classified at the safety gate.
    autonomous — agent acts, logs, no notification
    notify     — agent acts, tells the owner immediately
    approve    — agent waits for one-tap owner approval
    """
    AUTONOMOUS = "autonomous"
    NOTIFY = "notify"
    APPROVE = "approve"


class BaseTool(ABC):
    """Abstract base for all AIDE tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used by the agent in tool calls."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-sentence description the LLM sees when choosing tools."""
        ...

    @property
    def safety_tier(self) -> SafetyTier:
        """Default safety tier for this tool. Override per-tool."""
        return SafetyTier.AUTONOMOUS
    
    def run(self, *args, **kwargs):
        return self.execute(*args, **kwargs)

    @abstractmethod
    async def execute(self, input_text: str) -> str:
        """
        Run the tool and return a plain-text result.
        The agent reads this as an observation.
        Raise an exception on failure — the agent loop handles it.
        """
        ...

    