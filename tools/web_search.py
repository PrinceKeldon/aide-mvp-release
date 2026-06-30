"""
AIDE -- web search tool via SearXNG.
"""
import httpx
from datetime import date
from bs4 import BeautifulSoup
from loguru import logger
from tools.base import BaseTool, SafetyTier
from core.settings import settings


class WebSearchTool(BaseTool):

    MAX_RESULTS = 5
    MAX_CHARS_PER_RESULT = 300

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for current information. "
            "Use for news, weather, prices, or anything recent. "
            "Input: a search query string."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text) -> str:
        # Handle both string and dict input
        if isinstance(input_text, dict):
            query = input_text.get("input", "") or input_text.get("query", "") or str(input_text)
        else:
            query = str(input_text)
        query = query.strip()

        if not query:
            return "Error: empty search query."

        if "weather" in query.lower():
            today = date.today().strftime("%B %d %Y")
            if today.lower() not in query.lower():
                query = f"{query} {today} forecast"

        logger.info(f"Searching: {query!r}")

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    f"{settings.searxng_url}/search",
                    params={"q": query, "format": "json", "language": "en"},
                )
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError:
            return "Web search unavailable. Run: docker-compose up -d searxng"
        except Exception as e:
            logger.error(f"Search error: {e}")
            return f"Search failed: {e}"

        results = data.get("results", [])
        if not results:
            return f"No results found for: {query}"

        lines = [f"Search results for '{query}':\n"]
        for i, r in enumerate(results[: self.MAX_RESULTS], 1):
            title = r.get("title", "No title")
            url = r.get("url", "")
            content = self._clean(r.get("content", ""))
            lines.append(f"{i}. {title}\n   {url}\n   {content}\n")

        return "\n".join(lines)

    @staticmethod
    def _clean(text: str) -> str:
        soup = BeautifulSoup(text, "html.parser")
        cleaned = " ".join(soup.get_text().split())
        return cleaned[:300]
