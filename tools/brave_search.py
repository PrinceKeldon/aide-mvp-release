"""
AIDE -- Brave Search tool
Real search API with 2,000 free queries/month.
Better results than SearXNG for niche and specific queries.
"""
import httpx
from loguru import logger
from tools.base import BaseTool, SafetyTier
from core.settings import settings


class BraveSearchTool(BaseTool):

    MAX_RESULTS = 5
    MAX_CHARS = 400

    @property
    def name(self) -> str:
        return "brave_search"

    @property
    def description(self) -> str:
        return (
            "Search the web using Brave Search API. "
            "Use for specific topics, people, books, news, or anything "
            "SearXNG cannot find. Input: a search query string."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    async def execute(self, input_text) -> str:
        if isinstance(input_text, dict):
            query = input_text.get("input", "") or str(input_text)
        else:
            query = str(input_text)
        query = query.strip()

        if not query:
            return "Error: empty search query."

        api_key = getattr(settings, "brave_search_api_key", "")
        if not api_key:
            return "Brave Search not configured. Add BRAVE_SEARCH_API_KEY to .env"

        logger.info(f"Brave searching: {query!r}")

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": api_key,
                    },
                    params={
                        "q": query,
                        "count": self.MAX_RESULTS,
                        "text_decorations": False,
                        "search_lang": "en",
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"Brave search error: {e}")
            return f"Brave search failed: {e}"

        results = data.get("web", {}).get("results", [])
        if not results:
            return f"No results found for: {query}"

        lines = [f"Brave search results for '{query}':\n"]
        for i, r in enumerate(results, 1):
            title       = r.get("title", "No title")
            url         = r.get("url", "")
            description = r.get("description", "")[:self.MAX_CHARS]
            lines.append(f"{i}. {title}\n   {url}\n   {description}\n")

        return "\n".join(lines)
