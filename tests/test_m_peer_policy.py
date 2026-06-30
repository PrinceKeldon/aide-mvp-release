import asyncio
from types import SimpleNamespace

import pytest

from mesh.coordinator import MeshCoordinator
from mesh.device_registry import DeviceRegistry
from mesh.peer_registry import SovereignPeerRegistry


class FakeMeshNode:
    def __init__(self):
        self.identity = SimpleNamespace(
            device_id="local_aide",
            device_name="Frank's AIDE",
            device_type=SimpleNamespace(value="full_node"),
        )
        self.handlers = {}

    def register_handler(self, intent_type, handler):
        self.handlers[intent_type] = handler


class FakeMemory:
    def log_action(self, *args, **kwargs):
        return None

    def add_to_semantic(self, *args, **kwargs):
        return None


class FakeRouter:
    def __init__(self):
        self.sent = []

    async def send(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return True


class FakeAgent:
    def __init__(self, reply="Done."):
        self.reply = reply
        self.calls = []

    async def run(self, prompt):
        self.calls.append(prompt)
        return self.reply


class FakeSafety:
    def __init__(self, auto_approve=True):
        self.auto_approve = auto_approve
        self.actions = []

    async def process(self, action, executor):
        self.actions.append(action)
        if self.auto_approve:
            return await executor()
        return "Action cancelled."


def _registry_with_peer(tmp_path, *, scope_id="research_partner", peer_id="marco_agent_xyz"):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_peer_agent(
        peer_agent_id=peer_id,
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id=scope_id,
        capabilities=["research"],
    )
    return registry


def test_sovereign_peer_registry_sanitizes_memory_for_strict_scope(tmp_path):
    registry = _registry_with_peer(tmp_path)
    peers = SovereignPeerRegistry(registry)

    result = peers.validate_task_request(
        peer_agent_id="marco_agent_xyz",
        task_type="research",
        context_type="explicit_facts",
        context_payload={
            "facts": ["budget draft"],
            "memory": "private owner memory",
            "related_context": "hidden context should be stripped",
        },
    )

    assert result.allowed is True
    assert result.sandbox_mode.value == "strict"
    assert result.sanitized_context == {
        "context_type": "explicit_facts",
        "data": {"facts": ["budget draft"]},
    }


def test_scope_with_no_context_rejects_context_payload(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.create_trust_scope(
        scope_id="no_context_scope",
        scope_name="No Context Scope",
        can_request_tasks=True,
        can_receive_results=True,
        allowed_task_types=["research"],
        allowed_context_types=["explicit_facts"],
        can_receive_context="none",
        sandbox_mode="strict",
    )
    registry.create_peer_agent(
        peer_agent_id="peer_none",
        public_key="xyz123",
        display_name="Peer None",
        trust_scope_id="no_context_scope",
    )
    peers = SovereignPeerRegistry(registry)

    result = peers.validate_task_request(
        peer_agent_id="peer_none",
        task_type="research",
        context_type="explicit_facts",
        context_payload={"facts": ["should not pass"]},
    )

    assert result.allowed is False
    assert result.reason == "scope does not permit any context payload"


def test_paused_peer_cannot_request_tasks(tmp_path):
    registry = _registry_with_peer(tmp_path)
    registry.pause_peer_agent("marco_agent_xyz", reason="Waiting for clearer instructions")
    peers = SovereignPeerRegistry(registry)

    result = peers.validate_task_request(
        peer_agent_id="marco_agent_xyz",
        task_type="research",
        context_type="explicit_facts",
        context_payload={"facts": ["public memo"]},
    )

    assert result.allowed is False
    assert result.reason == "peer paused"


@pytest.mark.asyncio
async def test_peer_task_request_handler_returns_sandbox_and_sanitized_context(tmp_path):
    registry = _registry_with_peer(tmp_path)
    coordinator = MeshCoordinator(
        mesh_node=FakeMeshNode(),
        agent=FakeAgent("Budget summary complete."),
        memory=FakeMemory(),
        message_router=FakeRouter(),
        task_engine=object(),
        registry=registry,
    )

    response = await coordinator._handle_peer_task_request(
        {
            "sender_id": "marco_agent_xyz",
            "parameters": {
                "payload": {
                    "request_id": "req_1",
                    "task_type": "research",
                    "context_type": "explicit_facts",
                    "title": "Research request",
                    "instruction": "Summarize this",
                    "context": {
                        "facts": ["budget draft"],
                        "memory": "private memory should be removed",
                    },
                }
            },
        }
    )

    assert response["status"] == "completed"
    assert response["scope_id"] == "research_partner"
    assert response["sandbox_mode"] == "strict"
    assert response["sanitized_context"] == {
        "context_type": "explicit_facts",
        "data": {"facts": ["budget draft"]},
    }
    assert response["result_summary"] == "Budget summary complete."
    assert response["result_data"] == {"response": "Budget summary complete."}

    task_log = registry.get_peer_task_log("req_1")
    assert task_log is not None
    assert '"memory"' not in task_log["context_provided"]
    assert task_log["current_state"] == "completed"
    assert task_log["sandbox_executed"] == 1
    assert task_log["scope_validated"] == 1
    assert task_log["result_summary"] == "Budget summary complete."


@pytest.mark.asyncio
async def test_peer_task_request_handler_queues_owner_approval_and_executes_after_grant(tmp_path):
    registry = _registry_with_peer(tmp_path, scope_id="calendar_coordination", peer_id="coord_peer")
    registry.create_device(
        device_id="coord_device",
        device_name="Coord Device",
        device_type="full_node",
        public_key="abcd",
    )
    registry.mark_paired("coord_device")
    registry.bind_mesh("coord_device", "127.0.0.1", 7433)
    registry.create_peer_agent(
        peer_agent_id="coord_peer",
        public_key="abcd1234",
        display_name="Coord Peer",
        owner_name="Marco",
        trust_scope_id="calendar_coordination",
        primary_device_id="coord_device",
        capabilities=["coordination"],
    )
    safety = FakeSafety(auto_approve=True)
    agent = FakeAgent("Calendar comparison prepared.")
    router = FakeRouter()
    coordinator = MeshCoordinator(
        mesh_node=FakeMeshNode(),
        agent=agent,
        memory=FakeMemory(),
        message_router=router,
        task_engine=object(),
        safety_gate=safety,
        registry=registry,
    )

    response = await coordinator._handle_peer_task_request(
        {
            "sender_id": "coord_peer",
            "parameters": {
                "payload": {
                    "request_id": "req_approval",
                    "task_type": "calendar_coordination",
                    "context_type": "schedule_windows",
                    "title": "Compare calendars",
                    "instruction": "Compare these availability windows.",
                    "context": {"windows": ["11:00-12:00", "14:00-15:00"]},
                }
            },
        }
    )

    assert response["status"] == "awaiting_local_approval"
    assert response["sandbox_mode"] == "strict"

    await asyncio.sleep(0)

    task_log = registry.get_peer_task_log("req_approval")
    assert task_log is not None
    assert task_log["current_state"] == "completed"
    assert task_log["responding_approval_state"] == "approved"
    assert task_log["sandbox_executed"] == 1
    assert task_log["result_summary"] == "Calendar comparison prepared."
    assert len(safety.actions) == 1
    assert safety.actions[0].tool_name == "peer_task_request"
    assert agent.calls == ["Compare these availability windows."]
    sent_types = [kwargs["message_type"] for _, kwargs in router.sent]
    assert sent_types == ["PEER_TASK_UPDATE", "PEER_TASK_UPDATE", "PEER_TASK_RESULT"]


@pytest.mark.asyncio
async def test_peer_task_update_requires_update_capability(tmp_path):
    registry = _registry_with_peer(tmp_path, scope_id="assistant_introduction", peer_id="intro_peer")
    registry.create_peer_task_log(
        request_id="req_update",
        requesting_peer_id="local_aide",
        responding_peer_id="intro_peer",
        task_type="introduction",
        current_state="accepted",
    )
    coordinator = MeshCoordinator(
        mesh_node=FakeMeshNode(),
        agent=None,
        memory=FakeMemory(),
        message_router=FakeRouter(),
        task_engine=object(),
        registry=registry,
    )

    response = await coordinator._handle_peer_task_update(
        {
            "sender_id": "intro_peer",
            "parameters": {
                "payload": {
                    "request_id": "req_update",
                    "state": "executing",
                    "summary": "Working on it",
                }
            },
        }
    )

    assert response == {"status": "error", "error": "scope does not permit task updates"}


@pytest.mark.asyncio
async def test_peer_task_cancel_marks_task_cancelled(tmp_path):
    registry = _registry_with_peer(tmp_path, peer_id="cancel_peer")
    registry.create_peer_task_log(
        request_id="req_cancel",
        requesting_peer_id="cancel_peer",
        responding_peer_id="local_aide",
        task_type="research",
        current_state="executing",
    )
    coordinator = MeshCoordinator(
        mesh_node=FakeMeshNode(),
        agent=None,
        memory=FakeMemory(),
        message_router=FakeRouter(),
        task_engine=object(),
        registry=registry,
    )

    response = await coordinator._handle_peer_task_cancel(
        {
            "sender_id": "cancel_peer",
            "parameters": {
                "payload": {
                    "request_id": "req_cancel",
                    "reason": "No longer needed",
                }
            },
        }
    )

    assert response == {"status": "cancelled", "request_id": "req_cancel"}
    task_log = registry.get_peer_task_log("req_cancel")
    assert task_log["current_state"] == "cancelled"
    assert task_log["rejection_reason"] == "No longer needed"
