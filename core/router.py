"""
AIDE -- Model Router
Automatically picks the best LLM for each task type.
Falls back silently if a model is unavailable or quota is hit.

Routing priority:
  code        -> Groq -> Gemini -> Ollama -> OpenAI
  reasoning   -> Gemini -> Groq -> Ollama -> OpenAI
  research    -> Gemini -> Groq -> Ollama -> OpenAI
  documents   -> Gemini -> Groq -> Ollama -> OpenAI
  private     -> Ollama only
  general     -> Ollama -> Gemini -> Groq -> OpenAI
"""
from collections import deque
from datetime import datetime
from time import perf_counter
import httpx
from enum import Enum
from loguru import logger
from core.settings import settings
from core.network_utils import (
    is_network_error,
    is_auth_error,
    is_rate_limit_error,
    classify_exception,
    NetworkError,
)


class TaskType(str, Enum):
    CODE      = "code"
    RESEARCH  = "research"
    DOCUMENTS = "documents"
    REASONING = "reasoning"
    PRIVATE   = "private"
    GENERAL   = "general"
    TOOLS     = "tools"


class ModelRouter:

    CODE_SIGNALS = [
        "write code", "write a script", "python", "javascript",
        "function", "class", "debug", "fix this code", "implement",
        "algorithm", "programme", "program", "coding", "script",
        "html", "css", "sql",
    ]

    EMAIL_SIGNALS = [
        "email", "inbox", "unread", "send email", "reply to",
        "check mail", "read mail", "search email", "mail from",
    ]

    DOCUMENT_SIGNALS = [
        "summarise this document", "summarize this document",
        "read this", "analyse this file", "analyze this file",
        "this document", "this report", "this contract",
        "long document", "entire file",
    ]

    PRIVATE_SIGNALS = [
        "private", "confidential", "don't search", "offline",
        "without internet", "locally", "keep this private",
        "sensitive", "personal data", "bank statement",
        "transaction", "transactions", "financeos",
        "financial data", "budget target",
    ]

    REASONING_SIGNALS = [
        "analyse", "analyze", "compare", "evaluate", "assess",
        "pros and cons", "should i", "recommend", "advise",
        "best option", "decision", "strategy", "plan",
    ]


    def __init__(self) -> None:
        self._groq_tokens_today = self._load_groq_usage()
        self._groq_token_limit  = 90000
        self._recent_runs = deque(maxlen=50)

    def _load_groq_usage(self) -> int:
        import json
        from datetime import date
        try:
            with open("data/groq_usage.json") as f:
                data = json.load(f)
            if data.get("date") == str(date.today()):
                return data.get("tokens", 0)
        except Exception:
            pass
        return 0

    def _save_groq_usage(self) -> None:
        import json
        from datetime import date
        try:
            with open("data/groq_usage.json", "w") as f:
                json.dump({
                    "date":   str(date.today()),
                    "tokens": self._groq_tokens_today,
                }, f)
        except Exception:
            pass

    def _groq_available(self) -> bool:
        available = self._groq_tokens_today < self._groq_token_limit
        if not available:
            logger.warning(f"Groq daily limit reached ({self._groq_tokens_today} tokens)")
        return available

    def classify(self, message: str) -> TaskType:
        msg = message.lower()
        if any(s in msg for s in self.PRIVATE_SIGNALS):
            return TaskType.PRIVATE
        if any(s in msg for s in self.CODE_SIGNALS):
            return TaskType.CODE
        if any(s in msg for s in self.DOCUMENT_SIGNALS):
            return TaskType.DOCUMENTS
        if any(s in msg for s in self.REASONING_SIGNALS):
            return TaskType.REASONING
        if any(s in msg for s in self.EMAIL_SIGNALS):
            return TaskType.TOOLS
        return TaskType.GENERAL

    def offline_chain(self) -> list:
        """When internet is down — Ollama only."""
        return [
            (model, "ollama", self._ollama_chat)
            for model in self._ollama_models()
        ]

    def _ollama_models(self) -> list[str]:
        models = []
        for model in [settings.ollama_model, settings.ollama_fallback_model]:
            name = (model or "").strip()
            if name and name not in models:
                models.append(name)
        return models

    def _ollama_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if settings.ollama_api_key:
            headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
        return headers

    def _ollama_url(self, path: str, base_url: str | None = None) -> str:
        base = (base_url or settings.ollama_base_url).rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        if base.endswith("/api"):
            return f"{base}{suffix}"
        return f"{base}/api{suffix}"

    def _inject_openai(self, chain: list[tuple[str, str, callable]], *, first: bool = False) -> list[tuple[str, str, callable]]:
        if not settings.openai_api_key:
            return chain
        openai_entry = (settings.openai_model, "openai", self._openai_chat)
        return [openai_entry] + chain if first else chain + [openai_entry]

    def _ollama_max_tokens_for_task(self, task_type: TaskType, requested: int) -> int:
        if task_type in (TaskType.GENERAL, TaskType.TOOLS):
            return min(requested, settings.ollama_chat_max_tokens)
        return min(requested, settings.ollama_reasoning_max_tokens)

    def _routing_chain(self, task_type: TaskType, offline: bool = False) -> list[tuple[str, str, callable]]:
        if offline:
            return self.offline_chain()

        all_chains = {
            TaskType.CODE: self._inject_openai([
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
            ]),
            TaskType.REASONING: self._inject_openai([
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
            ]),
            TaskType.RESEARCH: self._inject_openai([
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
            ]),
            TaskType.DOCUMENTS: self._inject_openai([
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
            ]),
            TaskType.PRIVATE: [(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
            TaskType.TOOLS: self._inject_openai([
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
            ]),
            TaskType.GENERAL: self._inject_openai([
                *[(model, "ollama", self._ollama_chat) for model in self._ollama_models()],
                ("gemini-2.5-flash",        "gemini", self._gemini_chat),
                ("llama-3.3-70b-versatile", "groq",   self._groq_chat),
            ]),
        }
        return all_chains.get(task_type, all_chains[TaskType.GENERAL])

    def _record_run(
        self,
        *,
        task_type: TaskType,
        provider: str,
        model: str,
        mode: str,
        success: bool,
        offline: bool,
        duration_ms: float,
        metrics: dict | None = None,
        error: str | None = None,
    ) -> None:
        self._recent_runs.appendleft(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "task_type": task_type.value,
                "provider": provider,
                "model": model,
                "mode": mode,
                "success": success,
                "offline": offline,
                "duration_ms": round(duration_ms, 1),
                "metrics": metrics or {},
                "error": error,
            }
        )

    def recent_diagnostics(self) -> dict:
        runs = list(self._recent_runs)
        successful = [run for run in runs if run.get("success")]
        average_latency_ms = 0.0
        if successful:
            average_latency_ms = round(
                sum(float(run.get("duration_ms", 0.0)) for run in successful) / len(successful),
                1,
            )
        return {
            "configured": {
                "ollama_base_url": settings.ollama_base_url,
                "ollama_model": settings.ollama_model,
                "ollama_fallback_model": settings.ollama_fallback_model,
                "ollama_cloud": settings.ollama_base_url.startswith("https://"),
            },
            "summary": {
                "total_runs": len(runs),
                "successful_runs": len(successful),
                "average_latency_ms": average_latency_ms,
            },
            "runs": runs,
        }

    def _clean_response(self, content: str) -> str:
        """Remove LLM markers, turn tokens, and redundant echoes."""
        if not content:
            return ""
        
        # Remove turn markers and system labels
        markers = [
            r"<turn\s*/>", 
            r"<\|turn\|>", 
            r"User:", 
            r"Assistant:", 
            r"AIDE:", 
            r"Model:", 
            r"Assistant\s*:\s*"
        ]
        for marker in markers:
            import re
            content = re.sub(marker, "", content, flags=re.IGNORECASE)
        
        return content.strip()

    async def chat(
        self,
        messages: list[dict],
        task_type: TaskType | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        offline: bool = False,
    ) -> dict:
        if task_type is None:
            task_type = TaskType.GENERAL

        logger.info(f"Router: task_type={task_type.value}{' [OFFLINE]' if offline else ''}")
        chain = self._routing_chain(task_type, offline=offline)

        # If not forced offline, do a quick pre-flight check on Ollama
        # If Ollama is available and cloud fails, we want to fallback fast
        ollama_available = None
        if not offline:
            try:
                ollama_available = await self._should_fallback_to_ollama()
                if ollama_available:
                    logger.debug("Pre-flight: Ollama is running and available as fallback")
            except Exception as e:
                logger.debug(f"Pre-flight Ollama check failed: {e}")

        encountered_network_error = False
        for model_name, provider, fn in chain:
            if provider == "groq" and not self._groq_available():
                logger.warning("Groq limit reached — skipping")
                continue
            
            # If we hit a network error, skip all non-Ollama providers and go straight to Ollama
            if encountered_network_error and provider != "ollama":
                logger.info(f"Network error detected — skipping {provider}/{model_name}, going straight to Ollama")
                continue
            
            started = perf_counter()
            try:
                logger.info(f"Trying {provider}/{model_name}")
                provider_max_tokens = max_tokens
                if provider == "ollama":
                    provider_max_tokens = self._ollama_max_tokens_for_task(task_type, max_tokens)
                
                # Use local Ollama URL when:
                # 1. In fallback mode (after network error), OR
                # 2. In true offline mode
                use_local_url = encountered_network_error or offline
                if provider == "ollama" and use_local_url:
                    logger.debug(f"Using local Ollama at {settings.ollama_local_base_url}")
                    result = await self._ollama_chat(
                        messages=messages,
                        model=model_name,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=provider_max_tokens,
                        base_url=settings.ollama_local_base_url,
                    )
                else:
                    result = await fn(
                        messages=messages,
                        model=model_name,
                        tools=tools,
                        temperature=temperature,
                        max_tokens=provider_max_tokens,
                    )
                logger.info(f"Success: {provider}/{model_name}")
                self._record_run(
                    task_type=task_type,
                    provider=provider,
                    model=model_name,
                    mode="chat",
                    success=True,
                    offline=offline,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    metrics=result.get("metrics"),
                )
                if "content" in result:
                    result["content"] = self._clean_response(result["content"])
                return result
            except Exception as e:
                error_type = classify_exception(e)
                is_net_error = is_network_error(e)
                is_auth_error_local = is_auth_error(e)
                
                self._record_run(
                    task_type=task_type,
                    provider=provider,
                    model=model_name,
                    mode="chat",
                    success=False,
                    offline=offline,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    error=str(e),
                )
                
                if is_net_error:
                    logger.warning(f"{provider}/{model_name} network error: {e} — falling back to Ollama")
                    encountered_network_error = True
                    if provider == "ollama":
                        # Try next Ollama model, or fail
                        logger.warning(f"Ollama also failed: {e} — trying next Ollama model")
                        continue
                    # Skip remaining non-Ollama providers
                    continue
                elif is_auth_error_local:
                    logger.warning(f"{provider}/{model_name} auth error: {e} — skipping to next provider")
                    continue
                else:
                    logger.warning(f"{provider}/{model_name} failed: {e} — trying next")
                    continue

        raise RuntimeError("All models in routing chain failed.")

    async def stream_chat(
        self,
        messages: list[dict],
        task_type: TaskType | None = None,
        tools: list[dict] | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        offline: bool = False,
    ):
        if task_type is None:
            task_type = TaskType.GENERAL

        if tools:
            result = await self.chat(
                messages=messages,
                task_type=task_type,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                offline=offline,
            )
            content = result.get("content", "")
            if content:
                yield content
            return

        logger.info(f"Router stream: task_type={task_type.value}{' [OFFLINE]' if offline else ''}")
        chain = self._routing_chain(task_type, offline=offline)

        # If not forced offline, do a quick pre-flight check on Ollama
        ollama_available = None
        if not offline:
            try:
                ollama_available = await self._should_fallback_to_ollama()
                if ollama_available:
                    logger.debug("Pre-flight: Ollama is running and available as fallback")
            except Exception as e:
                logger.debug(f"Pre-flight Ollama check failed: {e}")

        encountered_network_error = False
        for model_name, provider, fn in chain:
            if provider == "groq" and not self._groq_available():
                logger.warning("Groq limit reached — skipping stream provider")
                continue
            
            # If we hit a network error, skip all non-Ollama providers and go straight to Ollama
            if encountered_network_error and provider != "ollama":
                logger.info(f"Network error detected — skipping {provider}/{model_name} stream, going straight to Ollama")
                continue
            
            started = perf_counter()
            try:
                logger.info(f"Trying stream {provider}/{model_name}")
                provider_max_tokens = max_tokens
                if provider == "ollama":
                    provider_max_tokens = self._ollama_max_tokens_for_task(task_type, max_tokens)
                    # Use local Ollama URL when:
                    # 1. In fallback mode (after network error), OR
                    # 2. In true offline mode
                    use_local_url = encountered_network_error or offline
                    ollama_base = settings.ollama_local_base_url if use_local_url else None
                    if use_local_url:
                        logger.debug(f"Using local Ollama at {settings.ollama_local_base_url}")
                    async for chunk in self._ollama_stream_chat(
                        messages=messages,
                        model=model_name,
                        temperature=temperature,
                        max_tokens=provider_max_tokens,
                        base_url=ollama_base,
                    ):
                        yield self._clean_response(chunk)
                    logger.info(f"Stream success: {provider}/{model_name}")

                    self._record_run(
                        task_type=task_type,
                        provider=provider,
                        model=model_name,
                        mode="stream",
                        success=True,
                        offline=offline,
                        duration_ms=(perf_counter() - started) * 1000.0,
                    )
                    return

                result = await fn(
                    messages=messages,
                    model=model_name,
                    tools=None,
                    temperature=temperature,
                    max_tokens=provider_max_tokens,
                )
                content = result.get("content", "")
                if content:
                    yield self._clean_response(content)
                logger.info(f"Stream fallback success: {provider}/{model_name}")
                self._record_run(
                    task_type=task_type,
                    provider=provider,
                    model=model_name,
                    mode="stream-fallback",
                    success=True,
                    offline=offline,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    metrics=result.get("metrics"),
                )
                return
            except Exception as e:
                error_type = classify_exception(e)
                is_net_error = is_network_error(e)
                is_auth_error_local = is_auth_error(e)
                
                self._record_run(
                    task_type=task_type,
                    provider=provider,
                    model=model_name,
                    mode="stream",
                    success=False,
                    offline=offline,
                    duration_ms=(perf_counter() - started) * 1000.0,
                    error=str(e),
                )
                
                if is_net_error:
                    logger.warning(f"stream {provider}/{model_name} network error: {e} — falling back to Ollama")
                    encountered_network_error = True
                    if provider == "ollama":
                        # Try next Ollama model, or fail
                        logger.warning(f"Ollama stream also failed: {e} — trying next Ollama model")
                        continue
                    # Skip remaining non-Ollama providers
                    continue
                elif is_auth_error_local:
                    logger.warning(f"stream {provider}/{model_name} auth error: {e} — skipping to next provider")
                    continue
                else:
                    logger.warning(f"stream {provider}/{model_name} failed: {e} — trying next")
                    continue

        raise RuntimeError("All models in routing chain failed.")

    # ── Claude ───────────────────────────────────────────────────

    async def _claude_chat(
        self, messages, model, tools, temperature, max_tokens
    ) -> dict:
        if not settings.anthropic_api_key:
            raise RuntimeError("No Anthropic API key")

        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        # Separate system message from conversation
        system = ""
        conv_messages = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system = content
            elif role in ("user", "assistant"):
                conv_messages.append({"role": role, "content": content})
            elif role == "tool":
                conv_messages.append({"role": "user", "content": f"Tool result: {content}"})
        if not conv_messages:
            conv_messages = [{"role": "user", "content": "Hello"}]
        kwargs: dict = {
            "model":      model,
            "max_tokens": max_tokens,
            "messages":   conv_messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        response = await client.messages.create(**kwargs)
        # Parse content and tool calls from response
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                import json
                input_str = json.dumps(block.input) if isinstance(block.input, dict) else str(block.input)
                tool_calls.append({
                    "name":  block.name,
                    "input": input_str,
                })
        return {"content": content, "tool_calls": tool_calls}
    # ── OpenAI ───────────────────────────────────────────────────

    async def _openai_chat(
        self, messages, model, tools, temperature, max_tokens
    ) -> dict:
        if not settings.openai_api_key:
            raise RuntimeError("No OpenAI API key")

        import json
        async with httpx.AsyncClient(timeout=60) as client:
            body: dict = {
                "model":       model,
                "messages":    messages,
                "temperature": temperature,
                "max_tokens":  max_tokens,
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"

            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type":  "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()

        message = data["choices"][0]["message"]
        content = message.get("content") or ""

        tool_calls = []
        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except Exception:
                    args = {"input": tc["function"]["arguments"]}
                tool_calls.append({
                    "name":  tc["function"]["name"],
                    "input": args.get("input", ""),
                })

        return {"content": content, "tool_calls": tool_calls}

    # ── Groq ─────────────────────────────────────────────────────

    async def _groq_chat(
        self, messages, model, tools, temperature, max_tokens
    ) -> dict:
        if not settings.groq_api_key:
            raise RuntimeError("No Groq API key")
        from groq import AsyncGroq
        import json
        client = AsyncGroq(api_key=settings.groq_api_key)

        clean_messages = []
        pending_id = None
        for msg in messages:
            role = msg.get("role", "")
            if role == "assistant" and msg.get("tool_calls"):
                pending_id = "call_001"
                clean_messages.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": [{
                        "id": pending_id,
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps({"input": tc.get("input", "")}),
                        }
                    } for tc in msg.get("tool_calls", [])]
                })
            elif role == "tool":
                clean_messages.append({
                    "role": "tool",
                    "tool_call_id": pending_id or "call_001",
                    "content": msg.get("content", ""),
                })
                pending_id = None
            else:
                clean_messages.append(msg)

        kwargs: dict = {
            "model":       model,
            "messages":    clean_messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
        }
        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = "auto"

        completion = await client.chat.completions.create(**kwargs)
        message = completion.choices[0].message
        content = message.content or ""

        usage = completion.usage
        if usage:
            self._groq_tokens_today += usage.total_tokens
            self._save_groq_usage()
            logger.debug(f"Groq tokens: {self._groq_tokens_today}/{self._groq_token_limit}")

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {"input": tc.function.arguments}
                tool_calls.append({
                    "name":  tc.function.name,
                    "input": args.get("input", ""),
                })

        return {"content": content, "tool_calls": tool_calls}

    # ── Ollama ───────────────────────────────────────────────────

    async def _ollama_chat(
        self, messages, model, tools, temperature, max_tokens, base_url: str | None = None
    ) -> dict:
        import json
        body: dict = {
            "model":   model,
            "messages": messages,
            "stream":  False,
            "keep_alive": settings.ollama_keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                self._ollama_url("/chat", base_url=base_url),
                headers=self._ollama_headers(),
                json=body,
            )
            response.raise_for_status()
            data = response.json()

        message          = data.get("message", {})
        content          = message.get("content", "") or ""
        tool_calls_raw   = message.get("tool_calls", [])

        tool_calls = []
        for tc in tool_calls_raw:
            fn       = tc.get("function", {})
            raw_args = fn.get("arguments", {})
            if isinstance(raw_args, dict):
                tool_input = raw_args.get("input", "")
            else:
                try:
                    tool_input = json.loads(raw_args).get("input", "")
                except Exception:
                    tool_input = str(raw_args)
            tool_calls.append({
                "name":  fn.get("name", ""),
                "input": tool_input,
            })

        metrics = {
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "prompt_eval_duration": data.get("prompt_eval_duration"),
            "eval_count": data.get("eval_count"),
            "eval_duration": data.get("eval_duration"),
        }

        return {"content": content, "tool_calls": tool_calls, "metrics": metrics}

    async def _ollama_stream_chat(
        self, messages, model, temperature, max_tokens, base_url: str | None = None
    ):
        import json

        body: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": settings.ollama_keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                self._ollama_url("/chat", base_url=base_url),
                headers=self._ollama_headers(),
                json=body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed Ollama stream chunk")
                        continue
                    chunk = (data.get("message") or {}).get("content", "") or ""
                    if chunk:
                        yield chunk

    # ── Gemini ───────────────────────────────────────────────────

    async def _gemini_chat(
        self, messages, model, tools, temperature, max_tokens
    ) -> dict:
        if not settings.gemini_api_key:
            raise RuntimeError("No Gemini API key")

        gemini_messages = []
        system_text = ""

        for msg in messages:
            role    = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                system_text = content
            elif role == "user":
                text = f"System: {system_text}\n\n{content}" if system_text else content
                gemini_messages.append({"role": "user",  "parts": [{"text": text}]})
                system_text = ""
            elif role == "assistant":
                gemini_messages.append({"role": "model", "parts": [{"text": content}]})

        if not gemini_messages:
            gemini_messages = [{"role": "user", "parts": [{"text": "Hello"}]}]

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": settings.gemini_api_key},
                json={
                    "contents": gemini_messages,
                    "tools": [{"function_declarations": [
                        {
                            "name": t["function"]["name"],
                            "description": t["function"].get("description", ""),
                            "parameters": t["function"].get("parameters", {}),
                        } for t in tools
                    ]}] if tools else [],
                    "generationConfig": {
                        "temperature":    temperature,
                        "maxOutputTokens": max_tokens,
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        text = ""
        tool_calls = []

        for part in parts:
            if "text" in part:
                text += part["text"]
            elif "functionCall" in part:
                import json
                fc = part["functionCall"]
                args = fc.get("args", {})
                tool_calls.append({
                    "name":  fc.get("name", ""),
                    "input": json.dumps(args) if isinstance(args, dict) else str(args),
                })

        return {"content": text, "tool_calls": tool_calls}

    async def is_ollama_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(
                    self._ollama_url("/tags"),
                    headers=self._ollama_headers(),
                )
                return r.status_code == 200
        except Exception:
            return False

    async def _should_fallback_to_ollama(self) -> bool:
        """
        Check if we should immediately fallback to Ollama.
        Returns True if Ollama is available and we're not already using it.
        """
        return await self.is_ollama_running()
