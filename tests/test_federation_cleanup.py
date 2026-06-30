import asyncio
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bridges.telegram_bridge import TelegramBridge
from core.safety import SafetyGate
from identity.alias_registry import AliasRegistry
from identity.bootstrap import ensure_local_device_registered
from identity.interceptor import ExplicitTargetInterceptor
from identity.resolution_guard import ResolutionGuard
from interface.telegram_bot import TelegramInterface
from main import bootstrap_discovered_peer
from mesh.device_registry import DeviceRegistry
from mesh.identity import DeviceIdentity
from mesh.coordinator import MeshCoordinator
from mesh.message_router import MessageRouter
from mesh.orchestrator import OwnerMeshOrchestrator
from mesh.pairing import PairingManager
from mesh.task_engine import OwnerMeshTaskEngine, MeshTaskState
from mesh.ui.pairing_ui import build_pairing_payload
from tools.device_router_tool import DeviceRouterTool


class FakeMemory:
    def __init__(self):
        self.facts = {}
        self.logged = []

    def get_fact(self, key):
        return self.facts.get(key)

    def store_fact(self, key, value):
        self.facts[key] = value

    def log_action(self, action, tier, outcome, reversible=True):
        self.logged.append((action, tier, outcome, reversible))

    def get_all_facts(self):
        return dict(self.facts)


class FakePersistentApprovalMemory(FakeMemory):
    def __init__(self):
        super().__init__()
        self.pending_rows = {}

    def save_pending_approval(
        self,
        *,
        action_id,
        tool_name,
        description,
        tier,
        reversible,
        payload,
        created_at,
        expires_at,
        status="pending",
    ):
        self.pending_rows[action_id] = {
            "action_id": action_id,
            "tool_name": tool_name,
            "description": description,
            "tier": tier,
            "reversible": reversible,
            "payload": payload,
            "created_at": created_at,
            "expires_at": expires_at,
            "status": status,
            "decision": None,
            "decided_at": None,
            "resolved_by": None,
        }

    def load_pending_approvals(self):
        return [
            row.copy()
            for row in self.pending_rows.values()
            if row["status"] == "pending"
        ]

    def mark_pending_approval_resolved(self, action_id, approved, resolved_by=None):
        row = self.pending_rows[action_id]
        row["status"] = "approved" if approved else "denied"
        row["decision"] = int(approved)
        row["resolved_by"] = resolved_by
        row["decided_at"] = datetime.now(timezone.utc).isoformat()

    def mark_pending_approval_expired(self, action_id):
        row = self.pending_rows[action_id]
        row["status"] = "expired"
        row["decided_at"] = datetime.now(timezone.utc).isoformat()


class FakeTelegram:
    def __init__(self):
        self.messages = []
        self.keyboard_messages = []

    async def send_message(self, chat_id, text, parse_mode="Markdown"):
        self.messages.append(
            {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        )

    async def send_message_with_keyboard(self, text, keyboard, *, chat_id=None, parse_mode="Markdown"):
        self.keyboard_messages.append(
            {
                "chat_id": chat_id,
                "text": text,
                "keyboard": keyboard,
                "parse_mode": parse_mode,
            }
        )


class FakeMeshNode:
    def __init__(self):
        self.sent = []
        self.handlers = {}
        self.trusted_calls = []
        self.revoked_calls = []
        self.identity = SimpleNamespace(
            device_id="local_full_node",
            device_name="Vera Desk",
            device_type=SimpleNamespace(value="full_node"),
        )

    def register_handler(self, intent_type, handler):
        self.handlers[intent_type] = handler

    def trust_peer(self, agent_id, public_key):
        self.trusted_calls.append((agent_id, public_key))

    def revoke_peer(self, agent_id):
        self.revoked_calls.append(agent_id)

    async def send_intent(self, peer, intent_type, parameters):
        self.sent.append(
            {"peer": peer, "intent_type": intent_type, "parameters": parameters}
        )
        return {"status": "completed"}


class FakeMeshMemory:
    def __init__(self, db_path):
        self._db_path = str(db_path)
        self.logged = []
        self.semantic = []

    def _get_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def log_action(self, action, tier, outcome, reversible=True):
        self.logged.append((action, tier, outcome, reversible))

    def add_to_semantic(self, text, metadata=None):
        self.semantic.append((text, metadata or {}))


class FakeOwnerMeshRouter:
    def __init__(self, responses=None):
        self.sent = []
        self.responses = responses or {}

    async def send(self, to_device_id, message_type, payload, from_device_id="local_full_node"):
        self.sent.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        response = self.responses.get((to_device_id, message_type), {"status": "sent"})
        return response is not None

    async def send_with_response(self, to_device_id, message_type, payload, from_device_id="local_full_node"):
        self.sent.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        return self.responses.get((to_device_id, message_type))


class FakeMessage:
    def __init__(self, chat_id, text=""):
        self.chat_id = chat_id
        self.text = text
        self.replies = []
        self.chat = SimpleNamespace(send_action=self._send_action)
        self.actions = []

    async def reply_text(self, text, parse_mode=None):
        self.replies.append({"text": text, "parse_mode": parse_mode})

    async def _send_action(self, action):
        self.actions.append(action)


class FakeCtx:
    def __init__(self, args=None):
        self.args = args or []


class FakePlaceholder:
    def __init__(self, text="Thinking..."):
        self.text = text
        self.edits = []

    async def edit_text(self, text):
        self.text = text
        self.edits.append(text)

    async def delete(self):
        return None


class FakeStreamingMessage:
    def __init__(self):
        self.replies = []
        self.placeholder = None

    async def reply_text(self, text, parse_mode=None):
        self.replies.append({"text": text, "parse_mode": parse_mode})
        if self.placeholder is None:
            self.placeholder = FakePlaceholder(text)
            return self.placeholder
        return None


class FakeQueryMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeQueryMessage()
        self.answered = False
        self.markup_cleared = False

    async def answer(self):
        self.answered = True

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_cleared = True


def build_stack(tmp_path):
    db_path = tmp_path / "device_registry.db"
    registry = DeviceRegistry(db_path)
    aliases = AliasRegistry(db_path)
    router = MessageRouter(registry)
    return registry, aliases, router


def make_interface(memory, safety):
    interface = TelegramInterface.__new__(TelegramInterface)
    interface._agent = SimpleNamespace(_memory=memory)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._safety = safety
    interface._bridge = None
    interface._chat_id = None
    interface._app = SimpleNamespace(bot=SimpleNamespace(send_message=None))
    return interface


def test_telegram_interface_enables_concurrent_updates(monkeypatch):
    calls = {}

    class FakeBuilder:
        def token(self, token):
            calls["token"] = token
            return self

        def concurrent_updates(self, enabled):
            calls["concurrent_updates"] = enabled
            return self

        def build(self):
            return SimpleNamespace(
                add_handler=lambda handler: None,
                add_error_handler=lambda handler: None,
            )

    monkeypatch.setattr("interface.telegram_bot.Application.builder", lambda: FakeBuilder())

    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "999")
    safety = SafetyGate(memory)
    agent = SimpleNamespace(_memory=memory)
    onboarding = SimpleNamespace(is_complete=lambda: True)

    TelegramInterface(agent=agent, onboarding=onboarding, safety=safety)

    assert calls["concurrent_updates"] is True


def test_alias_registration_requires_existing_device(tmp_path):
    _, aliases, _ = build_stack(tmp_path)

    with pytest.raises(ValueError):
        aliases.register("missing-device", "Ghost")


def test_local_bootstrap_creates_registry_and_alias(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    identity = DeviceIdentity.generate("VERA")

    ensure_local_device_registered(identity, registry, aliases)

    device = registry.get_by_device_id(identity.device_id)
    assert device is not None
    assert aliases.resolve("mac") == identity.device_id


def test_local_bootstrap_reclaims_vera_desk_aliases_from_stale_device(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    stale_id = registry.create_device("Frank's MacBook", device_type="full_node")
    aliases.register(
        stale_id,
        "Vera Desk",
        aliases=["topd", "mac", "desk", "computer"],
        is_default=True,
    )

    identity = DeviceIdentity.generate("VERA")
    ensure_local_device_registered(identity, registry, aliases)

    stale_row = next(
        entry for entry in aliases.list_all() if entry["device_id"] == stale_id
    )
    local_row = next(
        entry for entry in aliases.list_all() if entry["device_id"] == identity.device_id
    )

    assert local_row["is_default"] is True
    assert aliases.resolve("mac") == identity.device_id
    assert stale_row["is_default"] is False
    assert stale_row["canonical_name"] == "Frank's MacBook"
    assert "mac" not in stale_row["aliases"]


def test_explicit_target_interceptor_keeps_hi_vera_local_without_routing_intent(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    local_identity = DeviceIdentity.generate("VERA")
    ensure_local_device_registered(local_identity, registry, aliases)

    remote_id = registry.create_device(
        device_name="VERA",
        device_type="full_node",
        public_key="aa" * 32,
    )
    aliases.register(remote_id, "VERA", aliases=["vera"])

    interceptor = ExplicitTargetInterceptor(aliases, ResolutionGuard(aliases))

    no_route = interceptor.detect_and_resolve("Hi Vera")
    route = interceptor.detect_and_resolve("Ask Vera hello")

    assert no_route.has_explicit_target is False
    assert route.has_explicit_target is True
    assert route.status == "resolved"
    assert route.device_id == remote_id


@pytest.mark.asyncio
async def test_handle_pair_registers_alias_before_return(tmp_path):
    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=FakeMemory(),
    )

    response = await bridge.handle_pair(123, "midas", "Midas")

    device_id = registry.get_device_id_by_telegram(123)
    assert device_id is not None
    assert aliases.resolve("midas") == device_id
    assert "Midas" in response


@pytest.mark.asyncio
async def test_route_to_local_alias_hits_registry_record(tmp_path):
    registry, aliases, router = build_stack(tmp_path)
    identity = DeviceIdentity.generate("VERA")
    ensure_local_device_registered(identity, registry, aliases)
    tool = DeviceRouterTool(aliases, ResolutionGuard(aliases), router)

    result = await tool.execute({"target": "mac", "message_type": "PING", "payload": {}})

    assert "Failed to deliver to Vera Desk" in result
    assert "not found" not in result


def test_qr_pairing_creates_trusted_identity_without_transport(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    local = DeviceIdentity.generate("Local Node")
    peer = DeviceIdentity.generate("Peer Node")
    manager = PairingManager(
        local,
        device_registry=registry,
        alias_registry=aliases,
    )

    assert manager.complete_pairing(build_pairing_payload(peer)) is True

    record = registry.get_by_device_id(peer.device_id)
    assert record is not None
    assert record["public_key"] == peer.public_key.hex()
    assert record["transport_type"] is None
    assert record["trust_level"] == "trusted"
    assert record["paired_at"] is not None
    assert record["trust_source"] == "qr_pairing"


def test_qr_paired_peer_can_later_bind_transport(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    local = DeviceIdentity.generate("Local Node")
    peer = DeviceIdentity.generate("Peer Node")
    manager = PairingManager(
        local,
        device_registry=registry,
        alias_registry=aliases,
    )
    manager.complete_pairing(build_pairing_payload(peer))

    registry.bind_telegram(peer.device_id, 321, "peer")
    rebound = registry.get_by_device_id(peer.device_id)

    assert rebound["telegram_chat_id"] == 321
    assert rebound["device_id"] == peer.device_id


def test_full_node_defaults_match_owner_mesh_executor_role(tmp_path):
    registry, _, _ = build_stack(tmp_path)

    device_id = registry.create_device(
        device_name="Planner Node",
        device_type="full_node",
        public_key="ab" * 32,
    )

    device = registry.get_by_device_id(device_id)

    assert device["can_execute"] == 1
    assert device["can_receive_memory"] == 1
    assert device["can_receive_context"] == "full"


def test_registry_trust_metadata_persists_and_revocation_blocks_active_queries(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    device_id = registry.create_device("Trusted Peer", device_type="full_node", public_key="ef" * 32)
    registry.bind_mesh(device_id, "10.0.0.9", 7440)
    registry.mark_paired(device_id, trust_source="qr_pairing", note="Paired from QR")

    trust = registry.get_trust_record(device_id)
    assert trust is not None
    assert trust["trust_level"] == "trusted"
    assert trust["paired_at"] is not None
    assert "Paired from QR" in (trust["trust_notes"] or "")
    assert [peer["device_id"] for peer in registry.list_trusted_peers()] == [device_id]
    assert [peer["device_id"] for peer in registry.list_active_devices("can_execute")] == [device_id]

    registry.revoke_trust(device_id, note="Owner revoked this peer")

    revoked = registry.get_trust_record(device_id)
    assert revoked is not None
    assert revoked["revoked_at"] is not None
    assert revoked["trust_level"] == "revoked"
    assert "Owner revoked this peer" in (revoked["trust_notes"] or "")
    assert registry.device_exists(device_id) is True
    assert registry.list_trusted_peers() == []
    assert registry.list_active_devices("can_execute") == []


@pytest.mark.asyncio
async def test_router_sends_to_mesh_bound_full_node(tmp_path):
    registry, _, router = build_stack(tmp_path)
    mesh_node = FakeMeshNode()
    router.set_mesh_node(mesh_node)
    device_id = registry.create_device(
        device_name="Peer Executor",
        device_type="full_node",
        public_key="cd" * 32,
    )
    registry.bind_mesh(device_id, "10.0.0.8", 9001)

    ok = await router.send(device_id, "TASK_REQUEST", {"task": "Summarise this"})

    assert ok is True
    assert mesh_node.sent[0]["peer"]["agent_id"] == device_id
    assert mesh_node.sent[0]["intent_type"] == "TASK_REQUEST"


@pytest.mark.asyncio
async def test_router_blocks_revoked_peer_delivery(tmp_path):
    registry, _, router = build_stack(tmp_path)
    mesh_node = FakeMeshNode()
    router.set_mesh_node(mesh_node)
    device_id = registry.create_device(
        device_name="Revoked Peer",
        device_type="full_node",
        public_key="de" * 32,
    )
    registry.bind_mesh(device_id, "10.0.0.10", 9001)
    registry.revoke_trust(device_id, note="revoked for test")

    ok = await router.send(device_id, "TASK_REQUEST", {"task": "Summarise this"})

    assert ok is False
    assert mesh_node.sent == []


@pytest.mark.asyncio
async def test_mesh_coordinator_tracks_task_lifecycle(tmp_path):
    memory = FakeMeshMemory(tmp_path / "mesh_tasks.db")
    mesh_node = FakeMeshNode()
    task_engine = OwnerMeshTaskEngine(memory)
    agent = SimpleNamespace(run=lambda task: asyncio.sleep(0, result=f"done:{task}"))
    coordinator = MeshCoordinator(
        mesh_node=mesh_node,
        agent=agent,
        memory=memory,
        message_router=None,
        task_engine=task_engine,
    )

    coordinator.register_all_handlers()
    result = await coordinator._handle_task_request({
        "sender_id": "peer_executor",
        "parameters": {
            "payload": {"task": "Prepare summary"},
        },
    })

    record = task_engine.get_task(result["task_id"])

    assert "TASK_REQUEST" in mesh_node.handlers
    assert result["status"] == "completed"
    assert record is not None
    assert record.state == MeshTaskState.COMPLETED.value
    assert record.result == "done:Prepare summary"


@pytest.mark.asyncio
async def test_mesh_coordinator_formats_packaged_workflow_result(tmp_path):
    memory = FakeMeshMemory(tmp_path / "mesh_tasks_workflows.db")
    mesh_node = FakeMeshNode()
    task_engine = OwnerMeshTaskEngine(memory)
    prompts = []

    async def run(task):
        prompts.append(task)
        return "## Summary\nA short answer.\n\n## Key Points\n- First\n- Second\n\n## Recommended Next Step\n- Act"

    coordinator = MeshCoordinator(
        mesh_node=mesh_node,
        agent=SimpleNamespace(run=run),
        memory=memory,
        message_router=None,
        task_engine=task_engine,
    )

    result = await coordinator._handle_task_request(
        {
            "sender_id": "peer_executor",
            "parameters": {
                "payload": {
                    "task": "Summarize this topic",
                    "workflow_type": "research_brief",
                    "context_notes": "Keep it concise.",
                },
            },
        }
    )

    record = task_engine.get_task(result["task_id"])

    assert prompts
    assert "Workflow: Research Brief" in prompts[0]
    assert "Keep it concise." in prompts[0]
    assert result["workflow_type"] == "research_brief"
    assert result["result_kind"] == "research_brief"
    assert result["workflow_display_name"] == "Research Brief"
    assert result["result"].startswith("# Research Brief")
    assert record is not None
    assert record.payload["workflow_type"] == "research_brief"
    assert record.payload["result_kind"] == "research_brief"
    assert record.payload["result_preview"] == "## Summary A short answer. ## Key Points - First - Second ## Recommended Next Step - Act"


@pytest.mark.asyncio
async def test_discovery_bootstrap_trusts_only_registry_trusted_peers(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    trusted = DeviceIdentity.generate("Trusted Peer")
    revoked = DeviceIdentity.generate("Revoked Peer")
    registry.create_device(
        device_id=trusted.device_id,
        device_name=trusted.device_name,
        device_type=trusted.device_type.value,
        public_key=trusted.public_key.hex(),
    )
    registry.mark_paired(trusted.device_id, trust_source="qr_pairing")
    registry.create_device(
        device_id=revoked.device_id,
        device_name=revoked.device_name,
        device_type=revoked.device_type.value,
        public_key=revoked.public_key.hex(),
    )
    registry.mark_paired(revoked.device_id, trust_source="qr_pairing")
    registry.revoke_trust(revoked.device_id, note="revoked before discovery")

    mesh_node = FakeMeshNode()
    await bootstrap_discovered_peer(
        {"agent_id": trusted.device_id, "address": "10.0.0.5", "port": 9001},
        device_registry=registry,
        mesh_node=mesh_node,
    )
    await bootstrap_discovered_peer(
        {"agent_id": revoked.device_id, "address": "10.0.0.6", "port": 9002},
        device_registry=registry,
        mesh_node=mesh_node,
    )

    assert registry.get_mesh_endpoint(trusted.device_id) == ("10.0.0.5", 9001)
    assert registry.get_mesh_endpoint(revoked.device_id) is None
    assert mesh_node.trusted_calls[0][0] == trusted.device_id
    assert mesh_node.revoked_calls == [revoked.device_id]


@pytest.mark.asyncio
async def test_mesh_coordinator_enforces_trust_and_context_policy(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    proxied = registry.create_device("Phone Peer", device_type="proxied_terminal", public_key="aa" * 32)
    revoked = registry.create_device("Revoked Peer", device_type="proxied_terminal", public_key="bb" * 32)
    registry.mark_paired(proxied, trust_source="qr_pairing")
    registry.mark_paired(revoked, trust_source="qr_pairing")
    registry.revoke_trust(revoked, note="blocked")

    memory = FakeMeshMemory(tmp_path / "mesh_policy.db")
    mesh_node = FakeMeshNode()
    coordinator = MeshCoordinator(
        mesh_node=mesh_node,
        agent=SimpleNamespace(run=lambda task: asyncio.sleep(0, result=f"done:{task}")),
        memory=memory,
        message_router=None,
        task_engine=OwnerMeshTaskEngine(memory),
        registry=registry,
    )

    denied_memory = await coordinator._handle_memory_sync(
        {"sender_id": proxied, "intent_type": "MEMORY_SYNC", "parameters": {"snippet": "hello"}}
    )
    denied_context = await coordinator._handle_ask_peer(
        {"sender_id": revoked, "intent_type": "ASK_PEER", "parameters": {"prompt": "What happened?"}}
    )

    assert denied_memory["error"] == "missing capability can_receive_memory"
    assert denied_context["error"] == "peer not trusted"


@pytest.mark.asyncio
async def test_mesh_coordinator_logs_peer_conversation_turns(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    peer_id = registry.create_device("Phone Peer", device_type="full_node", public_key="aa" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")

    memory = FakeMeshMemory(tmp_path / "mesh_peer_conversation.db")
    mesh_node = FakeMeshNode()
    coordinator = MeshCoordinator(
        mesh_node=mesh_node,
        agent=SimpleNamespace(run=lambda task: asyncio.sleep(0, result=f"done:{task}")),
        memory=memory,
        message_router=None,
        task_engine=OwnerMeshTaskEngine(memory),
        registry=registry,
    )

    result = await coordinator._handle_ask_peer(
        {
            "sender_id": peer_id,
            "intent_type": "ASK_PEER",
            "parameters": {
                "payload": {
                    "prompt": "What happened yesterday?",
                    "correlation_id": "corr-123",
                }
            },
        }
    )

    assert result == {
        "status": "completed",
        "response": "done:What happened yesterday?",
        "correlation_id": "corr-123",
    }
    conversation = registry.list_peer_conversation_logs(peer_id)
    assert [entry["role"] for entry in conversation] == ["peer", "local"]
    assert conversation[0]["text"] == "What happened yesterday?"
    assert conversation[1]["text"] == "done:What happened yesterday?"
    assert all(entry["correlation_id"] == "corr-123" for entry in conversation)


@pytest.mark.asyncio
async def test_owner_mesh_approval_escalates_to_next_device(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    first = registry.create_device("Phone One", device_type="proxied_terminal")
    second = registry.create_device("Phone Two", device_type="proxied_terminal")
    registry.bind_telegram(first, 101, "one")
    registry.bind_telegram(second, 202, "two")

    memory = FakeMeshMemory(tmp_path / "approval_mesh.db")
    task_engine = OwnerMeshTaskEngine(memory)
    router = FakeOwnerMeshRouter()
    orchestrator = OwnerMeshOrchestrator(
        local_device_id="local_full_node",
        registry=registry,
        router=router,
        task_engine=task_engine,
        approval_stage_timeout=0.01,
    )

    future = asyncio.get_running_loop().create_future()
    action = SimpleNamespace(
        action_id="",
        description="Approve sending email",
        tool_name="send_email",
        payload={},
    )

    await orchestrator.route_approval_request("approve42", action, future)
    await asyncio.sleep(0.03)

    approval_sends = [msg for msg in router.sent if msg["message_type"] == "APPROVAL_REQUEST"]
    assert [msg["to_device_id"] for msg in approval_sends[:2]] == [first, second]

    active = task_engine.list_active()
    assert active
    record = active[0]
    assert record.approval_device_id == second
    assert record.payload["approval_attempts"] == [first, second]


@pytest.mark.asyncio
async def test_owner_mesh_delegation_retries_next_executor(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    first = registry.create_device("Executor One", device_type="full_node", public_key="aa" * 32)
    second = registry.create_device("Executor Two", device_type="full_node", public_key="bb" * 32)
    registry.bind_mesh(first, "10.0.0.5", 9001)
    registry.bind_mesh(second, "10.0.0.6", 9002)

    memory = FakeMeshMemory(tmp_path / "delegate_mesh.db")
    task_engine = OwnerMeshTaskEngine(memory)
    router = FakeOwnerMeshRouter(
        responses={
            (first, "DELEGATE_TASK"): None,
            (second, "DELEGATE_TASK"): {"status": "completed", "result": "done remotely"},
        }
    )
    orchestrator = OwnerMeshOrchestrator(
        local_device_id="local_full_node",
        registry=registry,
        router=router,
        task_engine=task_engine,
    )

    result = await orchestrator.delegate_task("Prepare summary")

    assert result["status"] == "completed"
    assert result["executor_device_id"] == second
    sends = [msg for msg in router.sent if msg["message_type"] == "DELEGATE_TASK"]
    assert [msg["to_device_id"] for msg in sends] == [first, second]
    record = task_engine.get_task(result["task_id"])
    assert record is not None
    assert record.state == MeshTaskState.COMPLETED.value
    assert record.retries == 1


@pytest.mark.asyncio
async def test_owner_mesh_delegation_persists_workflow_metadata(tmp_path):
    registry, _, _ = build_stack(tmp_path)
    executor = registry.create_device("Executor", device_type="full_node", public_key="aa" * 32)
    registry.bind_mesh(executor, "10.0.0.5", 9001)

    memory = FakeMeshMemory(tmp_path / "delegate_mesh_workflows.db")
    task_engine = OwnerMeshTaskEngine(memory)
    router = FakeOwnerMeshRouter(
        responses={
            (
                executor,
                "DELEGATE_TASK",
            ): {
                "status": "completed",
                "result": "# Email Triage\n\n## Urgent\n- Client reply",
                "result_preview": "Urgent client reply needs attention",
                "result_kind": "email_digest",
                "workflow_type": "email_triage",
                "workflow_display_name": "Email Triage",
            },
        }
    )
    orchestrator = OwnerMeshOrchestrator(
        local_device_id="local_full_node",
        registry=registry,
        router=router,
        task_engine=task_engine,
    )

    result = await orchestrator.delegate_task(
        "Review this morning inbox",
        payload={"workflow_type": "email_triage", "context_notes": "Client-facing only."},
    )

    record = task_engine.get_task(result["task_id"])

    assert result["status"] == "completed"
    assert result["workflow_type"] == "email_triage"
    assert result["result_kind"] == "email_digest"
    assert record is not None
    assert record.payload["workflow_type"] == "email_triage"
    assert record.payload["workflow_display_name"] == "Email Triage"
    assert record.payload["result_kind"] == "email_digest"
    assert record.payload["result_preview"] == "Urgent client reply needs attention"


@pytest.mark.asyncio
async def test_start_first_run_begins_onboarding_and_persists_owner_chat():
    memory = FakeMemory()
    safety = SafetyGate(memory)
    interface = make_interface(memory, safety)
    started = {"called": 0}

    async def start():
        started["called"] += 1
        return "Hi! I'm AIDE.\n\nFirst: what's your name?"

    interface._onboarding = SimpleNamespace(
        is_complete=lambda: False,
        start=start,
    )
    msg = FakeMessage(chat_id=77, text="/start")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_start(interface, update, None)

    assert memory.get_fact("telegram_chat_id") == "77"
    assert msg.replies
    assert started["called"] == 1
    assert "what's your name?" in msg.replies[0]["text"].lower()


@pytest.mark.asyncio
async def test_start_completed_onboarding_replies_with_ready_message():
    memory = FakeMemory()
    memory.store_fact("user_name", "Frank")
    safety = SafetyGate(memory)
    interface = make_interface(memory, safety)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    msg = FakeMessage(chat_id=77, text="/start")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_start(interface, update, None)

    assert memory.get_fact("telegram_chat_id") == "77"
    assert msg.replies
    assert "AIDE online, Frank." in msg.replies[0]["text"]


@pytest.mark.asyncio
async def test_start_on_paired_terminal_does_not_present_owner_identity(tmp_path):
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "999")
    memory.store_fact("user_name", "TopD")
    safety = SafetyGate(memory)
    interface = make_interface(memory, safety)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)

    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
    )
    await bridge.handle_pair(123, "midas", "Midas")
    interface._bridge = bridge

    msg = FakeMessage(chat_id=123, text="/start")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_start(interface, update, None)

    assert "Midas is online." in msg.replies[-1]["text"]
    assert "TopD" not in msg.replies[-1]["text"]


@pytest.mark.asyncio
async def test_paired_terminal_free_text_does_not_fall_into_owner_chat(tmp_path):
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "999")
    memory.store_fact("user_name", "TopD")
    safety = SafetyGate(memory)
    interface = make_interface(memory, safety)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)

    calls = {"run": 0}

    async def run(_text):
        calls["run"] += 1
        return "owner-reply"

    async def run_terminal_chat(_device_id, device_name, _text):
        return f"{device_name} local reply"

    interface._agent = SimpleNamespace(
        _memory=memory,
        run=run,
        run_terminal_chat=run_terminal_chat,
        can_stream=lambda _text: False,
    )

    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
    )
    await bridge.handle_pair(123, "midas", "Midas")
    interface._bridge = bridge

    msg = FakeMessage(chat_id=123, text="Hi")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["run"] == 0
    assert msg.replies[-1]["text"] == "Midas local reply"
    assert "TopD" not in msg.replies[-1]["text"]


@pytest.mark.asyncio
async def test_paired_terminal_free_text_uses_terminal_scoped_chat(tmp_path):
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "999")
    safety = SafetyGate(memory)
    interface = make_interface(memory, safety)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)

    calls = {"terminal": []}

    async def run_terminal_chat(device_id, device_name, user_message):
        calls["terminal"].append((device_id, device_name, user_message))
        return f"{device_name} heard: {user_message}"

    interface._agent = SimpleNamespace(
        _memory=memory,
        run_terminal_chat=run_terminal_chat,
    )

    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
    )
    await bridge.handle_pair(123, "midas", "Midas")
    interface._bridge = bridge

    msg = FakeMessage(chat_id=123, text="Who am I?")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    terminal_id = registry.get_device_id_by_telegram(123)
    assert calls["terminal"] == [(terminal_id, "Midas", "Who am I?")]
    assert msg.replies[-1]["text"] == "Midas heard: Who am I?"


@pytest.mark.asyncio
async def test_pong_notifies_owner_channel(tmp_path):
    registry, aliases, router = build_stack(tmp_path)
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "999")
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
    )

    await bridge.handle_pair(123, "midas", "Midas")
    await bridge.handle_incoming(123, "/pong")

    assert any(message["chat_id"] == 999 for message in telegram.messages)


@pytest.mark.asyncio
async def test_terminal_approval_command_and_owner_callback_both_resolve(tmp_path):
    memory = FakeMemory()
    safety = SafetyGate(memory)
    future_terminal = asyncio.get_running_loop().create_future()
    safety._approval_callbacks["abc123"] = future_terminal
    safety._pending["abc123"] = SimpleNamespace(description="Send email")

    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
        safety_gate=safety,
    )
    await bridge.handle_pair(123, "midas", "Midas")
    await bridge.handle_incoming(123, "/approve abc123")

    assert future_terminal.done() and future_terminal.result() is True

    future_owner = asyncio.get_running_loop().create_future()
    safety._approval_callbacks["def456"] = future_owner
    safety._pending["def456"] = SimpleNamespace(description="Buy item")

    interface = make_interface(memory, safety)
    query = FakeQuery("approve:def456")
    update = SimpleNamespace(callback_query=query)

    await TelegramInterface._on_callback(interface, update, None)

    assert future_owner.done() and future_owner.result() is True
    assert query.markup_cleared is True


@pytest.mark.asyncio
async def test_terminal_approval_alias_command_resolves_pending_action(tmp_path):
    memory = FakeMemory()
    safety = SafetyGate(memory)
    future_terminal = asyncio.get_running_loop().create_future()
    safety._approval_callbacks["alias123"] = future_terminal
    safety._pending["alias123"] = SimpleNamespace(description="Send email")

    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
        safety_gate=safety,
    )
    await bridge.handle_pair(123, "midas", "Midas")
    await bridge.handle_incoming(123, "/approved alias123")

    assert future_terminal.done() and future_terminal.result() is True


@pytest.mark.asyncio
async def test_proxied_terminal_can_start_peer_thread_via_bridge(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    telegram = FakeTelegram()
    peer_id = registry.create_device("VERA", device_type="full_node", public_key="aa" * 32)
    registry.bind_mesh(peer_id, "10.0.0.8", 7432)
    aliases.register(peer_id, "VERA", aliases=["vera"])

    router = FakeOwnerMeshRouter(
        responses={
            (peer_id, "ASK_PEER"): {"status": "completed", "response": "Peer says hello."},
        }
    )
    router._telegram = telegram
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=FakeMemory(),
    )

    await bridge.handle_pair(123, "midas", "Midas")
    handled = await bridge.handle_incoming(123, "/peer VERA what changed?")

    terminal_id = registry.get_device_id_by_telegram(123)
    assert handled is True
    assert terminal_id is not None
    assert router.sent[-1]["to_device_id"] == peer_id
    assert router.sent[-1]["message_type"] == "ASK_PEER"
    assert router.sent[-1]["payload"]["proxied_from_device_id"] == terminal_id
    assert "Peer says hello." in telegram.messages[-1]["text"]
    assert "Peer thread: VERA" in telegram.messages[-1]["text"]


@pytest.mark.asyncio
async def test_proxied_terminal_plain_text_continues_active_peer_thread(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    telegram = FakeTelegram()
    peer_id = registry.create_device("VERA", device_type="full_node", public_key="aa" * 32)
    registry.bind_mesh(peer_id, "10.0.0.8", 7432)
    aliases.register(peer_id, "VERA", aliases=["vera"])

    router = FakeOwnerMeshRouter(
        responses={
            (peer_id, "ASK_PEER"): {"status": "completed", "response": "Peer says hello."},
        }
    )
    router._telegram = telegram
    memory = FakeMemory()
    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=memory,
    )
    await bridge.handle_pair(123, "midas", "Midas")

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = bridge
    interface._safety = SimpleNamespace()
    calls = {"run": 0}

    async def run(_text):
        calls["run"] += 1
        return "local"

    interface._agent = SimpleNamespace(_memory=memory, run=run, can_stream=lambda _text: False)

    await bridge.handle_incoming(123, "/peer VERA first question")
    msg = FakeMessage(chat_id=123, text="continue the thread")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["run"] == 0
    assert router.sent[-1]["payload"]["prompt"] == "continue the thread"
    assert "Peer says hello." in telegram.messages[-1]["text"]


@pytest.mark.asyncio
async def test_terminal_reconnect_normalizes_proxied_capabilities(tmp_path):
    registry, aliases, router = build_stack(tmp_path)
    telegram = FakeTelegram()
    router.set_telegram(telegram)
    device_id = registry.create_device("Midas", device_type="proxied_terminal")
    registry.bind_telegram(device_id, 123, "midas")
    registry.update_capabilities(
        device_id,
        can_execute=1,
        can_receive_memory=1,
        can_receive_context="full",
    )

    bridge = TelegramBridge(
        registry=registry,
        alias_registry=aliases,
        message_router=router,
        memory=FakeMemory(),
    )

    response = await bridge.handle_pair(123, "midas", "Midas")
    device = registry.get_by_device_id(device_id)

    assert "reconnected" in response
    assert device["can_execute"] == 0
    assert device["can_receive_memory"] == 0
    assert device["can_receive_context"] == "summary_only"


def test_candidate_executors_prefers_local_over_stale_remote(tmp_path):
    registry, aliases, _ = build_stack(tmp_path)
    local_identity = DeviceIdentity.generate("VERA")
    ensure_local_device_registered(local_identity, registry, aliases)
    registry.bind_mesh(local_identity.device_id, "127.0.0.1", 7432)

    stale_remote = registry.create_device(
        "Old Remote",
        device_type="full_node",
        public_key="aa" * 32,
    )
    registry.bind_mesh(stale_remote, "10.0.0.8", 7432)

    orchestrator = OwnerMeshOrchestrator(
        local_device_id=local_identity.device_id,
        registry=registry,
        router=FakeOwnerMeshRouter(),
        task_engine=OwnerMeshTaskEngine(FakeMeshMemory(tmp_path / "mesh_tasks.db")),
    )

    assert orchestrator.candidate_executors() == [local_identity.device_id, stale_remote]


@pytest.mark.asyncio
async def test_stream_reply_does_not_send_duplicate_final_message():
    interface = TelegramInterface.__new__(TelegramInterface)
    interface.STREAM_EDIT_INTERVAL_SECONDS = 0.2
    interface.STREAM_EDIT_MIN_CHARS = 12

    async def run_stream(_text):
        yield "Hello from VERA"

    interface._agent = SimpleNamespace(run_stream=run_stream)

    msg = FakeStreamingMessage()

    await TelegramInterface._stream_reply(interface, msg, "hello")

    assert msg.placeholder is not None
    assert msg.placeholder.edits[-1] == "Hello from VERA"
    assert len(msg.replies) == 1


@pytest.mark.asyncio
async def test_on_message_uses_one_shot_replies_when_telegram_streaming_disabled(monkeypatch):
    monkeypatch.setattr("interface.telegram_bot.settings.telegram_stream_replies", False)

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()

    calls = {"stream": 0, "run": 0}

    async def run(_text):
        calls["run"] += 1
        return "One shot reply"

    async def stream_reply(_msg, _text):
        calls["stream"] += 1

    interface._agent = SimpleNamespace(
        can_stream=lambda _text: True,
        run=run,
    )
    interface._stream_reply = stream_reply

    msg = FakeMessage(chat_id=77, text="hello")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["stream"] == 0
    assert calls["run"] == 1
    assert msg.replies[-1]["text"] == "One shot reply"


@pytest.mark.asyncio
async def test_owner_can_start_peer_thread_from_telegram_command():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")

    async def run(text):
        memory.store_fact("active_peer_thread_device_id", "device_android")
        memory.store_fact("active_peer_thread_device_name", "Android")
        return f"peer:{text}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory, run=run)

    msg = FakeMessage(chat_id=77, text="/peer Android what changed?")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_peer(interface, update, FakeCtx(["Android", "what", "changed?"]))

    assert msg.actions == ["typing"]
    assert msg.replies[-1]["text"].startswith("peer:Ask Android what changed?")
    assert "Peer thread: Android" in msg.replies[-1]["text"]
    assert "Automatic continuation: on" in msg.replies[-1]["text"]
    assert memory.get_fact("active_peer_thread_device_id") == "device_android"


@pytest.mark.asyncio
async def test_owner_can_continue_active_peer_thread_from_telegram():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Android")

    async def run(text):
        return f"peer:{text}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory, run=run)

    msg = FakeMessage(chat_id=77, text="/replypeer continue the thread")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_replypeer(interface, update, FakeCtx(["continue", "the", "thread"]))

    assert msg.actions == ["typing"]
    assert msg.replies[-1]["text"].startswith("peer:Ask Android continue the thread")
    assert "Peer thread: Android" in msg.replies[-1]["text"]
    assert memory.get_fact("active_peer_thread_mode") == "armed"


@pytest.mark.asyncio
async def test_replypeer_requires_active_thread():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory, run=None)

    msg = FakeMessage(chat_id=77, text="/replypeer hello")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_replypeer(interface, update, FakeCtx(["hello"]))

    assert "No active peer thread" in msg.replies[-1]["text"]


@pytest.mark.asyncio
async def test_owner_message_auto_routes_to_active_peer_thread():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Android")
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact("active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat())

    calls = {"run": []}

    async def run(text):
        calls["run"].append(text)
        return f"peer:{text}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory, run=run, can_stream=lambda _text: False)

    msg = FakeMessage(chat_id=77, text="continue from yesterday")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["run"] == ["Ask Android continue from yesterday"]
    assert msg.replies[-1]["text"].startswith("peer:Ask Android continue from yesterday")
    assert "Peer thread: Android" in msg.replies[-1]["text"]
    assert memory.get_fact("active_peer_thread_mode") == "armed"


@pytest.mark.asyncio
async def test_owner_message_local_prefix_prevents_peer_misroute(monkeypatch):
    monkeypatch.setattr("interface.telegram_bot.settings.telegram_stream_replies", False)

    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Android")
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact("active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat())

    calls = {"run": []}

    async def run(text):
        calls["run"].append(text)
        return f"local:{text}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory, run=run, can_stream=lambda _text: False)

    msg = FakeMessage(chat_id=77, text="local: do not route this to peer")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["run"] == ["do not route this to peer"]
    assert msg.replies[-1]["text"] == "local:do not route this to peer"


@pytest.mark.asyncio
async def test_owner_explicit_target_overrides_active_peer_thread(monkeypatch):
    monkeypatch.setattr("interface.telegram_bot.settings.telegram_stream_replies", False)

    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Midas")
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact("active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat())

    class FakeTargetResolution:
        has_explicit_target = True
        status = "resolved"
        device_id = "device_vera"

    class FakeInterceptor:
        def detect_and_resolve(self, text):
            assert text == "Hi Vera"
            return FakeTargetResolution()

    calls = {"run": []}

    async def run(text):
        calls["run"].append(text)
        return f"local:{text}"

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(
        _memory=memory,
        _target_interceptor=FakeInterceptor(),
        run=run,
        can_stream=lambda _text: False,
    )

    msg = FakeMessage(chat_id=77, text="Hi Vera")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._on_message(interface, update, None)

    assert calls["run"] == ["Hi Vera"]
    assert msg.replies[-1]["text"] == "local:Hi Vera"


@pytest.mark.asyncio
async def test_donepeer_disables_automatic_peer_ingestion():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Android")
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact("active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat())

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory)

    msg = FakeMessage(chat_id=77, text="/donepeer")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_donepeer(interface, update, FakeCtx())

    assert memory.get_fact("active_peer_thread_mode") == "idle"
    assert memory.get_fact("active_peer_thread_device_id") == ""
    assert "back to normal local chat" in msg.replies[-1]["text"]


@pytest.mark.asyncio
async def test_peerthread_reports_expired_thread_window():
    memory = FakeMemory()
    memory.store_fact("telegram_chat_id", "77")
    memory.store_fact("active_peer_thread_device_id", "device_android")
    memory.store_fact("active_peer_thread_device_name", "Android")
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact(
        "active_peer_thread_updated_at",
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
    )

    interface = TelegramInterface.__new__(TelegramInterface)
    interface._onboarding = SimpleNamespace(is_complete=lambda: True)
    interface._bridge = None
    interface._safety = SimpleNamespace()
    interface._agent = SimpleNamespace(_memory=memory)

    msg = FakeMessage(chat_id=77, text="/peerthread")
    update = SimpleNamespace(message=msg)

    await TelegramInterface._cmd_peerthread(interface, update, FakeCtx())

    assert "Automatic continuation: off (expired)" in msg.replies[-1]["text"]


@pytest.mark.asyncio
async def test_approval_timeout_defaults_to_30_minutes(monkeypatch):
    memory = FakePersistentApprovalMemory()
    monkeypatch.setattr("core.safety.settings.approval_timeout_seconds", 1800)
    safety = SafetyGate(memory)

    action = SimpleNamespace(
        action_id="timeout30",
        tool_name="send_email",
        description="Send email to frankkoine@gmail.com",
        tier=SimpleNamespace(value="approve"),
        reversible=True,
        payload={"input": '{"to":"frankkoine@gmail.com","subject":"Test","body":"Hello"}'},
    )

    async def request():
        return await safety._request_approval(
            action,
            lambda: asyncio.sleep(0, result="sent"),
        )

    task = asyncio.create_task(request())
    await asyncio.sleep(0)
    row = memory.pending_rows["timeout30"]
    created_at = datetime.fromisoformat(row["created_at"])
    expires_at = datetime.fromisoformat(row["expires_at"])

    assert int((expires_at - created_at).total_seconds()) == 1800

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_pending_approval_persists_and_executes_after_restart():
    memory = FakePersistentApprovalMemory()
    safety_before = SafetyGate(memory)

    action = SimpleNamespace(
        action_id="persist123",
        tool_name="send_email",
        description="Send email to frankkoine@gmail.com",
        tier=SimpleNamespace(value="approve"),
        reversible=True,
        payload={"input": '{"to":"frankkoine@gmail.com","subject":"Restart","body":"Hello"}'},
    )

    async def create_pending():
        return await safety_before._request_approval(
            action,
            lambda: asyncio.sleep(0, result="sent before restart"),
        )

    pending_task = asyncio.create_task(create_pending())
    await asyncio.sleep(0)
    assert memory.pending_rows["persist123"]["status"] == "pending"

    pending_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_task

    executed = []
    notifications = []

    async def run_restored(restored_action):
        executed.append(restored_action.action_id)
        return "sent after restart"

    async def notify(message):
        notifications.append(message)

    safety_after = SafetyGate(memory)
    safety_after.register_executor_builder(run_restored)
    safety_after.register_notify_callback(notify)
    safety_after.activate_pending_approvals()
    await safety_after.reannounce_pending_approvals()

    assert notifications == ["approval_request:persist123:Send email to frankkoine@gmail.com"]

    resolved = safety_after.resolve_approval("persist123", True, resolved_by="Android Device")
    assert resolved is True
    await asyncio.sleep(0.05)

    assert executed == ["persist123"]
    assert memory.pending_rows["persist123"]["status"] == "approved"
    assert memory.pending_rows["persist123"]["resolved_by"] == "Android Device"
