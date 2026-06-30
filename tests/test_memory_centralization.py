import json
import sqlite3

import pytest

from core.agent import VeraAgent
from core.router import ModelRouter, TaskType
from core.task_queue import Step, Task, TaskQueue


class MemoryStub:
    def __init__(self):
        self.stored = []

    async def store(self, user_message, agent_reply):
        self.stored.append((user_message, agent_reply))

    async def recall(self, query, n_results=3):
        return ""

    def get_user_summary(self):
        return ""

    def load_recent_conversations(self, n=6):
        return [
            {"user": "We were discussing taxes", "assistant": "Yes, we were planning next steps.", "timestamp": "2026-04-01T10:00:00"},
        ]

    def load_recent_messages(self, n_pairs=6):
        return [
            {"role": "user", "content": "We were discussing taxes"},
            {"role": "assistant", "content": "Yes, we were planning next steps."},
        ]


class TaskQueueMemoryStub(MemoryStub):
    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path

    def _get_db(self):
        return sqlite3.connect(self.db_path)


class MemoryWithRecallStub(MemoryStub):
    async def recall(self, query, n_results=3):
        return "Owner context: Frank is waiting on local organizer actions."


class LLMStub:
    def __init__(self):
        self.calls = []
        self.stream_calls = []

    def classify_task(self, message):
        return TaskType.GENERAL

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": "Continuing the thread.", "tool_calls": []}

    async def stream_chat(self, **kwargs):
        self.stream_calls.append(kwargs)
        yield "Hello "
        yield "again."


class SearchLLMStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": "No reminders are currently set.", "tool_calls": []}


class SearchToolStub:
    name = "tavily_search"
    description = "search"

    def __init__(self, result="Reminder search results"):
        self.calls = []
        self.result = result

    async def execute(self, query):
        self.calls.append(query)
        return self.result


class WebSearchToolStub(SearchToolStub):
    name = "web_search"


class BrowserToolStub:
    name = "browse_url"
    description = "browse"

    def __init__(self, result="Page: Example\nURL: https://example.com\n\nExample page content"):
        self.calls = []
        self.result = result

    async def execute(self, payload):
        self.calls.append(payload)
        return self.result


class BrowseLLMStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {"content": "The site describes Global Pulse World and includes a main landing page summary.", "tool_calls": []}


class LLMToolCallStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "content": "",
                "tool_calls": [{"name": "send_email", "input": '{"to":"me@example.com","subject":"Approval test","body":"Hello"}'}],
            }
        return {"content": "Done after approval.", "tool_calls": []}


class LLMEmptyEmailToolCallStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "content": "",
                "tool_calls": [{"name": "send_email", "input": ""}],
            }
        return {"content": "Email prepared.", "tool_calls": []}


class RepeatingSearchLLMStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools") is None:
            return {"content": "Benchmark answer from gathered search results.", "tool_calls": []}
        if len([call for call in self.calls if call.get("tools") is not None]) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": "tavily_search",
                        "input": "best AI personal finance tools 2026 features and benchmarks",
                    }
                ],
            }
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "tavily_search",
                    "input": "best AI personal finance tools 2026 features and gaps",
                }
            ],
        }


class BenchmarkLLMStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "content": "Current benchmark: competitors handle categorization and alerts. Glaring gaps: reconciliation and exports. MVP hardening: bounded review and reports. Later: bank sync.",
            "tool_calls": [],
        }


class AutonomousTaskLLMStub(LLMStub):
    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if "Break this request" in prompt:
            return {
                "content": json.dumps(
                    [
                        {
                            "description": "Search PulseChat",
                            "tool": "tavily_search",
                            "input": "PulseChat online",
                            "requires_internet": True,
                        },
                        {
                            "description": "Write report",
                            "tool": "llm",
                            "input": "Write the report using this research:\n{previous_result}",
                            "requires_internet": False,
                        },
                    ]
                ),
                "tool_calls": [],
            }
        if "Final completed response" in prompt:
            return {"content": "Final completed report from gathered research.", "tool_calls": []}
        return {"content": "Intermediate report from search result.", "tool_calls": []}


class RouteToolStub:
    name = "route_to_device"
    description = "route"

    def __init__(self):
        self.calls = []

    async def execute(self, payload):
        self.calls.append(payload)
        return "sent"


class AskPeerRouteToolStub(RouteToolStub):
    async def execute(self, payload):
        self.calls.append(payload)
        return "Android remembers the inbox workflow follow-up."


class InterceptorStub:
    class Result:
        def __init__(self, has_explicit_target, target_phrase=None, status="none", clarification_msg=None):
            self.has_explicit_target = has_explicit_target
            self.target_phrase = target_phrase
            self.device_id = "device_android" if target_phrase else None
            self.canonical_name = "Android Device" if target_phrase else None
            self.status = status
            self.clarification_msg = clarification_msg

    def detect_and_resolve(self, message):
        lowered = message.lower()
        if "android" in lowered:
            return self.Result(True, target_phrase="android", status="resolved")
        if "phone" in lowered:
            return self.Result(True, target_phrase="phone", status="resolved")
        return self.Result(False)

    def build_routing_context(self, resolution):
        return ""


class ContextInjectingInterceptorStub(InterceptorStub):
    def build_routing_context(self, resolution):
        return "\n[MESH ROUTING CONTEXT]\nINSTRUCTION: Call route_to_device with target='android'\n"


class SyncApprovalTool:
    name = "send_email"
    description = "send email"
    requires_confirmation = True

    def __init__(self):
        self.calls = []

    def execute(self, payload):
        self.calls.append(payload)
        return "✅ Email sent to frankkoine@gmail.com\nSubject: Approval test"


class SafeReadTool:
    name = "search_email"
    description = "search email"

    def __init__(self):
        self.calls = []

    async def execute(self, payload):
        self.calls.append(payload)
        return "Found safe read result."


class FakeCalendarTool:
    name = "google_calendar"
    description = "calendar"

    def __init__(self):
        self.calls = []

    async def execute(self, payload):
        self.calls.append(payload)
        return f"calendar {payload.get('action')}"


class SafetyStub:
    def __init__(self):
        self.actions = []

    def classify(self, tool_name):
        from core.safety import SafetyTier
        return SafetyTier.NOTIFY

    async def process(self, action, executor):
        self.actions.append(action)
        return await executor()


@pytest.mark.asyncio
async def test_agent_uses_central_recent_messages_for_context():
    memory = MemoryStub()
    llm = LLMStub()
    agent = VeraAgent(llm=llm, tools=[], memory=memory)

    reply = await agent.run("What was I saying?")

    assert reply == "Continuing the thread."
    sent_messages = llm.calls[0]["messages"]
    assert any(msg.get("content") == "We were discussing taxes" for msg in sent_messages)
    assert any(msg.get("content") == "Yes, we were planning next steps." for msg in sent_messages)


@pytest.mark.asyncio
async def test_force_search_path_keeps_memory_and_recent_context():
    memory = MemoryWithRecallStub()
    llm = SearchLLMStub()
    tavily = SearchToolStub("Reminder platform search results")
    agent = VeraAgent(llm=llm, tools=[tavily, WebSearchToolStub()], memory=memory)

    reply = await agent.run("What happened in the latest city budget meeting?")

    assert reply == "No reminders are currently set."
    sent_messages = next(
        call["messages"]
        for call in llm.calls
        if "Owner context" in call["messages"][0]["content"]
    )
    system_message = sent_messages[0]["content"]
    assert "Owner context: Frank is waiting on local organizer actions." in system_message
    assert any(msg.get("content") == "We were discussing taxes" for msg in sent_messages)
    assert any("Live search results:" in msg.get("content", "") for msg in sent_messages if msg.get("role") == "user")


def test_reminder_questions_do_not_force_search():
    agent = VeraAgent(llm=LLMStub(), tools=[], memory=MemoryStub())

    assert agent._should_force_search("Did you set any reminders for me?") is False
    assert agent._should_force_search("What's on my calendar today?") is False


def test_agent_detects_explicit_bare_domain_browse_requests():
    agent = VeraAgent(llm=LLMStub(), tools=[], memory=MemoryStub())

    assert (
        agent._extract_browse_url_request("Browse the website globalpulseworld.xyz")
        == "globalpulseworld.xyz"
    )
    assert (
        agent._extract_browse_url_request("Can you open https://globalpulseworld.xyz/about?")
        == "https://globalpulseworld.xyz/about"
    )
    assert agent._extract_browse_url_request("What is globalpulseworld.xyz?") is None


@pytest.mark.asyncio
async def test_agent_browses_explicit_website_before_standard_llm_reply():
    memory = MemoryStub()
    llm = BrowseLLMStub()
    browser = BrowserToolStub(
        "Page: Global Pulse World\nURL: https://globalpulseworld.xyz\n\nGlobal Pulse World landing page."
    )
    agent = VeraAgent(llm=llm, tools=[browser], memory=memory)

    reply = await agent.run("Browse the website globalpulseworld.xyz")

    assert reply == "The site describes Global Pulse World and includes a main landing page summary."
    assert browser.calls == [{"input": "globalpulseworld.xyz"}]
    browse_call = next(
        call
        for call in llm.calls
        if "Browser observation:" in call["messages"][0]["content"]
    )
    assert browse_call["tools"] is None


@pytest.mark.asyncio
async def test_agent_synthesizes_when_search_tool_calls_repeat():
    memory = MemoryStub()
    llm = RepeatingSearchLLMStub()
    tavily = SearchToolStub("Direct answer: AI finance tools emphasize categorization, forecasting, alerts, and reports.")
    agent = VeraAgent(llm=llm, tools=[tavily], memory=memory)

    reply = await agent.run("Use available tools for AI personal finance landscape")

    assert reply == "Benchmark answer from gathered search results."
    assert tavily.calls == ["best AI personal finance tools 2026 features and benchmarks"]
    assert any(
        "Do not call any more tools" in call["messages"][0]["content"]
        for call in llm.calls
        if call.get("tools") is None
    )


@pytest.mark.asyncio
async def test_benchmark_research_path_runs_bounded_searches_without_tool_recursion():
    memory = MemoryStub()
    llm = BenchmarkLLMStub()
    tavily = SearchToolStub("Direct answer: benchmark source results")
    agent = VeraAgent(llm=llm, tools=[tavily], memory=memory)

    reply = await agent.run("Benchmark FinanceOS against personal finance tools and list glaring gaps")

    assert "Current benchmark" in reply
    assert len(tavily.calls) == 2
    assert all("finance" in query.lower() or "ynab" in query.lower() for query in tavily.calls)
    synthesis_call = llm.calls[0]
    assert synthesis_call["tools"] is None
    assert "Do not call tools" in synthesis_call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_task_queue_executes_research_to_final_deliverable(tmp_path, monkeypatch):
    from core import task_queue as task_queue_module

    memory = TaskQueueMemoryStub(tmp_path / "tasks.db")
    llm = AutonomousTaskLLMStub()
    tavily = SearchToolStub("PulseChat research result")
    queue = TaskQueue(memory=memory, llm=llm, tools={"tavily_search": tavily})

    async def online():
        return True

    monkeypatch.setattr(queue, "_check_internet", online)

    task = await queue.plan("Research PulseChat online and provide a response")
    reply = await queue.execute(task)

    assert reply == "Final completed report from gathered research."
    assert tavily.calls == ["PulseChat online"]
    final_call = llm.calls[-1]
    assert final_call["task_type"] == TaskType.RESEARCH
    assert final_call["max_tokens"] == task_queue_module.settings.ollama_reasoning_max_tokens
    assert "Do not describe a future plan" in final_call["messages"][0]["content"]
    assert "PulseChat research result" in final_call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_task_queue_runs_safe_read_tools_as_autonomous(tmp_path, monkeypatch):
    from core.safety import SafetyTier

    memory = TaskQueueMemoryStub(tmp_path / "safe-tasks.db")
    llm = AutonomousTaskLLMStub()
    search_email = SafeReadTool()
    safety = SafetyStub()
    queue = TaskQueue(
        memory=memory,
        llm=llm,
        tools={"search_email": search_email},
        safety_gate=safety,
    )

    async def online():
        return True

    monkeypatch.setattr(queue, "_check_internet", online)
    task = Task(
        id="safe1",
        description="Search my inbox and summarize",
        steps=[
            Step(
                id="step1",
                description="Search inbox",
                tool="search_email",
                input='{"query":"invoice"}',
            )
        ],
        created_at="now",
    )

    reply = await queue.execute(task)

    assert reply == "Final completed report from gathered research."
    assert search_email.calls == ['{"query":"invoice"}']
    assert safety.actions[0].tool_name == "search_email"
    assert safety.actions[0].tier == SafetyTier.AUTONOMOUS


@pytest.mark.asyncio
async def test_task_queue_routes_side_effect_tools_through_approval(tmp_path, monkeypatch):
    from core.safety import SafetyTier

    memory = TaskQueueMemoryStub(tmp_path / "approval-tasks.db")
    llm = AutonomousTaskLLMStub()
    send_email = SyncApprovalTool()
    safety = SafetyStub()
    queue = TaskQueue(
        memory=memory,
        llm=llm,
        tools={"send_email": send_email},
        safety_gate=safety,
    )

    async def online():
        return True

    monkeypatch.setattr(queue, "_check_internet", online)
    task = Task(
        id="approve1",
        description="Send the report by email",
        steps=[
            Step(
                id="step1",
                description="Send report",
                tool="send_email",
                input='{"to":"me@example.com","subject":"Report","body":"Done"}',
            )
        ],
        created_at="now",
    )

    await queue.execute(task)

    assert send_email.calls == ['{"to":"me@example.com","subject":"Report","body":"Done"}']
    assert safety.actions[0].tool_name == "send_email"
    assert safety.actions[0].tier == SafetyTier.APPROVE


@pytest.mark.asyncio
async def test_task_queue_uses_input_sensitive_calendar_safety(tmp_path, monkeypatch):
    from core.safety import SafetyTier

    memory = TaskQueueMemoryStub(tmp_path / "calendar-tasks.db")
    llm = AutonomousTaskLLMStub()
    calendar = FakeCalendarTool()
    safety = SafetyStub()
    queue = TaskQueue(
        memory=memory,
        llm=llm,
        tools={"google_calendar": calendar},
        safety_gate=safety,
    )

    async def online():
        return True

    monkeypatch.setattr(queue, "_check_internet", online)
    task = Task(
        id="calendar1",
        description="Read then update calendar",
        steps=[
            Step(
                id="step1",
                description="List events",
                tool="google_calendar",
                input='{"action":"list"}',
            ),
            Step(
                id="step2",
                description="Create event",
                tool="google_calendar",
                input='{"action":"create","summary":"Review","start":"2026-05-15T09:00:00Z","end":"2026-05-15T09:30:00Z"}',
            ),
        ],
        created_at="now",
    )

    await queue.execute(task)

    assert [action.tier for action in safety.actions[:2]] == [
        SafetyTier.AUTONOMOUS,
        SafetyTier.APPROVE,
    ]
    assert calendar.calls[0] == {"action": "list"}
    assert calendar.calls[1]["action"] == "create"


def test_router_injects_openai_only_when_configured(monkeypatch):
    from core import router as router_module

    monkeypatch.setattr(router_module.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(router_module.settings, "openai_model", "gpt-4o-mini")

    router = ModelRouter()
    chain = router._inject_openai([("mistral:7b", "ollama", router._ollama_chat)])

    assert chain[-1][1] == "openai"
    assert chain[-1][0] == "gpt-4o-mini"


def test_router_ollama_chat_tuning_uses_fast_primary_model(monkeypatch):
    from core import router as router_module

    monkeypatch.setattr(router_module.settings, "openai_api_key", "")
    monkeypatch.setattr(router_module.settings, "ollama_model", "gemma4:e4b")
    monkeypatch.setattr(router_module.settings, "ollama_fallback_model", "qwen2.5-coder:3b")
    monkeypatch.setattr(router_module.settings, "ollama_chat_max_tokens", 256)
    monkeypatch.setattr(router_module.settings, "ollama_reasoning_max_tokens", 768)

    router = ModelRouter()

    assert [entry[0] for entry in router.offline_chain()] == ["gemma4:e4b", "qwen2.5-coder:3b"]
    assert router._ollama_max_tokens_for_task(TaskType.GENERAL, 2048) == 256
    assert router._ollama_max_tokens_for_task(TaskType.TOOLS, 2048) == 256
    assert router._ollama_max_tokens_for_task(TaskType.REASONING, 2048) == 768


def test_router_normalizes_ollama_cloud_urls_and_auth(monkeypatch):
    from core import router as router_module

    monkeypatch.setattr(router_module.settings, "ollama_base_url", "https://ollama.com/api")
    monkeypatch.setattr(router_module.settings, "ollama_api_key", "secret")

    router = ModelRouter()

    assert router._ollama_url("/chat") == "https://ollama.com/api/chat"
    assert router._ollama_url("/tags") == "https://ollama.com/api/tags"
    assert router._ollama_headers()["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_agent_streams_general_chat_and_persists_memory():
    memory = MemoryStub()
    llm = LLMStub()
    agent = VeraAgent(llm=llm, tools=[], memory=memory)

    chunks = [chunk async for chunk in agent.run_stream("Hello Vera")]

    assert "".join(chunks) == "Hello again."
    assert memory.stored[0] == ("Hello Vera", "Hello again.")
    assert llm.stream_calls
    sent_messages = llm.stream_calls[0]["messages"]
    assert any(msg.get("content") == "We were discussing taxes" for msg in sent_messages)


def test_agent_can_stream_only_simple_general_chat():
    memory = MemoryStub()
    llm = LLMStub()
    agent = VeraAgent(llm=llm, tools=[], memory=memory)

    assert agent.can_stream("Hello Vera") is True
    assert agent.can_stream("What is the latest news in Nairobi today?") is False


@pytest.mark.asyncio
async def test_router_stream_chat_prefers_local_ollama(monkeypatch):
    from core import router as router_module

    monkeypatch.setattr(router_module.settings, "openai_api_key", "")
    monkeypatch.setattr(router_module.settings, "ollama_model", "gemma4:e4b")
    monkeypatch.setattr(router_module.settings, "ollama_fallback_model", "qwen2.5-coder:3b")

    router = ModelRouter()

    async def fake_ollama_stream_chat(
        *, messages, model, temperature, max_tokens, base_url=None
    ):
        assert model == "gemma4:e4b"
        assert max_tokens == router_module.settings.ollama_chat_max_tokens
        assert base_url is None
        yield "Hello"
        yield " world"

    monkeypatch.setattr(router, "_ollama_stream_chat", fake_ollama_stream_chat)

    chunks = [
        chunk async for chunk in router.stream_chat(
            messages=[{"role": "user", "content": "Hello Vera"}],
            task_type=TaskType.GENERAL,
        )
    ]

    assert "".join(chunks) == "Hello world"


@pytest.mark.asyncio
async def test_ping_fast_path_uses_resolved_android_target():
    memory = MemoryStub()
    llm = LLMStub()
    route_tool = RouteToolStub()
    agent = VeraAgent(
        llm=llm,
        tools=[route_tool],
        memory=memory,
        target_interceptor=InterceptorStub(),
    )

    reply = await agent.run("ping my android")

    assert reply == "sent"
    assert route_tool.calls[0]["target"] == "android"


@pytest.mark.asyncio
async def test_ping_fast_path_normalizes_phone_phrase_without_interceptor():
    memory = MemoryStub()
    llm = LLMStub()
    route_tool = RouteToolStub()
    agent = VeraAgent(
        llm=llm,
        tools=[route_tool],
        memory=memory,
    )

    reply = await agent.run("ping the phone")

    assert reply == "sent"
    assert route_tool.calls[0]["target"] == "phone"


@pytest.mark.asyncio
async def test_ping_fast_path_beats_context_injection_and_skips_llm():
    memory = MemoryStub()
    llm = LLMStub()
    route_tool = RouteToolStub()
    agent = VeraAgent(
        llm=llm,
        tools=[route_tool],
        memory=memory,
        target_interceptor=ContextInjectingInterceptorStub(),
    )

    reply = await agent.run("Ping Android")

    assert reply == "sent"
    assert route_tool.calls[0]["target"] == "android"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_ask_peer_fast_path_routes_directly_and_skips_llm():
    memory = MemoryStub()
    llm = LLMStub()
    route_tool = AskPeerRouteToolStub()
    agent = VeraAgent(
        llm=llm,
        tools=[route_tool],
        memory=memory,
        target_interceptor=ContextInjectingInterceptorStub(),
    )

    reply = await agent.run("Ask Android about the inbox workflow follow-up")

    assert reply == "Android remembers the inbox workflow follow-up."
    assert route_tool.calls[0] == {
        "target": "android",
        "message_type": "ASK_PEER",
        "payload": {"prompt": "the inbox workflow follow-up"},
    }
    assert llm.calls == []


@pytest.mark.asyncio
async def test_agent_executes_sync_approval_tool_via_safety_gate():
    memory = MemoryStub()
    llm = LLMToolCallStub()
    tool = SyncApprovalTool()
    safety = SafetyStub()
    agent = VeraAgent(
        llm=llm,
        tools=[tool],
        memory=memory,
        safety_gate=safety,
    )

    reply = await agent.run("Send an email to me@example.com")

    assert reply == "✅ Email sent to frankkoine@gmail.com\nSubject: Approval test"
    assert json.loads(tool.calls[0]) == {
        "to": "me@example.com",
        "subject": "Approval test",
        "body": "Hello",
    }
    assert safety.actions
    assert safety.actions[0].tier.value == "approve"
    assert "Send email to me@example.com" in safety.actions[0].description


@pytest.mark.asyncio
async def test_agent_hydrates_empty_send_email_tool_input_from_user_request():
    memory = MemoryStub()
    llm = LLMEmptyEmailToolCallStub()
    tool = SyncApprovalTool()
    safety = SafetyStub()
    agent = VeraAgent(
        llm=llm,
        tools=[tool],
        memory=memory,
        safety_gate=safety,
    )

    await agent.run(
        'Send an email to frankkoine@gmail.com with subject "Approval test" and body "Testing owner mesh approval."'
    )

    payload = json.loads(tool.calls[0])
    assert payload["to"] == "frankkoine@gmail.com"
    assert payload["subject"] == "Approval test"
    assert payload["body"] == "Testing owner mesh approval."
    assert safety.actions[0].description == "Send email to frankkoine@gmail.com with subject 'Approval test'"


@pytest.mark.asyncio
async def test_agent_hydrates_send_email_account_from_user_request():
    memory = MemoryStub()
    llm = LLMEmptyEmailToolCallStub()
    tool = SyncApprovalTool()
    safety = SafetyStub()
    agent = VeraAgent(
        llm=llm,
        tools=[tool],
        memory=memory,
        safety_gate=safety,
    )

    await agent.run(
        'Send an email from privateemail to frankkoine@gmail.com with subject "Approval test" and body "Testing owner mesh approval."'
    )

    payload = json.loads(tool.calls[0])
    assert payload["account"] == "privateemail"
    assert "from privateemail" in safety.actions[0].description


@pytest.mark.asyncio
async def test_agent_hydrates_send_email_exec_account_alias_from_user_request():
    memory = MemoryStub()
    llm = LLMEmptyEmailToolCallStub()
    tool = SyncApprovalTool()
    safety = SafetyStub()
    agent = VeraAgent(
        llm=llm,
        tools=[tool],
        memory=memory,
        safety_gate=safety,
    )

    await agent.run(
        'Send an email from exec to frankkoine@gmail.com with subject "Test" and body "Sent from exec."'
    )

    payload = json.loads(tool.calls[0])
    assert payload["account"] == "exec"
    assert "from exec" in safety.actions[0].description


@pytest.mark.asyncio
async def test_agent_returns_terminal_send_email_observation_without_second_llm_turn():
    memory = MemoryStub()
    llm = LLMEmptyEmailToolCallStub()
    tool = SyncApprovalTool()
    safety = SafetyStub()
    agent = VeraAgent(
        llm=llm,
        tools=[tool],
        memory=memory,
        safety_gate=safety,
    )

    reply = await agent.run(
        'Send an email to frankkoine@gmail.com with subject "Approval test" and body "Testing owner mesh approval."'
    )

    assert reply == "✅ Email sent to frankkoine@gmail.com\nSubject: Approval test"
    assert tool.calls
    assert len(llm.calls) >= 1
