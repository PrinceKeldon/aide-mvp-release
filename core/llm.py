"""
AIDE -- LLM client
Now delegates to ModelRouter for intelligent model selection.
Keeps backward compatibility -- existing code works unchanged.
"""
from core.router import ModelRouter, TaskType
from core.settings import settings

# Single shared router instance
_router = ModelRouter()


class LLMClient:
    """
    Backward-compatible wrapper around ModelRouter.
    Existing code calls llm.chat() -- router handles model selection.
    """

    def __init__(self) -> None:
        self._router = _router

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        task_type: TaskType | None = None,
        offline: bool = False,
    ) -> dict:
        return await self._router.chat(
            messages=messages,
            task_type=task_type,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            offline=offline,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        task_type: TaskType | None = None,
        offline: bool = False,
    ):
        async for chunk in self._router.stream_chat(
            messages=messages,
            task_type=task_type,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            offline=offline,
        ):
            yield chunk

    def classify_task(self, message: str) -> TaskType:
        return self._router.classify(message)

    async def is_ollama_running(self) -> bool:
        return await self._router.is_ollama_running()

    async def available_providers(self) -> list[str]:
        providers: list[str] = []
        if await self.is_ollama_running():
            providers.append("ollama")
        if settings.groq_api_key:
            providers.append("groq")
        if settings.gemini_api_key:
            providers.append("gemini")
        if settings.openai_api_key:
            providers.append("openai")
        return providers
