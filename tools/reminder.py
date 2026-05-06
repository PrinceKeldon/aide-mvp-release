"""
AIDE -- Reminder tool
Lets the agent set reminders from conversation.
"Remind me to call John tomorrow at 9am"
"""
from tools.base import BaseTool, SafetyTier
from loguru import logger


class ReminderTool(BaseTool):

    def __init__(self, scheduler) -> None:
        self._scheduler = scheduler

    @property
    def name(self) -> str:
        return "set_reminder"

    @property
    def description(self) -> str:
        return (
            "Set a reminder for the user. "
            "Input: JSON with 'text' (what to remind) and 'due' (ISO datetime). "
            "Example: {\"text\": \"call John\", \"due\": \"2026-03-23T09:00:00\"}"
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    async def execute(self, input_text) -> str:
        import json
        from datetime import datetime
        try:
            if isinstance(input_text, dict):
                data = input_text
            else:
                data = json.loads(input_text)

            text = data.get("text", "")
            due  = data.get("due", "")

            if not text or not due:
                return "Error: need 'text' and 'due' fields."

            self._scheduler.add_reminder(text, due)
            due_dt = datetime.fromisoformat(due)
            return f"Reminder set: '{text}' at {due_dt.strftime('%B %d at %H:%M')}"

        except Exception as e:
            logger.error(f"Reminder tool error: {e}")
            return f"Could not set reminder: {e}"


class MonitorTopicTool(BaseTool):

    def __init__(self, scheduler) -> None:
        self._scheduler = scheduler

    @property
    def name(self) -> str:
        return "monitor_topic"

    @property
    def description(self) -> str:
        return (
            "Add a topic for AIDE to monitor and alert you about. "
            "Input: the topic string to watch. "
            "Example: 'gold price' or 'Tesla stock' or 'AI news'"
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text) -> str:
        topic = str(input_text).strip()
        if not topic:
            return "Error: empty topic."
        self._scheduler.add_monitor_topic(topic)
        return f"Now monitoring '{topic}'. I'll alert you when there's something new."
