"""
AIDE -- Tavily Search tool
Purpose-built for AI agents. Returns clean, structured,
LLM-ready results. No HTML artifacts, no noise.
Free tier: 1,000 searches/month.
"""

import httpx
from loguru import logger
from tools.base import BaseTool, SafetyTier
from core.settings import settings


class TavilySearchTool(BaseTool):
    @property
    def name(self) -> str:
        return "tavily_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for accurate, up-to-date information. "
            "Use for news, weather, people, books, prices, or any factual query. "
            "Input: a search query string."
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

        if len(query) > 500:
            return "Error: search query is too long. Please refine your request to be more concise."

        api_key = getattr(settings, "tavily_api_key", "")

        if not api_key:
            return "Tavily not configured. Add TAVILY_API_KEY to .env"

        logger.info(f"Tavily searching: {query!r}")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": api_key,
                        "query": query,
                        "search_depth": "basic",
                        "max_results": 5,
                        "include_answer": True,
                        "include_raw_content": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"Tavily error: {e}")
            return f"Tavily search failed: {e}"

        # Tavily returns a direct answer + source results
        answer = data.get("answer", "")
        results = data.get("results", [])

        if not answer and not results:
            return f"No results found for: {query}"

        lines = []

        # Direct answer first — this is the gold
        if answer:
            lines.append(f"Direct answer: {answer}\n")

        # Supporting sources
        if results:
            lines.append("Sources:")
            for i, r in enumerate(results[:3], 1):
                title = r.get("title", "")
                url = r.get("url", "")
                content = r.get("content", "")[:300]
                lines.append(f"{i}. {title}\n   {url}\n   {content}\n")

        return "\n".join(lines)
