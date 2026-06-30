"""
AIDE Tools — Pastor Studio Contact Messages
Fetches new contact messages from The Pastor Studio admin API.
"""

import os
import httpx
from tools.base import BaseTool, SafetyTier
from loguru import logger


class ContactMessagesTool(BaseTool):
    """
    Fetches contact messages from The Pastor Studio.
    Use this when the user asks "Do I have any new messages?" or
    wants to check for new contact requests.
    """

    @property
    def name(self) -> str:
        return "get_pastor_studio_messages"

    @property
    def description(self) -> str:
        return (
            "Fetch new contact messages from The Pastor Studio. "
            "Returns a list of messages including name, email, and message content."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text: str = "") -> str:
        # Retrieve API Key from environment
        api_key = os.environ.get("PASTOR_STUDIO_API_KEY")

        if not api_key:
            logger.error("PASTOR_STUDIO_API_KEY not found in environment variables")
            return "Error: Pastor Studio API key is not configured. Please set PASTOR_STUDIO_API_KEY."

        url = "https://thepastorstudio.com/contact-messages"
        headers = {"X-Admin-Api-Key": api_key, "Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=headers)

                if response.status_code == 401:
                    return "Error: Unauthorized. The PASTOR_STUDIO_API_KEY is invalid."

                response.raise_for_status()
                messages = response.json()

                if not messages:
                    return "No new contact messages found."

                if not isinstance(messages, list):
                    # Handle case where response might be wrapped in an object
                    if isinstance(messages, dict) and "messages" in messages:
                        messages = messages["messages"]
                    else:
                        return f"Unexpected response format: {messages}"

                # Format messages for AIDE to read in plain English
                formatted = []
                for msg in messages:
                    name = msg.get("name", "Unknown")
                    email = msg.get("email", "No email")
                    content = msg.get("message", "No content")
                    formatted.append(f"From: {name} ({email})\nMessage: {content}\n---")

                return "\n".join(formatted)

        except httpx.HTTPStatusError as e:
            logger.error(f"Pastor Studio API HTTP error: {e}")
            return f"Error: Failed to fetch messages. API returned status {e.response.status_code}."
        except Exception as e:
            logger.exception(f"Unexpected error fetching Pastor Studio messages: {e}")
            return (
                f"Error: An unexpected error occurred while fetching messages: {str(e)}"
            )
