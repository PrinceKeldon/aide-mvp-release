"""
AIDE -- ReAct agent loop with task queue, force search, and tool calling.
"""

import asyncio
import inspect
import re
import json
from typing import Tuple
from loguru import logger
from core.llm import LLMClient
from core.settings import settings
from tools.base import BaseTool
from memory.manager import MemoryManager
from core.router import TaskType
from core.self_corrector import SelfCorrector


SYSTEM_PROMPT = """You are Vera, a personal AI agent running entirely on this device.
You are helpful, accurate, and concise. You remember your owner across sessions.

{user_summary}

Today's date is {today}.

Rules:
- Be conversational and natural
- Never show raw context, memory facts, or system information to the user
- No markdown, no bullet points, no asterisks in responses
- Just clean plain sentences
- For weather, news, prices or current events — use the search tool
- For opening, browsing, visiting, or reading a specific website or URL — use the browse_url tool
- For reading, checking, or fetching emails — use the read_email tool
- For sending an email — use the send_email tool
- For searching emails by keyword or sender — use the search_email tool
- For replying to an email — use the reply_email tool
- Never tell the user to check their email themselves — always use the email tools
- For pinging or sending messages to a device — use the route_to_device tool
- For first-time FinanceOS budget setup — use the setup_finance_budget tool
- Device names include: android, phone, midas, mac, desk, computer
"""

SEARCH_RESULT_PROMPT = """You are Vera, a helpful personal agent.

Conversation so far:
{history}

The user just asked: {question}

Live search results:
{results}

Answer in 2-4 plain sentences.
Rules:
- No markdown, no bold, no asterisks, no bullet points
- No source URLs
- Use Celsius for temperatures unless user asks otherwise
- Only report weather for the location actually asked about
- If search results contain multiple locations, use only the relevant one
- Never reference previous incorrect information"""

TRIGGERS = [
    "weather",
    "temperature",
    "forecast",
    "rain",
    "snow",
    "sunny",
    "news",
    "latest",
    "current",
    "today",
    "right now",
    "happening",
    "price",
    "stock",
    "score",
    "who won",
    "what happened",
    "who is",
    "who was",
    "what is",
    "what was",
    "what are",
    "tell me about",
    "explain",
    "describe",
    "summarise",
    "summarize",
    "book",
    "novel",
    "author",
    "written by",
    "authored",
    "film",
    "movie",
    "album",
    "song",
    "artist",
    "how does",
    "how do",
    "why is",
    "why does",
    "where is",
    "when did",
    "when was",
]


class VeraAgent:
    MAX_ITERATIONS = 6
    MAX_TOOL_CALLS = 3
    SEARCH_TOOL_CALL_LIMIT = 2
    SEARCH_TOOL_NAMES = {"tavily_search", "web_search", "browse_url"}
    BENCHMARK_SEARCH_LIMIT = 2
    TERMINAL_TOOLS = {
        "send_email",
        "reply_email",
        "route_to_device",
        "delegate_mesh_task",
    }

    def __init__(
        self,
        llm,
        tools,
        memory,
        task_queue=None,
        offline_manager=None,
        device_manifest=None,
        target_interceptor=None,
        safety_gate=None,
    ) -> None:
        self._device_manifest = device_manifest
        self._target_interceptor = target_interceptor
        self._safety = safety_gate
        self._llm = llm
        self._tools: dict[str, BaseTool] = {t.name: t for t in tools}
        self._memory = memory
        from memory.extractor import MemoryExtractor

        self._extractor = MemoryExtractor(self._memory, self._llm)
        self._corrector = SelfCorrector()
        self._history: list[dict] = []
        self._tool_schemas = self._build_schemas()
        self._task_queue = task_queue
        self._offline_manager = offline_manager
        self._notify = None  # Set by scheduler or interface
        self._load_persistent_history()

    def _handle_action_command(self, message: str) -> str | None:
        """
        Detect and handle action commands directly.
        Returns tool name and input if matched, None otherwise.
        """
        msg = message.lower()

        if "remind me" in msg or "set a reminder" in msg:
            return "reminder"
        if "monitor the topic" in msg or "monitor topic" in msg:
            return "monitor"
        finance_setup_signals = [
            "set up finance budget",
            "setup finance budget",
            "set up my finance budget",
            "setup my finance budget",
            "first-time budget setup",
            "financeos budget setup",
            "guided budget setup",
        ]
        if any(signal in msg for signal in finance_setup_signals):
            return "finance_budget_setup"
        return None

    def _finance_setup_active(self) -> bool:
        tool = self._tools.get("setup_finance_budget")
        setup = getattr(tool, "setup", None)
        if setup is None:
            return False
        try:
            return setup.status().get("status") == "in_progress"
        except Exception:
            return False

    def _load_persistent_history(self) -> None:
        """
        Central memory is the source of truth for cross-model continuity.
        We keep no separate persistent in-process transcript.
        """
        try:
            recent = self._memory.load_recent_conversations(n=6)
            if recent:
                logger.info(
                    f"Central memory has {len(recent)} recent conversations available"
                )
        except Exception as e:
            logger.warning(f"Could not load persistent history: {e}")

    def _recent_messages(self, n_pairs: int = 6) -> list[dict]:
        try:
            return self._memory.load_recent_messages(n_pairs=n_pairs)
        except Exception as e:
            logger.warning(f"Could not load recent messages: {e}")
            return []

    def _last_user_message(self) -> str:
        recent = self._recent_messages(n_pairs=6)
        for msg in reversed(recent):
            if msg["role"] == "user":
                return msg["content"][:100]
        return ""

    def _recent_history_text(self, n_pairs: int = 6) -> str:
        lines = []
        for msg in self._recent_messages(n_pairs=n_pairs):
            role = "You" if msg["role"] == "assistant" else "User"
            lines.append(f"{role}: {msg['content'][:200]}")
        return "\n".join(lines)

    def _build_schemas(self) -> list[dict]:
        schemas = []
        for tool in self._tools.values():
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "input": {
                                    "type": "string",
                                    "description": "The search query or tool input",
                                }
                            },
                            "required": ["input"],
                        },
                    },
                }
            )
        return schemas

    def _describe_tool_action(self, tool_name: str, tool_input) -> str:
        if tool_name == "send_email":
            try:
                data = (
                    tool_input
                    if isinstance(tool_input, dict)
                    else json.loads(str(tool_input))
                )
                recipient = data.get("to", "unknown recipient")
                subject = data.get("subject", "").strip()
                account = (data.get("account") or data.get("from") or "").strip()
                if subject:
                    if account:
                        return f"Send email to {recipient} from {account} with subject '{subject}'"
                    return f"Send email to {recipient} with subject '{subject}'"
                if account:
                    return f"Send email to {recipient} from {account}"
                return f"Send email to {recipient}"
            except Exception:
                return "Send email"
        if tool_name == "reply_email":
            try:
                data = (
                    tool_input
                    if isinstance(tool_input, dict)
                    else json.loads(str(tool_input))
                )
                uid = data.get("uid", "unknown")
                return f"Reply to email UID {uid}"
            except Exception:
                return "Reply to an email"
        if tool_name == "route_to_device":
            try:
                data = (
                    tool_input
                    if isinstance(tool_input, dict)
                    else json.loads(str(tool_input))
                )
                target = data.get("target", "device")
                message_type = data.get("message_type", "PING")
                return f"Send {message_type} to {target}"
            except Exception:
                return "Route message to a device"
        return f"Run tool {tool_name}"

    def _prepare_tool_input(
        self,
        tool_name: str,
        tool_input,
        user_message: str | None = None,
    ):
        if tool_name != "send_email":
            return tool_input

        data = None
        if isinstance(tool_input, dict):
            data = dict(tool_input)
        elif isinstance(tool_input, str) and tool_input.strip().startswith("{"):
            try:
                data = json.loads(tool_input)
            except json.JSONDecodeError:
                data = None

        if not data:
            data = {}

        if user_message:
            extracted = self._extract_send_email_fields(user_message)
            for key, value in extracted.items():
                data.setdefault(key, value)

        # Normalize field names that LLM might generate
        data = self._normalize_send_email_fields(data)

        required = {"to", "subject", "body"}
        if required.issubset(
            {key for key, value in data.items() if str(value).strip()}
        ):
            payload = {
                "to": data["to"],
                "subject": data["subject"],
                "body": data["body"],
            }
            if str(data.get("account", "")).strip():
                payload["account"] = data["account"]
            return json.dumps(payload)
        return tool_input

    def _is_search_tool(self, tool_name: str) -> bool:
        return tool_name in self.SEARCH_TOOL_NAMES

    def _normalise_tool_input(self, tool_input) -> str:
        if isinstance(tool_input, dict):
            try:
                return json.dumps(tool_input, sort_keys=True)
            except Exception:
                return str(tool_input)
        return re.sub(r"\s+", " ", str(tool_input or "")).strip()

    def _tool_signature(self, tool_name: str, tool_input) -> str:
        return f"{tool_name}:{self._normalise_tool_input(tool_input).lower()}"

    def _search_budget_exhausted(
        self,
        tool_name: str,
        tool_input,
        search_queries: list[str],
        tool_observations: list[dict[str, str]],
    ) -> bool:
        if not self._is_search_tool(tool_name):
            return False
        if not tool_observations:
            return False
        query = self._normalise_tool_input(tool_input)
        if len(search_queries) >= self.SEARCH_TOOL_CALL_LIMIT:
            return True
        return any(self._queries_are_similar(query, previous) for previous in search_queries)

    @staticmethod
    def _queries_are_similar(left: str, right: str) -> bool:
        stopwords = {
            "a",
            "an",
            "and",
            "are",
            "best",
            "for",
            "in",
            "of",
            "the",
            "to",
            "with",
            "what",
            "which",
        }
        left_terms = {
            term
            for term in re.findall(r"[a-z0-9]+", left.lower())
            if term not in stopwords and len(term) > 2
        }
        right_terms = {
            term
            for term in re.findall(r"[a-z0-9]+", right.lower())
            if term not in stopwords and len(term) > 2
        }
        if not left_terms or not right_terms:
            return left.strip().lower() == right.strip().lower()
        overlap = len(left_terms & right_terms)
        union = len(left_terms | right_terms)
        return union > 0 and (overlap / union) >= 0.55

    async def _synthesize_from_tool_observations(
        self,
        user_message: str,
        observations: list[dict[str, str]],
        *,
        offline: bool,
        reason: str,
    ) -> str:
        if not observations:
            return ""
        packed = "\n\n".join(
            (
                f"Tool: {item['tool']}\n"
                f"Input: {item['input']}\n"
                f"Observation: {item['observation'][:2500]}"
            )
            for item in observations[-4:]
        )
        prompt = (
            "The agent has already gathered tool results and must now answer the user. "
            "Do not call any more tools. Use only the observations below. "
            "If the evidence is incomplete, state the limitation and give the best useful answer.\n\n"
            f"User request: {user_message}\n"
            f"Reason tool use stopped: {reason}\n\n"
            f"Tool observations:\n{packed}\n\n"
            "Answer in concise plain English."
        )
        try:
            result = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                task_type=TaskType.REASONING,
                temperature=0.1,
                offline=offline,
            )
        except Exception as exc:
            logger.warning(f"Tool observation synthesis failed: {exc}")
            return ""
        return str(result.get("content") or "").strip()

    @staticmethod
    def _compact_tool_answer(observations: list[dict[str, str]], reason: str) -> str:
        if not observations:
            return (
                "I could not complete the tool workflow because "
                f"{reason}. Please try again with a narrower request."
            )
        latest = observations[-1]
        observation = latest["observation"].strip()
        if len(observation) > 900:
            observation = observation[:900].rsplit(" ", 1)[0] + "..."
        return (
            f"I stopped additional tool calls because {reason}. "
            f"Using the results already gathered from {latest['tool']}: {observation}"
        )

    def _extract_send_email_fields(self, user_message: str) -> dict[str, str]:
        text = user_message.strip()
        recipient_match = re.search(
            r"\bto\s+([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b",
            text,
            re.IGNORECASE,
        )
        subject_match = re.search(
            r"\bsubject\s+[\"'](?P<subject>.+?)[\"']",
            text,
            re.IGNORECASE,
        )
        body_match = re.search(
            r"\bbody\s+[\"'](?P<body>.+?)[\"']",
            text,
            re.IGNORECASE,
        )

        extracted = {}
        if recipient_match:
            extracted["to"] = recipient_match.group(1).strip()
        if subject_match:
            extracted["subject"] = subject_match.group("subject").strip()
        if body_match:
            extracted["body"] = body_match.group("body").strip()

        account_match = re.search(
            r"\b(?:from|using)\s+(?:my\s+)?(?P<account>.+?)\s+to\s+[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
            text,
            re.IGNORECASE,
        )
        if account_match:
            extracted["account"] = account_match.group("account").strip().lower()
        return extracted

    def _normalize_send_email_fields(self, data: dict) -> dict:
        """
        Normalize various field name aliases that the LLM might generate.
        Maps: recipient/recipient_address/email → "to"
              message/message_body/content/text → "body"
              from/sender → "account"
        """
        normalized = dict(data)

        # Map "to" field aliases
        to_aliases = [
            "recipient",
            "recipient_address",
            "email",
            "email_address",
            "destination",
        ]
        if "to" not in normalized or not str(normalized.get("to", "")).strip():
            for alias in to_aliases:
                if alias in normalized and str(normalized.get(alias, "")).strip():
                    normalized["to"] = normalized[alias]
                    break

        # Map "subject" field aliases
        subject_aliases = ["title"]
        if (
            "subject" not in normalized
            or not str(normalized.get("subject", "")).strip()
        ):
            for alias in subject_aliases:
                if alias in normalized and str(normalized.get(alias, "")).strip():
                    normalized["subject"] = normalized[alias]
                    break

        # Map "body" field aliases
        body_aliases = ["message", "message_body", "content", "text", "text_body"]
        if "body" not in normalized or not str(normalized.get("body", "")).strip():
            for alias in body_aliases:
                if alias in normalized and str(normalized.get(alias, "")).strip():
                    normalized["body"] = normalized[alias]
                    break

        # Map "account" field aliases (from, from_email, sender)
        account_aliases = ["from", "from_email", "sender", "from_account"]
        if (
            "account" not in normalized
            or not str(normalized.get("account", "")).strip()
        ):
            for alias in account_aliases:
                if alias in normalized and str(normalized.get(alias, "")).strip():
                    normalized["account"] = normalized[alias]
                    break

        return normalized

    def _should_return_tool_observation(self, tool_name: str, observation: str) -> bool:
        if tool_name not in self.TERMINAL_TOOLS:
            return False
        if not isinstance(observation, str):
            return False
        text = observation.strip()
        if not text:
            return False
        return True

    async def _invoke_tool(self, tool: BaseTool, tool_input):
        result = tool.execute(tool_input)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _execute_tool(
        self, tool_name: str, tool_input, user_message: str | None = None
    ):
        tool = self._tools[tool_name]
        prepared_input = self._prepare_tool_input(
            tool_name, tool_input, user_message=user_message
        )

        async def executor():
            return await self._invoke_tool(tool, prepared_input)

        if not self._safety:
            return await executor()

        from core.safety import PendingAction, SafetyTier as GateSafetyTier

        tier = self._safety.classify(tool_name)
        if getattr(tool, "requires_confirmation", False):
            tier = GateSafetyTier.APPROVE

        action = PendingAction(
            action_id="",
            tool_name=tool_name,
            description=self._describe_tool_action(tool_name, prepared_input),
            tier=tier,
            payload={"input": prepared_input},
        )
        return await self._safety.process(action, executor)

    async def execute_pending_action(self, action) -> str:
        tool_name = action.tool_name
        if tool_name not in self._tools:
            # Special handling for system-level actions like OnIt processing
            if tool_name == "process_onit_transcript":
                return await self._process_onit_transcript(action.payload or {})
            raise RuntimeError(f"Tool not available for restored approval: {tool_name}")
        tool_input = (action.payload or {}).get("input", "")
        return await self._invoke_tool(self._tools[tool_name], tool_input)

    async def _process_onit_transcript(self, payload: dict) -> str:
        session_id = payload.get("session_id")
        if not session_id:
            return "No session ID provided for transcript processing."

        full_text = self._memory.finalize_transcription(session_id)
        if not full_text:
            return "No transcription data found for this session."

        logger.info(
            f"Processing OnIt transcript for session {session_id} ({len(full_text)} chars)"
        )

        # Feed the transcript back into the agent for extraction and summarization
        prompt = (
            f"I have just finished a session. Here is the raw transcription:\n\n"
            f"--- START TRANSCRIPT ---\n{full_text}\n--- END TRANSCRIPT ---\n\n"
            f"Please extract any key decisions, goals, or facts and store them in my memory. "
            f"Then provide a brief summary of what was captured."
        )

        return await self.run(prompt)

    async def _refine_query(self, prompt: str) -> str:
        """Refines a user prompt into a concise, keyword-based search query."""
        if len(prompt.split()) < 5:
            return prompt

        refine_prompt = f"""Refine the following user request into a concise, keyword-based string for a web search engine.
Remove conversational filler, pronouns, and politeness. Keep only the core factual search terms.

Request: {prompt}

Refined Query:"""

        try:
            # Use simple chat without tools for refinement
            result = await self._llm.chat(
                messages=[{"role": "user", "content": refine_prompt}],
                tools=None,
                temperature=0.1,
                task_type=TaskType.REASONING,
            )
            return result.get("content", prompt).strip().strip("\"'")
        except Exception as e:
            logger.warning(f"Query refinement failed: {e}")
            return prompt

    def _should_force_search(self, message: str) -> bool:
        msg = message.lower()

        # Never force search for action commands — let ReAct loop handle these
        action_commands = [
            "remind me",
            "set a reminder",
            "monitor the topic",
            "monitor topic",
            "add reminder",
            "schedule",
            "stop monitoring",
            "cancel reminder",
            "my calendar",
            "my reminders",
            "calendar reminder",
            "what is on my calendar",
            "what's on my calendar",
            "did you set any reminder",
            "did you set any reminders",
            "what reminders",
            "what reminder",
            # Mesh routing — must never go through web search
            "ping",
            "send a ping",
            "send to",
            "message to",
            "route to",
            "send message to",
            "forward to",
            "what devices",
            "list devices",
            "list mesh",
        ]

        if any(msg.startswith(a) or a in msg for a in action_commands):
            return False

        # Never force search for email commands — let tool calling handle these
        email_commands = [
            "email",
            "inbox",
            "unread",
            "send email",
            "reply to",
            "check mail",
            "read mail",
            "search email",
            "mail from",
        ]
        if any(e in msg for e in email_commands):
            return False
        # Never force search conversational or self-referential questions
        if self._is_conversational(message):
            return False

        # Never search questions about AIDE's own capabilities or the local system
        self_referential = [
            "can you",
            "are you",
            "do you",
            "what can you",
            "how many devices",
            "how are you",
            "what is your",
            "who are you",
            "tell me about yourself",
            "what do you",
            "scan your",
            "write code",
            "your code",
            "your environment",
            "in this conversation",
            "in the mesh",
        ]
        if any(s in msg for s in self_referential):
            return False

        # Never search for short factual questions under 6 words
        if len(msg.split()) < 6:
            return False
        # Don't search very short messages
        if len(msg.strip()) < 4:
            return False

        # Explicit trigger words
        if any(t in msg for t in TRIGGERS):
            # Only force search if it's not a simple "what is" conversational question
            if not any(
                msg.startswith(q) and len(msg.split()) < 4
                for q in ["what is", "who is", "where is"]
            ):
                return True

        # Question patterns
        question_starters = [
            "what ",
            "who ",
            "where ",
            "when ",
            "why ",
            "how ",
            "do you know",
            "tell me",
            "explain",
            "what's",
            "who's",
            "what is",
            "what are",
            "can you tell",
            "i want to know",
            "do you know",
        ]
        if any(msg.startswith(q) or q in msg for q in question_starters):
            return True

        # Ends with question mark
        if msg.strip().endswith("?"):
            return True

        return False

    def _is_conversational(self, message: str) -> bool:
        """
        Detect pure conversational messages that don't need internet.
        Greetings, simple questions, personal exchanges.
        """
        msg = message.lower().strip()
        conversational = [
            "hello",
            "hi",
            "hey",
            "how are you",
            "what day",
            "what time",
            "who are you",
            "what can you do",
            "thank",
            "thanks",
            "ok",
            "okay",
            "yes",
            "no",
            "good morning",
            "good night",
            "bye",
            "goodbye",
            "tell me about yourself",
            "what is your name",
            "available",
            "chat",
        ]
        return any(msg.startswith(c) or c in msg for c in conversational)

    def _is_simple_ping_command(self, message: str) -> bool:
        clean_msg = message.strip("\"'").strip()
        return (
            re.match(
                r"^(?:send\s+(?:a\s+)?)?ping(?:\s+to)?\s+(.+)$",
                clean_msg,
                re.IGNORECASE,
            )
            is not None
        )

    def _is_benchmark_research_request(self, message: str) -> bool:
        msg = message.lower()
        benchmark_signals = [
            "benchmark",
            "competitor",
            "compare against",
            "market comparison",
            "best in class",
            "glaring gaps",
            "feature gaps",
            "industry standard",
        ]
        research_signals = [
            "features",
            "tools",
            "apps",
            "platforms",
            "finance",
            "financeos",
            "personal finance",
            "budget",
        ]
        return any(signal in msg for signal in benchmark_signals) and any(
            signal in msg for signal in research_signals
        )

    def _extract_browse_url_request(self, message: str) -> str | None:
        """
        Detect explicit requests to open/read a specific URL or bare domain.
        This keeps direct browsing deterministic instead of relying on the LLM
        to choose the browse_url tool.
        """
        msg = message.strip()
        url_match = re.search(r"\bhttps?://[^\s<>()\"']+", msg, re.IGNORECASE)
        if url_match:
            return url_match.group(0).rstrip(".,;:!?)]}")

        browse_intent = re.search(
            r"\b(?:browse|open|visit|read|check|look at|go to|access)\b",
            msg,
            re.IGNORECASE,
        )
        if not browse_intent:
            return None

        domain_match = re.search(
            r"\b(?:www\.)?[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
            r"(?:/[^\s<>()\"']*)?",
            msg,
            re.IGNORECASE,
        )
        if not domain_match:
            return None

        domain = domain_match.group(0).rstrip(".,;:!?)]}")
        if "@" in domain:
            return None
        return domain

    async def _run_direct_browse(self, user_message: str, url: str, *, offline: bool) -> str:
        tool = self._tools.get("browse_url")
        if tool is None:
            return (
                "I do not have the browse_url tool available in this runtime, "
                "so I cannot open that website directly right now."
            )

        try:
            observation = await self._execute_tool(
                "browse_url",
                {"input": url},
                user_message=user_message,
            )
        except Exception as exc:
            logger.warning(f"Direct browse failed for {url}: {exc}")
            observation = f"Could not browse {url}: {exc}"

        observation_text = str(observation or "").strip()
        if not observation_text:
            return f"I tried to browse {url}, but the page returned no readable content."

        prompt = (
            "You are Vera. The user asked you to browse a specific website. "
            "Use only the browser observation below. Do not claim you cannot browse. "
            "If the browser observation shows an error or blocker, state that specific limitation. "
            "Otherwise, summarize what the page contains in 2-4 concise plain sentences.\n\n"
            f"User request: {user_message}\n\n"
            f"Browser observation:\n{observation_text[:6000]}"
        )
        try:
            result = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                temperature=0.1,
                task_type=TaskType.RESEARCH,
                offline=offline,
                max_tokens=settings.ollama_reasoning_max_tokens,
            )
            final_reply = str(result.get("content") or "").strip()
        except Exception as exc:
            logger.warning(f"Direct browse synthesis failed: {exc}")
            final_reply = ""

        if final_reply:
            return final_reply
        if len(observation_text) > 1200:
            observation_text = observation_text[:1200].rsplit(" ", 1)[0] + "..."
        return observation_text

    def _benchmark_queries(self, message: str) -> list[str]:
        msg = message.lower()
        if "finance" in msg or "budget" in msg:
            return [
                "AI personal finance apps budgeting transaction categorization alerts reporting features 2026",
                "personal finance app benchmark YNAB Monarch Copilot Rocket Money Cleo feature gaps 2026",
            ]
        cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", message)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) > 160:
            cleaned = cleaned[:160].rsplit(" ", 1)[0]
        return [
            f"{cleaned} competitor benchmark features",
            f"{cleaned} feature gaps best practices",
        ]

    def _offline_mode_for(self) -> bool:
        return (
            self._offline_manager is not None and not self._offline_manager.is_online()
        )

    def _normalize_routing_target(self, raw_target: str) -> str:
        target = raw_target.strip().strip("\"'.?!,;:").lower()
        words = target.split()
        while words and words[0] in {"my", "the", "a", "an"}:
            words.pop(0)
        return " ".join(words) if words else target

    async def _evaluate_progress(
        self, user_message: str, iterations: int, history: list[dict]
    ) -> Tuple[bool, str]:
        """
        Sprint 6: Progress Evaluator.
        Checks for loops, result quality, budget, and goal drift.
        Returns (is_failing, failure_reason).
        """
        if iterations < 2:
            return False, ""

        # 1. Result Quality
        # Check last 2 tool observations in history
        observations = [m["content"] for m in history if m["role"] == "tool"]
        if len(observations) >= 2:
            last_two = observations[-2:]
            if all(
                (
                    not obs
                    or len(str(obs)) < 50
                    or any(
                        x in str(obs).lower()
                        for x in ["error", "not found", "no results"]
                    )
                )
                for obs in last_two
            ):
                return True, "Low quality results in consecutive tool calls"

        # 2. Loop Detection (Already handled by SelfCorrector, but we can add a high-level check)
        # 3. Iteration Budget
        if iterations > 4:
            # Only fail if we haven't found a terminal answer yet
            return True, "Iteration budget exceeded without definitive result"

        # 4. Goal Drift (Simplified)
        # Check if the last assistant thought/content still contains keywords from original intent
        if history and history[-1]["role"] == "assistant":
            last_content = history[-1]["content"].lower()
            user_keywords = [w for w in user_message.lower().split() if len(w) > 4]
            if user_keywords and not any(k in last_content for k in user_keywords):
                return True, "Potential goal drift detected"

        return False, ""

    async def _pivot(self, reason: str, user_message: str, history: list[dict]) -> str:
        """Triggers an autonomous pivot: notifies user and returns the REPLAN_SIGNAL."""
        logger.info(f"Autonomous pivot triggered: {reason}")

        if self._notify:
            await self._notify("First approach hit a wall — trying a different route")

        completed_summary = "Recent steps: " + "\n".join(
            [f"- {m['content'][:100]}" for m in history if m["role"] == "tool"][-3:]
        )

        return self._corrector.get_replan_prompt(
            failure_reason=reason,
            completed_summary=completed_summary,
            original_goal=user_message,
        )

    def _extract_ask_peer_request(
        self, raw_message: str, resolution
    ) -> tuple[str, str] | None:
        if not resolution or not getattr(resolution, "has_explicit_target", False):
            return None
        if getattr(resolution, "status", "") != "resolved":
            return None

        target_phrase = self._normalize_routing_target(
            getattr(resolution, "target_phrase", "")
            or getattr(resolution, "canonical_name", "")
        )
        if not target_phrase:
            return None

        clean = raw_message.strip().strip("\"'").strip()
        target_pattern = re.escape(target_phrase)
        patterns = [
            rf"^(?:ask|message|tell)\s+{target_pattern}(?:\s*(?:about|to|:|-))?\s*(?P<prompt>.+)$",
            rf"^{target_pattern}[\s,:-]+(?P<prompt>.+)$",
            rf"^(?P<greeting>hi|hey|hello)\s+{target_pattern}(?:[\s,:-]+(?P<rest>.*))?$",
        ]
        for pattern in patterns:
            match = re.match(pattern, clean, re.IGNORECASE)
            if not match:
                continue
            if match.groupdict().get("prompt"):
                prompt = match.group("prompt").strip()
                if prompt:
                    return target_phrase, prompt
            greeting = match.groupdict().get("greeting")
            if greeting:
                rest = (match.groupdict().get("rest") or "").strip()
                prompt = greeting.capitalize()
                if rest:
                    prompt = f"{prompt}. {rest}"
                return target_phrase, prompt
        return None

    async def _build_standard_messages(
        self,
        user_message_for_llm: str,
        *,
        n_pairs: int = 6,
        include_memory_recall: bool = True,
    ) -> tuple[list[dict], bool]:
        offline = self._offline_mode_for()
        memory_context = ""
        if include_memory_recall:
            memory_context = await self._memory.recall(user_message_for_llm)
        from datetime import datetime

        user_summary = self._memory.get_user_summary()
        system = SYSTEM_PROMPT.format(
            user_summary=user_summary or "",
            today=datetime.now().strftime("%A, %B %d %Y"),
        )
        if memory_context:
            system += f"\n\nRelevant context:\n{memory_context}"
        if self._device_manifest:
            manifest = self._device_manifest.build()
            if manifest:
                system += f"\n\n{manifest}"

        messages = (
            [{"role": "system", "content": system}]
            + self._recent_messages(n_pairs=n_pairs)
            + [{"role": "user", "content": user_message_for_llm}]
        )
        return messages, offline

    async def _build_research_messages(
        self,
        user_message_for_llm: str,
        search_results: str,
        *,
        n_pairs: int = 6,
    ) -> tuple[list[dict], bool]:
        history_text = self._recent_history_text(n_pairs=n_pairs)
        prompt = SEARCH_RESULT_PROMPT.format(
            history=history_text or "No previous conversation.",
            results=search_results,
            question=user_message_for_llm,
        )
        messages, offline = await self._build_standard_messages(
            prompt,
            n_pairs=n_pairs,
            include_memory_recall=True,
        )
        return messages, offline

    async def _run_benchmark_research(self, user_message: str) -> str:
        logger.info(f"Benchmark research path: {user_message[:80]!r}")
        search_tool = self._tools.get("tavily_search") or self._tools.get("web_search")
        if search_tool is None:
            return ""

        observations: list[dict[str, str]] = []
        for query in self._benchmark_queries(user_message)[: self.BENCHMARK_SEARCH_LIMIT]:
            logger.info(f"Benchmark search: {query!r}")
            try:
                result = await self._invoke_tool(search_tool, query)
            except Exception as exc:
                result = f"Search failed: {exc}"
            observations.append(
                {
                    "tool": getattr(search_tool, "name", "search"),
                    "input": query,
                    "observation": str(result),
                }
            )

        if not observations:
            return ""

        packed = "\n\n".join(
            (
                f"Search query: {item['input']}\n"
                f"Result:\n{item['observation'][:3000]}"
            )
            for item in observations
        )
        history_text = self._recent_history_text(n_pairs=4)
        prompt = (
            "You are Vera doing a bounded benchmark research task. "
            "Use only the gathered search results and prior conversation context below. "
            "Do not call tools. Produce a practical build recommendation, identify glaring gaps, "
            "and separate MVP from later work. Keep it concise and concrete.\n\n"
            f"Prior context:\n{history_text or 'No previous conversation.'}\n\n"
            f"User request:\n{user_message}\n\n"
            f"Search results:\n{packed}\n\n"
            "Answer with: Current benchmark, Glaring gaps, MVP hardening, Later."
        )
        offline = self._offline_mode_for()
        result = await self._llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            temperature=0.1,
            task_type=TaskType.RESEARCH,
            offline=offline,
            max_tokens=settings.ollama_reasoning_max_tokens,
        )
        return str(result.get("content") or "").strip()

    def can_stream(self, user_message_for_llm: str) -> bool:
        if self._target_interceptor:
            resolution = self._target_interceptor.detect_and_resolve(
                user_message_for_llm
            )
            if resolution.has_explicit_target:
                return False
        if self._task_queue and self._task_queue.is_complex_task(user_message_for_llm):
            return False
        if self._handle_action_command(user_message_for_llm):
            return False
        if self._is_simple_ping_command(user_message_for_llm):
            return False
        if self._should_force_search(user_message_for_llm):
            return False
        return (
            self._is_conversational(user_message_for_llm)
            and len(user_message_for_llm.split()) < 10
        )

    async def run_stream(self, user_message_for_llm: str):
        if hasattr(self, "_log_mesh_event"):
            self._log_mesh_event("chat_user", user_message_for_llm[:100], "info")
        if not self.can_stream(user_message_for_llm):
            reply = await self.run(user_message_for_llm)
            if reply:
                yield reply
            return

        short_conversational = (
            self._is_conversational(user_message_for_llm)
            or len(user_message_for_llm.split()) <= 8
        )
        messages, offline = await self._build_standard_messages(
            user_message_for_llm,
            n_pairs=3 if short_conversational else 5,
            include_memory_recall=not short_conversational,
        )
        task_type = self._llm.classify_task(user_message_for_llm)
        chunks: list[str] = []

        try:
            async for chunk in self._llm.stream_chat(
                messages=messages,
                tools=None,
                temperature=0.3,
                max_tokens=settings.ollama_chat_max_tokens,
                task_type=task_type,
                offline=offline,
            ):
                if not chunk:
                    continue
                chunks.append(chunk)
                yield chunk
        except Exception as e:
            logger.warning(f"Streaming failed: {e} — falling back to standard run")
            if chunks:
                final_reply = "".join(chunks).strip()
                await self._memory.store(user_message_for_llm, final_reply)
                await self._extractor.extract_and_store(
                    user_message_for_llm, final_reply
                )
                return
            reply = await self.run(user_message_for_llm)
            if reply:
                yield reply
            return

        final_reply = "".join(chunks).strip()
        if not final_reply:
            reply = await self.run(user_message_for_llm)
            if reply:
                yield reply
            return

        await self._memory.store(user_message_for_llm, final_reply)
        if hasattr(self, "_log_mesh_event"):
            self._log_mesh_event("chat_aide", final_reply[:100], "success")
        logger.info(f"Agent streamed reply: {final_reply[:80]}...")
        await self._extractor.extract_and_store(user_message_for_llm, final_reply)

    async def run(self, user_message_for_llm: str) -> str:
        logger.info(f"Agent received: {user_message_for_llm[:80]}...")
        if hasattr(self, "_log_mesh_event"):
            self._log_mesh_event("chat_user", user_message_for_llm[:100], "info")
        raw_user_message = user_message_for_llm
        resolution = None
        # ── Explicit target interception (before LLM reasoning) ────
        if self._target_interceptor:
            resolution = self._target_interceptor.detect_and_resolve(raw_user_message)
            if resolution.has_explicit_target:
                if resolution.status in ("not_found", "ambiguous"):
                    await self._memory.store(
                        raw_user_message, resolution.clarification_msg
                    )
                    return resolution.clarification_msg

        # Device routing fast-path for simple pings only.
        clean_msg = raw_user_message.strip("\"'").strip()
        ping_match = re.match(
            r"^(?:send\s+(?:a\s+)?)?ping(?:\s+to)?\s+(.+)$", clean_msg, re.IGNORECASE
        )
        if ping_match and "route_to_device" in self._tools:
            if (
                resolution
                and resolution.has_explicit_target
                and resolution.status == "resolved"
            ):
                target = resolution.target_phrase or ping_match.group(1).strip()
            else:
                target = self._normalize_routing_target(ping_match.group(1))
            result = await self._tools["route_to_device"].execute(
                {"target": target, "message_type": "PING", "payload": {}}
            )
            return result

        ask_peer = self._extract_ask_peer_request(raw_user_message, resolution)
        if ask_peer and "route_to_device" in self._tools:
            target, prompt = ask_peer
            result = await self._tools["route_to_device"].execute(
                {
                    "target": target,
                    "message_type": "ASK_PEER",
                    "payload": {"prompt": prompt},
                }
            )
            return result

        if self._finance_setup_active() and "setup_finance_budget" in self._tools:
            result = await self._tools["setup_finance_budget"].execute(
                json.dumps({"action": "answer", "value": raw_user_message})
            )
            await self._memory.store(raw_user_message, result)
            return result

        if (
            resolution
            and resolution.has_explicit_target
            and resolution.status == "resolved"
        ):
            ctx = self._target_interceptor.build_routing_context(resolution)
            user_message_for_llm = raw_user_message + ctx
            logger.info(
                f"Explicit target detected — injected routing context for LLM:\n{ctx}"
            )

        # Offline mode detection
        offline = self._offline_mode_for()

        browse_url = self._extract_browse_url_request(user_message_for_llm)
        if browse_url:
            if offline:
                if self._offline_manager:
                    self._offline_manager.queue_task(user_message_for_llm)
                return (
                    "No internet right now. I've queued that and will "
                    "browse it as soon as we're back online."
                )
            final_reply = await self._run_direct_browse(
                user_message_for_llm,
                browse_url,
                offline=offline,
            )
            await self._memory.store(user_message_for_llm, final_reply)
            if hasattr(self, "_log_mesh_event"):
                self._log_mesh_event("chat_aide", final_reply[:100], "success")
            logger.info(f"Direct browse replied: {final_reply[:80]}...")
            await self._extractor.extract_and_store(user_message_for_llm, final_reply)
            return final_reply

        # Complex multi-step task path
        if self._task_queue and self._task_queue.is_complex_task(user_message_for_llm):
            logger.info("Complex task detected — routing to task queue")
            task = await self._task_queue.plan(user_message_for_llm)

            # Mesh state reporting
            notify_callback = None
            if hasattr(self, "_update_mesh_state") and hasattr(self, "_log_mesh_event"):

                async def mesh_notify(msg):
                    self._log_mesh_event("task_progress", msg, "info")

                notify_callback = mesh_notify
                self._update_mesh_state(
                    project="General",
                    task=user_message_for_llm,
                    device=getattr(self, "device_id", "unknown"),
                    progress=0,
                )
                self._log_mesh_event(
                    "task_start", f"Started: {user_message_for_llm[:60]}...", "info"
                )

            reply = await self._task_queue.execute(
                task, notify_callback=notify_callback
            )

            if hasattr(self, "_update_mesh_state"):
                self._update_mesh_state(progress=100)
            if hasattr(self, "_log_mesh_event"):
                self._log_mesh_event(
                    "task_complete",
                    f"Completed: {user_message_for_llm[:60]}...",
                    "success",
                )

            await self._memory.store(user_message_for_llm, reply)
            return reply

        if offline:
            # Only queue tasks that genuinely need internet
            needs_internet = self._should_force_search(
                user_message_for_llm
            ) and not self._is_conversational(user_message_for_llm)
            if needs_internet:
                self._offline_manager.queue_task(user_message_for_llm)
                return (
                    "No internet right now. I've queued that and will "
                    "handle it as soon as we're back online."
                )
            # Otherwise fall through — Ollama handles it locally

        # Direct action commands — bypass LLM tool calling
        action_type = self._handle_action_command(user_message_for_llm)
        if action_type == "reminder" and "set_reminder" in self._tools:
            # Extract reminder details using LLM
            from datetime import datetime

            parse_result = await self._llm.chat(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Extract the reminder details from this message as JSON with "
                            f"'text' and 'due' (ISO datetime, today is {datetime.utcnow().isoformat()}): "
                            f"{user_message_for_llm}\n\nJSON only:"
                        ),
                    }
                ],
                tools=None,
                temperature=0.1,
                task_type=TaskType.REASONING,
            )
            content = parse_result.get("content", "")
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    reminder_data = json.loads(match.group())
                    result = await self._tools["set_reminder"].execute(reminder_data)
                    await self._memory.store(user_message_for_llm, result)
                    return result
                except Exception as e:
                    logger.warning(f"Reminder parse failed: {e}")

        if action_type == "monitor" and "monitor_topic" in self._tools:
            # Extract topic
            topic = user_message_for_llm.lower()
            for phrase in [
                "monitor the topic:",
                "monitor topic:",
                "monitor the topic",
                "monitor topic",
            ]:
                topic = topic.replace(phrase, "").strip()
            topic = topic.strip("\"'")
            if topic:
                result = await self._tools["monitor_topic"].execute(topic)
                await self._memory.store(user_message_for_llm, result)
                return result

        if action_type == "finance_budget_setup" and "setup_finance_budget" in self._tools:
            result = await self._tools["setup_finance_budget"].execute(
                json.dumps({"action": "start"})
            )
            await self._memory.store(user_message_for_llm, result)
            return result

        if self._is_benchmark_research_request(user_message_for_llm):
            try:
                final_reply = await self._run_benchmark_research(user_message_for_llm)
            except Exception as exc:
                logger.warning(f"Benchmark research path failed: {exc}")
                final_reply = ""
            if final_reply:
                await self._memory.store(user_message_for_llm, final_reply)
                if hasattr(self, "_log_mesh_event"):
                    self._log_mesh_event("chat_aide", final_reply[:100], "success")
                logger.info(f"Benchmark research replied: {final_reply[:80]}...")
                await self._extractor.extract_and_store(user_message_for_llm, final_reply)
                return final_reply

        # Don't search for very short or empty messages
        # Force search path
        if self._should_force_search(user_message_for_llm):
            logger.info(f"Force searching for: {user_message_for_llm[:60]!r}")
            try:
                # Refine the query before searching to avoid 400 errors
                search_query = await self._refine_query(user_message_for_llm)
                logger.info(f"Refined force search query: {search_query!r}")

                last_user = self._last_user_message()
                follow_up_signals = [
                    "it",
                    "this",
                    "that",
                    "there",
                    "he",
                    "she",
                    "they",
                    "more",
                    "also",
                    "similar",
                ]
                is_follow_up = len(user_message_for_llm.split()) < 8 and any(
                    user_message_for_llm.lower().startswith(s)
                    for s in follow_up_signals
                )
                if is_follow_up and last_user:
                    # For follow-ups, refine the combined context
                    combined_prompt = f"{last_user} {user_message_for_llm}"
                    search_query = await self._refine_query(combined_prompt)
                    logger.info(f"Refined follow-up search query: {search_query!r}")

                if "tavily_search" in self._tools:
                    search_results = await self._tools["tavily_search"].execute(
                        search_query
                    )
                    if (
                        "No results" in search_results
                        or "failed" in search_results.lower()
                    ):
                        logger.info("Tavily empty — falling back to SearXNG")
                        search_results = await self._tools["web_search"].execute(
                            search_query
                        )
                else:
                    search_results = await self._tools["web_search"].execute(
                        search_query
                    )

                logger.info("Search complete — sending results to LLM for summary")
                messages, offline = await self._build_research_messages(
                    user_message_for_llm,
                    search_results,
                    n_pairs=6,
                )

                result = await self._llm.chat(
                    messages=messages,
                    tools=None,
                    temperature=0.1,
                    task_type=TaskType.RESEARCH,
                    offline=offline,
                    max_tokens=settings.ollama_reasoning_max_tokens,
                )
                final_reply = result.get("content", "").strip()

                if final_reply:
                    await self._memory.store(user_message_for_llm, final_reply)
                    if hasattr(self, "_log_mesh_event"):
                        self._log_mesh_event("chat_aide", final_reply[:100], "success")
                    logger.info(f"Agent replied: {final_reply[:80]}...")
                    await self._extractor.extract_and_store(user_message_for_llm, final_reply)
                    return final_reply


            except Exception as e:
                logger.warning(
                    f"Force search failed: {e} — falling through to agent loop"
                )

        # Standard ReAct loop for conversational queries
        messages, offline = await self._build_standard_messages(user_message_for_llm)
        final_reply = ""
        tool_call_count = 0
        tool_signatures: set[str] = set()
        search_queries: list[str] = []
        tool_observations: list[dict[str, str]] = []

        for iteration in range(self.MAX_ITERATIONS):
            logger.debug(f"ReAct iteration {iteration + 1}")

            # --- Sprint 6: Progress Evaluation ---
            if iteration > 0 and iteration % 2 == 0:
                is_failing, reason = await self._evaluate_progress(
                    user_message_for_llm, iteration, messages
                )
                if is_failing:
                    logger.warning(f"Progress evaluator fired: {reason}")
                    replan_signal = await self._pivot(
                        reason, user_message_for_llm, messages
                    )
                    messages.append({"role": "system", "content": replan_signal})
            # -------------------------------------

            task_type = self._llm.classify_task(user_message_for_llm)
            result = await self._llm.chat(
                messages=messages,
                tools=self._tool_schemas,
                task_type=task_type,
                offline=offline,
            )

            content = result.get("content", "")
            tool_calls = result.get("tool_calls", [])

            if not tool_calls:
                final_reply = content.strip()
                break

            # Prevent infinite tool call loops
            tool_call_count += len(tool_calls)
            if tool_call_count > self.MAX_TOOL_CALLS:
                logger.warning("Too many tool calls — breaking loop")
                final_reply = await self._synthesize_from_tool_observations(
                    user_message_for_llm,
                    tool_observations,
                    offline=offline,
                    reason="tool call limit reached",
                )
                if not final_reply:
                    final_reply = content.strip() or self._compact_tool_answer(
                        tool_observations,
                        "tool call limit reached",
                    )
                break

            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_input = tc.get("input", "")
                signature = self._tool_signature(tool_name, tool_input)

                if signature in tool_signatures:
                    logger.warning(
                        f"Duplicate tool call blocked: {tool_name}({tool_input!r})"
                    )
                    final_reply = await self._synthesize_from_tool_observations(
                        user_message_for_llm,
                        tool_observations,
                        offline=offline,
                        reason=f"duplicate {tool_name} call",
                    )
                    if not final_reply:
                        final_reply = self._compact_tool_answer(
                            tool_observations,
                            f"duplicate {tool_name} call",
                        )
                    break

                if self._search_budget_exhausted(
                    tool_name,
                    tool_input,
                    search_queries,
                    tool_observations,
                ):
                    logger.warning(
                        f"Search budget exhausted before {tool_name}({tool_input!r})"
                    )
                    final_reply = await self._synthesize_from_tool_observations(
                        user_message_for_llm,
                        tool_observations,
                        offline=offline,
                        reason="search budget exhausted",
                    )
                    if not final_reply:
                        final_reply = self._compact_tool_answer(
                            tool_observations,
                            "search budget exhausted",
                        )
                    break

                tool_signatures.add(signature)
                if self._is_search_tool(tool_name):
                    search_queries.append(self._normalise_tool_input(tool_input))

                if tool_name not in self._tools:
                    observation = f"Error: tool '{tool_name}' not found."
                    logger.warning(observation)
                else:
                    logger.info(f"Calling tool: {tool_name}({tool_input!r})")
                    try:
                        observation = await self._execute_tool(
                            tool_name,
                            tool_input,
                            user_message=user_message_for_llm,
                        )
                    except Exception as e:
                        observation = f"Tool error: {e}"
                        logger.error(f"Tool {tool_name} failed: {e}")

                # --- Sprint 6: Self-Correction Hook ---
                observation, corrected = await asyncio.to_thread(
                    self._corrector.evaluate_and_correct,
                    tool_name,
                    tool_input,
                    observation,
                    self._tools,
                )
                if corrected:
                    logger.info(f"Self-correction applied to {tool_name} result")
                    messages.append(
                        {
                            "role": "system",
                            "content": f"CRITICAL: The last tool call to {tool_name} was a duplicate. You already have the result in your history. STOP calling this tool with the same input and use the existing information to respond to the user.",
                        }
                    )
                # --------------------------------------

                logger.info(
                    f"Tool observation from {tool_name}: {str(observation)[:120]}"
                )

                if self._should_return_tool_observation(tool_name, observation):
                    final_reply = str(observation).strip()
                    break

                tool_observations.append(
                    {
                        "tool": tool_name,
                        "input": self._normalise_tool_input(tool_input),
                        "observation": str(observation),
                    }
                )

                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "tool", "content": observation})
            if final_reply:
                break

        else:
            final_reply = await self._synthesize_from_tool_observations(
                user_message_for_llm,
                tool_observations,
                offline=offline,
                reason="reasoning iteration limit reached",
            )
            if not final_reply:
                final_reply = self._compact_tool_answer(
                    tool_observations,
                    "reasoning iteration limit reached",
                )

        await self._memory.store(user_message_for_llm, final_reply)

        logger.info(f"Agent replied: {final_reply[:80]}...")
        await self._extractor.extract_and_store(user_message_for_llm, final_reply)
        return final_reply

    def _trim_history(self) -> None:
        max_msgs = settings.context_window_size
        if len(self._history) > max_msgs:
            self._history = self._history[-max_msgs:]

    def clear_history(self) -> None:
        self._history = []
        logger.info("Conversation history cleared")

    def _terminal_history_key(self, device_id: str) -> str:
        return f"terminal_chat::{device_id}::history"

    def _load_terminal_history(self, device_id: str) -> list[dict]:
        raw = self._memory.get_fact(self._terminal_history_key(device_id))
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [
                    {
                        "role": item.get("role", "user"),
                        "content": item.get("content", ""),
                    }
                    for item in data
                    if isinstance(item, dict) and item.get("content")
                ]
        except Exception:
            logger.warning(f"Could not parse terminal chat history for {device_id}")
        return []

    def _store_terminal_history(self, device_id: str, messages: list[dict]) -> None:
        trimmed = [
            {
                "role": item.get("role", "user"),
                "content": str(item.get("content", ""))[:2000],
            }
            for item in messages[-12:]
        ]
        self._memory.store_fact(
            self._terminal_history_key(device_id), json.dumps(trimmed)
        )

    async def run_terminal_chat(
        self, device_id: str, device_name: str, user_message: str
    ) -> str:
        system = (
            f"You are Vera running on Vera Desk.\n"
            f"You are currently speaking with the paired sovereign terminal '{device_name}' "
            f"(device_id: {device_id}).\n"
            f"This chat is local to that terminal and is separate from the owner control chat.\n"
            f"Rules:\n"
            f"- Be conversational and natural\n"
            f"- No markdown, no bullet points, no asterisks\n"
            f"- Do not claim this terminal is the owner control chat\n"
            f"- Do not rely on or mention unrelated owner-private chat history\n"
            f"- If the user wants to talk to another node, tell them to use /peer <device> <question>\n"
        )
        history = self._load_terminal_history(device_id)
        messages = (
            [{"role": "system", "content": system}]
            + history
            + [{"role": "user", "content": user_message}]
        )
        task_type = self._llm.classify_task(user_message)
        result = await self._llm.chat(
            messages=messages,
            tools=None,
            temperature=0.3,
            task_type=task_type,
            offline=self._offline_mode_for(),
        )
        reply = (
            result.get("content") or ""
        ).strip() or "I couldn't form a reply just now."
        self._store_terminal_history(
            device_id,
            history
            + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            ],
        )
        return reply
