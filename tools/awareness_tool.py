import json
from typing import Any, Dict
from loguru import logger
from tools.base import BaseTool, SafetyTier
from memory.manager import MemoryManager

class UserAwarenessTool(BaseTool):
    """
    Allows the agent to update its awareness of the user's current 
    location and timezone. This information is persisted in memory
    and used for time-sensitive and geo-aware tasks.
    """

    @property
    def name(self) -> str:
        return "update_user_awareness"

    @property
    def description(self) -> str:
        return (
            "Update the user's current geographical and temporal awareness. "
            "Input must be JSON: {'location': 'City, Country', 'timezone': 'IANA Timezone (e.g., Europe/Berlin)'}. "
            "You can update one or both."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    async def execute(self, input_text: str) -> str:
        try:
            data = json.loads(input_text)
        except json.JSONDecodeError:
            return "Error: Input must be a valid JSON string."

        location = data.get("location")
        timezone = data.get("timezone")

        if not location and not timezone:
            return "Error: Either 'location' or 'timezone' must be provided."

        memory = MemoryManager()
        updates = []

        if location:
            memory.store_fact("user_location", location)
            updates.append(f"location to {location}")
        
        if timezone:
            memory.store_fact("user_timezone", timezone)
            updates.append(f"timezone to {timezone}")

        return f"Successfully updated user awareness: {', '.join(updates)}."
