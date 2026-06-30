import sqlite3
from datetime import date, datetime

from fastapi.testclient import TestClient

from core.settings import settings
from daily_brief.schema import DailyBrief, DailyBriefItem, DailyBriefSummary, ItemType, Priority
from daily_brief.storage import load_daily_brief, save_daily_brief
from identity.alias_registry import AliasRegistry
from identity.bootstrap import ensure_local_device_registered
from interface.web import app, set_runtime
from mesh.device_registry import DeviceRegistry
from mesh.identity import DeviceIdentity
from mesh.task_engine import MeshTaskState, OwnerMeshTaskEngine


class FakeSafety:
    def __init__(self, should_resolve: bool = True) -> None:
        self.should_resolve = should_resolve
        self.calls = []

    def resolve_approval(self, action_id: str, approved: bool, resolved_by: str | None = None) -> bool:
        self.calls.append(
            {
                "action_id": action_id,
                "approved": approved,
                "resolved_by": resolved_by,
            }
        )
        return self.should_resolve


class FakeYourDayService:
    def __init__(self, brief: DailyBrief) -> None:
        self.brief = brief
        self.calls = []

    async def generate_and_deliver(self, target_date: date | None = None) -> DailyBrief:
        self.calls.append(target_date)
        save_daily_brief(self.brief)
        return self.brief

    async def reshare_saved_brief(self) -> None:
        self.calls.append("reshare")


class FakeDraftLLM:
    async def chat(self, messages):
        prompt = messages[0]["content"]
        if "Owner question:" in prompt:
            return "The client is waiting on a quick decision, so the main thing is to answer clearly and soon."
        return """
        {
          "recipient_name": "Client",
          "channel": "email",
          "subject": "Re: Need a quick answer",
          "body": "Hi, thanks for your note. I have this and will send a fuller response shortly.",
          "reason": "The sender is waiting on a quick answer."
        }
        """


class FakeAgent:
    def __init__(self) -> None:
        self._llm = FakeDraftLLM()
        self._tools = {
            "send_email": FakeSendEmailTool(),
            "reply_email": FakeReplyEmailTool(),
        }


class FakeSendEmailTool:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, input_str: str) -> str:
        self.calls.append(input_str)
        return "✅ Email sent to client@example.com\nFrom: fkoine@gmail.com\nSubject: Re: Need a quick answer"


class FakeReplyEmailTool:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, input_str: str) -> str:
        self.calls.append(input_str)
        return "✅ Reply sent to client@example.com\nFrom: fkoine@gmail.com\nSubject: Re: Need a quick answer"


class FakeMeshAdminNode:
    def __init__(self) -> None:
        self.trusted = []
        self.revoked = []
        self.outbound = []
        self.cleared = []

    def trust_peer(self, device_id, public_key) -> None:
        self.trusted.append((device_id, public_key))

    def revoke_peer(self, device_id) -> None:
        self.revoked.append(device_id)

    async def ensure_outbound_session(self, peer) -> None:
        self.outbound.append(peer)

    async def clear_outbound_session(self, device_id) -> None:
        self.cleared.append(device_id)


class FakeDiscovery:
    def __init__(self, peers=None) -> None:
        self._peers = peers or []

    def get_peers(self):
        return list(self._peers)


class FakeMeshRouter:
    def __init__(self) -> None:
        self.calls = []

    async def send_with_response(self, to_device_id, message_type, payload, from_device_id=""):
        self.calls.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        if message_type == "ASK_PEER":
            return {"status": "completed", "response": "Peer memory says hello."}
        return {"status": "pong", "device_id": to_device_id}


class FakeOwnerMesh:
    def __init__(self) -> None:
        self.calls = []
        self.task_log = []

    async def delegate_task(self, task, *, preferred_device_id=None, payload=None, origin_device_id=None):
        task_id = f"mesh-task-{len(self.calls) + 1}"
        self.calls.append(
            {
                "task": task,
                "preferred_device_id": preferred_device_id,
                "payload": payload,
                "origin_device_id": origin_device_id,
            }
        )
        self.task_log.insert(
            0,
            {
                "task_id": task_id,
                "description": task,
                "state": "completed",
                "origin_device_id": origin_device_id or "local",
                "origin_device_name": "Local Node",
                "assigned_device_id": preferred_device_id,
                "assigned_device_name": "Peer Node" if preferred_device_id else "mesh executor",
                "approval_device_id": None,
                "approval_device_name": None,
                "result": "mesh ok",
                "result_preview": "mesh ok",
                "retries": 0,
                "created_at": "2026-04-08T11:00:00+00:00",
                "updated_at": "2026-04-08T11:00:00+00:00",
                "payload": payload or {},
                "workflow_type": (payload or {}).get("workflow_type", "generic_task"),
                "requires_approval": bool((payload or {}).get("requires_approval")),
                "context_notes": (payload or {}).get("context_notes"),
            },
        )
        return {"status": "completed", "task_id": task_id, "executor_device_id": preferred_device_id, "result": "mesh ok"}


class FakeMeshMemory:
    def __init__(self, db_path) -> None:
        self._db_path = str(db_path)

    def _get_db(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn


class FactMemory:
    def __init__(self) -> None:
        self.facts = {}

    def store_fact(self, key, value):
        self.facts[key] = value

    def get_fact(self, key):
        return self.facts.get(key)


class FakeOwnerMeshRuntime:
    def __init__(self, db_path) -> None:
        self.calls = []
        self._task_engine = OwnerMeshTaskEngine(FakeMeshMemory(db_path))

    async def delegate_task(
        self,
        task,
        *,
        preferred_device_id=None,
        payload=None,
        origin_device_id=None,
        existing_task_id=None,
    ):
        self.calls.append(
            {
                "task": task,
                "preferred_device_id": preferred_device_id,
                "payload": payload,
                "origin_device_id": origin_device_id,
                "existing_task_id": existing_task_id,
            }
        )
        if existing_task_id:
            task_id = existing_task_id
            self._task_engine.transition(
                task_id,
                MeshTaskState.COMPLETED,
                assigned_device_id=preferred_device_id,
                result="mesh ok",
                payload_update={
                    **(payload or {}),
                    "result_preview": "mesh ok",
                    "result_kind": "mesh_result",
                    "approval_state": (payload or {}).get("approval_state", "approved"),
                },
            )
        else:
            task_id = self._task_engine.create_task(
                description=task,
                origin_device_id=origin_device_id or "local",
                assigned_device_id=preferred_device_id,
                payload={
                    **(payload or {}),
                    "task": task,
                    "result_preview": "mesh ok",
                    "result_kind": "mesh_result",
                },
                state=MeshTaskState.COMPLETED,
            )
        return {
            "status": "completed",
            "task_id": task_id,
            "executor_device_id": preferred_device_id,
            "result": "mesh ok",
            "result_preview": "mesh ok",
            "result_kind": "mesh_result",
            "workflow_type": (payload or {}).get("workflow_type", "generic_task"),
            "workflow_display_name": (payload or {}).get("workflow_display_name", "Generic Task"),
        }


class FakeDiagnosticsRouter:
    def recent_diagnostics(self):
        return {
            "configured": {
                "ollama_base_url": "https://ollama.com/api",
                "ollama_model": "gemma4:e4b",
                "ollama_fallback_model": "qwen2.5-coder:3b",
                "ollama_cloud": True,
            },
            "summary": {
                "total_runs": 2,
                "successful_runs": 1,
                "average_latency_ms": 842.4,
            },
            "runs": [
                {
                    "timestamp": "2026-04-06T15:00:00",
                    "task_type": "general",
                    "provider": "ollama",
                    "model": "gemma4:e4b",
                    "mode": "chat",
                    "success": True,
                    "offline": False,
                    "duration_ms": 842.4,
                    "metrics": {"eval_count": 123},
                    "error": None,
                }
            ],
        }


class FakeMeshOnlyRouter:
    async def send_with_response(self, *args, **kwargs):
        return {"status": "ok"}


def _sample_brief() -> DailyBrief:
    return DailyBrief(
        date=date.today().isoformat(),
        summary=DailyBriefSummary(
            headline="Good morning. Your inbox, schedule, and approvals are ready.",
            counts={
                "approval_requests": 1,
                "unread_emails": 1,
                "calendar_events": 1,
                "schedule_suggestions": 1,
            },
        ),
        items=[
            DailyBriefItem(
                id="approval_approve123",
                type=ItemType.APPROVAL_REQUEST,
                priority=Priority.HIGH,
                title="Approval: Send contract email",
                reason="Pending owner approval before AIDE can proceed.",
                content={
                    "approval_id": "approve123",
                    "action_description": "Send contract email",
                    "action_type": "send_email",
                    "consequence": "This action is paused until an approved owner device confirms it.",
                    "expires_at": "2026-04-02T07:30:00+00:00",
                },
                actions=["approve", "deny"],
                created_at=datetime.now(),
            ),
            DailyBriefItem(
                id="email_digest_1",
                type=ItemType.EMAIL_DIGEST,
                priority=Priority.MEDIUM,
                title="Inbox: Client follow-up",
                reason="Unread in the last 48 hours.",
                content={
                    "from": "Client <client@example.com>",
                    "subject": "Need a quick answer",
                    "summary": "The client wants a decision on the draft proposal.",
                    "account": "exec",
                    "uid": "email-1",
                    "body": "Can you confirm whether the proposal is approved?",
                },
                actions=["open_thread", "show_original", "draft_reply", "snooze", "dismiss"],
                created_at=datetime.now(),
            ),
            DailyBriefItem(
                id="calendar_1",
                type=ItemType.CALENDAR_AGENDA,
                priority=Priority.MEDIUM,
                title="Today’s agenda",
                reason="Pulled from approved owner calendars.",
                content={
                    "schedule": "09:00 Project sync; 14:00 Client call",
                },
                actions=[],
                created_at=datetime.now(),
            ),
            DailyBriefItem(
                id="draft_1",
                type=ItemType.DRAFT_MESSAGE,
                priority=Priority.MEDIUM,
                title="Reply to Client",
                reason="Prepared from an unread email that appears actionable today.",
                content={
                    "to": "client@example.com",
                    "subject": "Re: Need a quick answer",
                    "body": "Hi, thanks for your note. I have this and will reply shortly.",
                    "account": "fkoine",
                    "uid": "email-1",
                    "source_item_id": "email_digest_1",
                    "quoted_context": "Can you confirm whether the proposal is approved?",
                    "quoted_subject": "Need a quick answer",
                    "quoted_sender": "Client <client@example.com>",
                },
                actions=["send", "snooze", "dismiss"],
                created_at=datetime.now(),
            ),
        ],
        generated_at=datetime.now(),
    )


def test_your_day_page_renders_owner_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    set_runtime(agent=None, safety=None, your_day_service=None)

    client = TestClient(app)
    response = client.get("/your-day")

    assert response.status_code == 200
    assert "Your Day" in response.text
    assert "Inbox Digest" in response.text
    assert "Approvals" in response.text
    assert "Schedule Conflicts" in response.text
    assert "Mesh Activity" in response.text
    assert "Mesh Needs Attention" in response.text
    assert "Source Task Detail" in response.text
    assert "Calendar Sources" in response.text
    assert "FinanceOS" in response.text
    assert "Statement PDF" in response.text
    assert "Process Statement" in response.text
    assert "/api/finance/upload" in response.text
    assert "Delegated Work" in response.text
    assert "M-Peer" in response.text
    assert 'href="/finance"' in response.text
    assert "lets your AIDE work with another person's AIDE" in response.text


def test_mesh_page_renders_owner_trust_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "10.0.0.8", 9001)
    set_runtime(agent=None, safety=None, your_day_service=None, registry=registry, alias_registry=aliases, mesh_node=FakeMeshAdminNode())

    client = TestClient(app)
    response = client.get("/mesh")

    assert response.status_code == 200
    assert "Peer Trust" in response.text
    assert "Owner Mesh" in response.text
    assert "M-Peer" in response.text
    assert "lets your AIDE work with another person's AIDE" in response.text
    assert "Manual Mesh Bind" in response.text
    assert "Ask Device" in response.text
    assert "Task Archive" in response.text
    assert "All workflows" in response.text
    assert "All devices" in response.text
    assert "Waiting Approval" in response.text
    assert "Completed Today" in response.text
    assert "Running" in response.text
    assert "Archived" in response.text


def test_mesh_api_lists_and_updates_trusted_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "10.0.0.8", 9001)
    mesh_node = FakeMeshAdminNode()
    set_runtime(agent=None, safety=None, your_day_service=None, registry=registry, alias_registry=aliases, mesh_node=mesh_node)

    client = TestClient(app)

    peers_response = client.get("/api/mesh/peers")
    assert peers_response.status_code == 200
    peer_rows = peers_response.json()["peers"]
    assert any(peer["device_id"] == peer_id and peer["is_trusted"] for peer in peer_rows)

    rename_response = client.put(f"/api/mesh/peers/{peer_id}/name", json={"name": "Field Node"})
    assert rename_response.status_code == 200
    assert registry.get_by_device_id(peer_id)["device_name"] == "Field Node"
    assert aliases.get_canonical_name(peer_id) == "Field Node"

    toggle_response = client.put(
        f"/api/mesh/peers/{peer_id}/capabilities/can_receive_brief",
        json={"enabled": False},
    )
    assert toggle_response.status_code == 200
    assert registry.can(peer_id, "can_receive_brief") is False

    revoke_response = client.post(f"/api/mesh/peers/{peer_id}/revoke")
    assert revoke_response.status_code == 200
    assert registry.is_trusted(peer_id) is False
    assert mesh_node.revoked == [peer_id]

    restore_response = client.post(f"/api/mesh/peers/{peer_id}/restore")
    assert restore_response.status_code == 200
    assert registry.is_trusted(peer_id) is True
    assert mesh_node.trusted[0][0] == peer_id


def test_mesh_pairing_api_exposes_local_payload_and_accepts_peer_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    mesh_node = FakeMeshAdminNode()
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=mesh_node,
        mesh_identity=local,
    )

    client = TestClient(app)
    pairing_response = client.get("/api/mesh/pairing")
    assert pairing_response.status_code == 200
    pairing_payload = pairing_response.json()
    assert pairing_payload["device_id"] == local.device_id
    assert pairing_payload["qr_data_uri"].startswith("data:image/png;base64,")
    assert '"id"' in pairing_payload["payload"]

    peer = DeviceIdentity.generate("Peer Node")
    imported = client.post("/api/mesh/pairing", json={"payload": "not-a-valid-pairing-payload"})
    assert imported.status_code == 400

    from mesh.ui.pairing_ui import build_pairing_payload

    add_peer_response = client.post("/api/mesh/pairing", json={"payload": build_pairing_payload(peer)})
    assert add_peer_response.status_code == 200
    assert registry.device_exists(peer.device_id) is True
    assert registry.is_trusted(peer.device_id) is True
    assert mesh_node.trusted
    assert mesh_node.trusted[-1][0] == peer.device_id


def test_mesh_dashboard_reports_discovery_and_supports_ping_task_and_forget(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "10.0.0.8", 9001)
    discovery = FakeDiscovery([
        {"agent_id": peer_id, "address": "10.0.0.8", "port": 9001},
        {"agent_id": "stranger", "address": "10.0.0.99", "port": 7444},
    ])
    router = FakeMeshRouter()
    owner_mesh = FakeOwnerMesh()
    mesh_node = FakeMeshAdminNode()
    memory = FactMemory()
    set_runtime(
        agent=type("AgentStub", (), {"_memory": memory})(),
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=mesh_node,
        mesh_discovery=discovery,
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=router,
    )

    client = TestClient(app)

    peers_response = client.get("/api/mesh/peers")
    assert peers_response.status_code == 200
    payload = peers_response.json()
    assert len(payload["discovered"]) == 2
    assert any(row["device_id"] == peer_id and row["trusted"] for row in payload["discovered"])
    assert any(row["device_id"] == "stranger" and not row["known"] for row in payload["discovered"])
    assert payload["active_peer_thread"]["status"] == "idle"

    ping_response = client.post(f"/api/mesh/peers/{peer_id}/ping")
    assert ping_response.status_code == 200
    assert router.calls[0]["message_type"] == "PING"
    assert router.calls[0]["to_device_id"] == peer_id

    ask_response = client.post(
        f"/api/mesh/peers/{peer_id}/ask",
        json={"prompt": "What do you remember about the handoff?"},
    )
    assert ask_response.status_code == 200
    assert router.calls[1]["message_type"] == "ASK_PEER"
    assert router.calls[1]["payload"]["prompt"] == "What do you remember about the handoff?"
    assert router.calls[1]["payload"]["correlation_id"]
    assert ask_response.json()["response"] == "Peer memory says hello."
    assert len(ask_response.json()["conversation"]) == 2
    assert ask_response.json()["active_peer_thread"]["device_id"] == peer_id
    assert ask_response.json()["active_peer_thread"]["status"] == "armed"

    conversation_response = client.get(f"/api/mesh/peers/{peer_id}/conversation")
    assert conversation_response.status_code == 200
    conversation = conversation_response.json()["conversation"]
    assert [entry["role"] for entry in conversation] == ["local", "peer"]
    assert conversation[0]["text"] == "What do you remember about the handoff?"
    assert conversation[1]["text"] == "Peer memory says hello."
    assert memory.get_fact("active_peer_thread_device_id") == peer_id
    assert memory.get_fact("active_peer_thread_device_name") == "Peer Node"
    assert memory.get_fact("active_peer_thread_mode") == "armed"

    clear_thread_response = client.post("/api/mesh/active-peer-thread/clear")
    assert clear_thread_response.status_code == 200
    assert clear_thread_response.json()["active_peer_thread"]["status"] == "idle"
    assert memory.get_fact("active_peer_thread_device_id") == ""

    task_response = client.post(f"/api/mesh/peers/{peer_id}/test-task", json={"task": "mesh health"})
    assert task_response.status_code == 200
    assert owner_mesh.calls[0]["task"] == "mesh health"
    assert owner_mesh.calls[0]["preferred_device_id"] == peer_id
    assert task_response.json()["tasks"][0]["description"] == "mesh health"

    forget_response = client.post(f"/api/mesh/peers/{peer_id}/forget")
    assert forget_response.status_code == 200
    assert registry.device_exists(peer_id) is False
    assert aliases.get_canonical_name(peer_id) is None
    assert mesh_node.revoked == [peer_id]


def test_mesh_dashboard_supports_manual_mesh_bind_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    mesh_node = FakeMeshAdminNode()
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=mesh_node,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    bind_response = client.post(
        f"/api/mesh/peers/{peer_id}/bind-mesh",
        json={"host": "192.168.1.24", "port": 7432},
    )
    assert bind_response.status_code == 200
    assert registry.get_mesh_endpoint(peer_id) == ("192.168.1.24", 7432)
    assert mesh_node.trusted[0][0] == peer_id
    assert mesh_node.outbound == [
        {"agent_id": peer_id, "address": "192.168.1.24", "port": 7432}
    ]

    clear_response = client.post(f"/api/mesh/peers/{peer_id}/unbind-mesh")
    assert clear_response.status_code == 200
    assert registry.get_mesh_endpoint(peer_id) is None
    assert mesh_node.cleared == [peer_id]


def test_mesh_dashboard_can_create_and_list_owner_mesh_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMesh()
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    create_response = client.post(
        "/api/mesh/tasks",
        json={
            "task": "Summarize urgent inbox threads",
            "workflow_type": "email_triage",
            "target_device_id": peer_id,
            "requires_approval": False,
            "context_notes": "Focus on client-facing messages.",
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["task"]["description"] == "Summarize urgent inbox threads"
    assert payload["task"]["workflow_type"] == "email_triage"
    assert payload["task"]["requires_approval"] is False
    assert owner_mesh.calls[0]["preferred_device_id"] == peer_id
    assert owner_mesh.calls[0]["payload"]["context_notes"] == "Focus on client-facing messages."

    list_response = client.get("/api/mesh/tasks")
    assert list_response.status_code == 200
    tasks = list_response.json()["tasks"]
    assert tasks[0]["description"] == "Summarize urgent inbox threads"

    detail_response = client.get(f"/api/mesh/tasks/detail/{payload['task']['task_id']}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["task"]
    assert detail["description"] == "Summarize urgent inbox threads"
    assert detail["workflow_type"] == "email_triage"
    assert detail["assigned_device_id"] == peer_id
    assert detail["payload"]["context_notes"] == "Focus on client-facing messages."


def test_mesh_dashboard_blocks_approval_aware_task_until_approved(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks.db")
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    create_response = client.post(
        "/api/mesh/tasks",
        json={
            "task": "Review this draft",
            "workflow_type": "follow_up_draft",
            "target_device_id": peer_id,
            "requires_approval": True,
        },
    )
    assert create_response.status_code == 200
    created = create_response.json()
    task_id = created["task"]["task_id"]
    assert created["result"]["status"] == "awaiting_approval"
    assert created["task"]["state"] == "waiting_approval"
    assert created["task"]["can_approve"] is True
    assert owner_mesh.calls == []

    approve_response = client.post(f"/api/mesh/tasks/{task_id}/approve")
    assert approve_response.status_code == 200
    approved = approve_response.json()
    assert approved["task"]["state"] == "completed"
    assert owner_mesh.calls[0]["existing_task_id"] == task_id
    assert owner_mesh.calls[0]["preferred_device_id"] == peer_id


def test_mesh_dashboard_can_cancel_and_retry_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks_retry.db")
    task_id = owner_mesh._task_engine.create_task(
        description="Run this later",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "task": "Run this later",
            "workflow_type": "generic_task",
            "workflow_display_name": "Generic Task",
            "target_device_id": peer_id,
        },
        state=MeshTaskState.ROUTED,
    )
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    cancel_response = client.post(f"/api/mesh/tasks/{task_id}/cancel")
    assert cancel_response.status_code == 200
    cancelled = cancel_response.json()
    assert cancelled["task"]["state"] == "cancelled"
    assert cancelled["task"]["can_retry"] is True

    retry_response = client.post(f"/api/mesh/tasks/{task_id}/retry")
    assert retry_response.status_code == 200
    retried = retry_response.json()
    assert retried["task"]["state"] == "completed"
    assert owner_mesh.calls[0]["existing_task_id"] == task_id


def test_mesh_dashboard_lists_archived_tasks_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks_archive.db")
    archived_task_id = owner_mesh._task_engine.create_task(
        description="Old market brief",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "task": "Old market brief",
            "workflow_type": "research_brief",
            "workflow_display_name": "Research Brief",
            "result_preview": "Three signals to watch.",
        },
        state=MeshTaskState.COMPLETED,
    )
    archived_record = owner_mesh._task_engine.get_task(archived_task_id)
    archived_record.updated_at = "2026-04-01T09:00:00+00:00"
    archived_record.created_at = "2026-04-01T08:00:00+00:00"
    owner_mesh._task_engine._save(archived_record)

    active_task_id = owner_mesh._task_engine.create_task(
        description="Fresh research brief",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "task": "Fresh research brief",
            "workflow_type": "research_brief",
            "workflow_display_name": "Research Brief",
        },
        state=MeshTaskState.COMPLETED,
    )
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    task_response = client.get("/api/mesh/tasks")
    assert task_response.status_code == 200
    active_ids = [task["task_id"] for task in task_response.json()["tasks"]]
    assert archived_task_id not in active_ids
    assert active_task_id in active_ids

    archive_response = client.get("/api/mesh/tasks/archive")
    assert archive_response.status_code == 200
    archive = archive_response.json()["archive"]
    assert archive["count"] == 1
    assert archive["by_workflow"][0]["key"] == "research_brief"
    assert archive["by_workflow"][0]["tasks"][0]["task_id"] == archived_task_id

    detail_response = client.get(f"/api/mesh/tasks/detail/{archived_task_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()["task"]
    assert detail["archived_at"] is not None
    assert detail["workflow_type"] == "research_brief"

    manual_task_id = owner_mesh._task_engine.create_task(
        description="Cancelled draft",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "task": "Cancelled draft",
            "workflow_type": "follow_up_draft",
            "workflow_display_name": "Follow-up Draft",
        },
        state=MeshTaskState.CANCELLED,
    )
    archive_now_response = client.post(f"/api/mesh/tasks/{manual_task_id}/archive")
    assert archive_now_response.status_code == 200
    archive_after_manual = archive_now_response.json()["archive"]
    archived_ids = {
        task["task_id"]
        for group in archive_after_manual["by_workflow"]
        for task in group["tasks"]
    }
    assert manual_task_id in archived_ids


def test_mesh_archive_requires_backup_before_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks_backup.db")
    archived_task_id = owner_mesh._task_engine.create_task(
        description="Archived brief",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "task": "Archived brief",
            "workflow_type": "research_brief",
            "workflow_display_name": "Research Brief",
            "result_preview": "A compact exported result.",
        },
        state=MeshTaskState.COMPLETED,
    )
    archived_record = owner_mesh._task_engine.get_task(archived_task_id)
    archived_record.updated_at = "2026-04-01T09:00:00+00:00"
    owner_mesh._task_engine._save(archived_record)
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    delete_without_backup = client.post("/api/mesh/tasks/archive/delete", json={"backup_exported_at": "missing"})
    assert delete_without_backup.status_code == 400

    backup_response = client.post("/api/mesh/tasks/archive/backup")
    assert backup_response.status_code == 200
    backup_payload = backup_response.json()
    assert backup_payload["export"]["task_count"] == 1
    assert backup_payload["export"]["tasks"][0]["task_id"] == archived_task_id
    backup_token = backup_payload["backup_exported_at"]

    delete_response = client.post("/api/mesh/tasks/archive/delete", json={"backup_exported_at": backup_token})
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted_count"] == 1

    archive_response = client.get("/api/mesh/tasks/archive")
    assert archive_response.status_code == 200
    assert archive_response.json()["archive"]["count"] == 0


def test_mesh_task_can_replay_completed_result_into_your_day(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMesh()
    owner_mesh.task_log = [
        {
            "task_id": "mesh-task-99",
            "description": "Summarize market notes",
            "state": "completed",
            "origin_device_id": local.device_id,
            "origin_device_name": "Local Node",
            "assigned_device_id": peer_id,
            "assigned_device_name": "Peer Node",
            "approval_device_id": None,
            "approval_device_name": None,
            "result": "# Research Brief\n\n## Summary\nShort market update.",
            "result_preview": "Short market update.",
            "result_kind": "research_brief",
            "retries": 0,
            "created_at": "2026-04-08T11:00:00+00:00",
            "updated_at": "2026-04-08T11:05:00+00:00",
            "payload": {
                "workflow_type": "research_brief",
                "workflow_display_name": "Research Brief",
                "requires_approval": False,
                "context_notes": "Focus on signals that matter today.",
            },
            "workflow_type": "research_brief",
            "workflow_display_name": "Research Brief",
            "requires_approval": False,
            "context_notes": "Focus on signals that matter today.",
        }
    ]
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)

    response = client.post("/api/mesh/tasks/mesh-task-99/replay-to-your-day")
    assert response.status_code == 200
    payload = response.json()
    assert payload["your_day_item_id"] == "mesh_result_mesh-task-99"
    assert payload["tasks"][0]["replayed_to_your_day"] is True

    updated = load_daily_brief(date.today())
    replayed = next(item for item in updated["items"] if item["id"] == "mesh_result_mesh-task-99")
    assert replayed["type"] == "prepared_task"
    assert replayed["content"]["source"] == "owner_mesh"
    assert replayed["content"]["mesh_task_id"] == "mesh-task-99"
    assert replayed["content"]["workflow_display_name"] == "Research Brief"
    assert replayed["content"]["executor_device_name"] == "Peer Node"


def test_your_day_can_create_follow_up_from_replayed_mesh_result(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMesh()
    owner_mesh.task_log = [
        {
            "task_id": "mesh-task-100",
            "description": "Draft tomorrow agenda",
            "state": "completed",
            "origin_device_id": local.device_id,
            "origin_device_name": "Local Node",
            "assigned_device_id": peer_id,
            "assigned_device_name": "Peer Node",
            "approval_device_id": None,
            "approval_device_name": None,
            "result": "Agenda ready.",
            "result_preview": "Agenda ready.",
            "result_kind": "follow_up_draft",
            "retries": 0,
            "created_at": "2026-04-08T11:00:00+00:00",
            "updated_at": "2026-04-08T11:05:00+00:00",
            "payload": {
                "workflow_type": "follow_up_draft",
                "workflow_display_name": "Follow-up Draft",
                "requires_approval": False,
            },
            "workflow_type": "follow_up_draft",
            "workflow_display_name": "Follow-up Draft",
            "requires_approval": False,
        }
    ]
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)
    replay_response = client.post("/api/mesh/tasks/mesh-task-100/replay-to-your-day")
    assert replay_response.status_code == 200

    response = client.post("/api/your-day/items/mesh_result_mesh-task-100/actions/follow_up_task")
    assert response.status_code == 200
    payload = response.json()
    assert payload["created_item_id"].startswith("mesh_follow_up_")

    updated = load_daily_brief(date.today())
    replayed = next(item for item in updated["items"] if item["id"] == "mesh_result_mesh-task-100")
    follow_up = next(item for item in updated["items"] if item["id"] == payload["created_item_id"])
    assert replayed["content"]["follow_up_item_id"] == follow_up["id"]
    assert follow_up["type"] == "prepared_task"
    assert follow_up["content"]["source"] == "owner_mesh_follow_up"
    assert follow_up["content"]["mesh_task_id"] == "mesh-task-100"


def test_your_day_can_open_replayed_follow_up_draft_as_editable_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMesh()
    owner_mesh.task_log = [
        {
            "task_id": "mesh-task-101",
            "description": "Draft follow-up for supplier",
            "state": "completed",
            "origin_device_id": local.device_id,
            "origin_device_name": "Local Node",
            "assigned_device_id": peer_id,
            "assigned_device_name": "Peer Node",
            "approval_device_id": None,
            "approval_device_name": None,
            "result": "# Follow-up Draft\n\n## Request\nDraft follow-up for supplier\n\n## Subject\nRe: Supplier timing\n\n## Draft Body\nHi, thanks for the update. Can you confirm the revised delivery date?\n\n## Rationale\nWe need a concrete date before the client call.\n\n## Approval Notes\nReview before sending.",
            "result_preview": "Supplier follow-up draft ready.",
            "result_kind": "draft_message",
            "retries": 0,
            "created_at": "2026-04-08T11:00:00+00:00",
            "updated_at": "2026-04-08T11:05:00+00:00",
            "payload": {
                "workflow_type": "follow_up_draft",
                "workflow_display_name": "Follow-up Draft",
                "requires_approval": False,
            },
            "workflow_type": "follow_up_draft",
            "workflow_display_name": "Follow-up Draft",
            "requires_approval": False,
        }
    ]
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)
    replay_response = client.post("/api/mesh/tasks/mesh-task-101/replay-to-your-day")
    assert replay_response.status_code == 200

    response = client.post("/api/your-day/items/mesh_result_mesh-task-101/actions/open_as_draft")
    assert response.status_code == 200
    payload = response.json()
    assert payload["created_item_id"].startswith("msg_")

    updated = load_daily_brief(date.today())
    replayed = next(item for item in updated["items"] if item["id"] == "mesh_result_mesh-task-101")
    draft_item = next(item for item in updated["items"] if item["id"] == payload["created_item_id"])
    assert replayed["content"]["draft_item_id"] == draft_item["id"]
    assert draft_item["type"] == "draft_message"
    assert draft_item["content"]["subject"] == "Re: Supplier timing"
    assert "revised delivery date" in draft_item["content"]["body"]
    assert draft_item["actions"] == ["dismiss"]


def test_your_day_can_archive_source_task_from_replayed_mesh_result(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    registry.bind_mesh(peer_id, "192.168.1.24", 7432)
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks_cleanup.db")
    task_id = owner_mesh._task_engine.create_task(
        description="Summarize market notes",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "workflow_type": "research_brief",
            "workflow_display_name": "Research Brief",
            "requires_approval": False,
            "result_preview": "Short market update.",
            "result_kind": "research_brief",
        },
        state=MeshTaskState.COMPLETED,
    )
    owner_mesh._task_engine.transition(
        task_id,
        MeshTaskState.COMPLETED,
        result="# Research Brief\n\n## Summary\nShort market update.",
    )
    set_runtime(
        agent=None,
        safety=None,
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        mesh_node=FakeMeshAdminNode(),
        owner_mesh=owner_mesh,
        mesh_identity=local,
        router=FakeMeshRouter(),
    )

    client = TestClient(app)
    replay_response = client.post(f"/api/mesh/tasks/{task_id}/replay-to-your-day")
    assert replay_response.status_code == 200

    response = client.post(f"/api/your-day/items/mesh_result_{task_id}/actions/archive_source_task")
    assert response.status_code == 200

    record = owner_mesh._task_engine.get_task(task_id)
    assert record.archived_at is not None
    updated = load_daily_brief(date.today())
    replayed = next(item for item in updated["items"] if item["id"] == f"mesh_result_{task_id}")
    assert replayed["state"] == "dismissed"
    assert replayed["content"]["source_task_archive_reason"] == "your_day_cleanup"


def test_your_day_api_returns_saved_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=None, safety=FakeSafety(), your_day_service=None)
    monkeypatch.setattr(
        "interface.web.get_calendar_source_status",
        lambda target_date: [
            {
                "label": "Family",
                "provider": "ics",
                "ok": True,
                "today_event_count": 0,
                "message": "No events today or in the next 21 days.",
                "next_event": None,
            }
        ],
    )

    client = TestClient(app)
    response = client.get("/api/your-day")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["brief"]["summary"]["headline"].startswith("Good morning")
    assert payload["brief"]["summary"]["counts"]["approval_requests"] == 1
    assert payload["brief"]["items"][0]["type"] == "approval_request"
    assert payload["mesh_attention"] == []
    assert payload["calendar_status"][0]["label"] == "Family"


def test_your_day_api_exposes_owner_mesh_attention(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    aliases = AliasRegistry(tmp_path / "device_registry.db")
    local = DeviceIdentity.generate("Local Node")
    ensure_local_device_registered(local, registry, aliases)
    peer_id = registry.create_device("Peer Node", device_type="full_node", public_key="ab" * 32)
    registry.mark_paired(peer_id, trust_source="qr_pairing")
    owner_mesh = FakeOwnerMeshRuntime(tmp_path / "owner_mesh_tasks_attention.db")
    task_id = owner_mesh._task_engine.create_task(
        description="Approve external draft",
        origin_device_id=local.device_id,
        assigned_device_id=peer_id,
        payload={
            "workflow_type": "follow_up_draft",
            "workflow_display_name": "Follow-up Draft",
            "requires_approval": True,
            "approval_state": "pending",
            "result_preview": "Waiting on owner approval.",
        },
        state=MeshTaskState.WAITING_APPROVAL,
    )
    owner_mesh._task_engine.transition(
        task_id,
        MeshTaskState.WAITING_APPROVAL,
        approval_device_id=local.device_id,
    )
    set_runtime(
        agent=None,
        safety=FakeSafety(),
        your_day_service=None,
        registry=registry,
        alias_registry=aliases,
        owner_mesh=owner_mesh,
        mesh_identity=local,
    )

    client = TestClient(app)
    response = client.get("/api/your-day")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["mesh_attention"]) == 1
    assert payload["mesh_attention"][0]["attention_reason"] == "waiting_approval"
    assert payload["mesh_attention"][0]["workflow_display_name"] == "Follow-up Draft"


def test_your_day_approval_endpoint_uses_safety_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    safety = FakeSafety()
    set_runtime(agent=None, safety=safety, your_day_service=None)

    client = TestClient(app)
    response = client.post("/api/your-day/approvals/approve123", json={"approved": True})

    assert response.status_code == 200
    assert safety.calls == [
        {
            "action_id": "approve123",
            "approved": True,
            "resolved_by": "web_owner",
        }
    ]
    assert response.json()["status"] == "approved"


def test_your_day_refresh_endpoint_regenerates_brief(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    brief = _sample_brief()
    service = FakeYourDayService(brief)
    set_runtime(agent=None, safety=None, your_day_service=service)

    client = TestClient(app)
    response = client.post("/api/your-day/refresh")

    assert response.status_code == 200
    assert service.calls == [date.today()]
    payload = response.json()
    assert payload["ok"] is True
    assert payload["brief"]["summary"]["counts"]["unread_emails"] == 1


def test_your_day_item_action_creates_persisted_reply_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=FakeAgent(), safety=None, your_day_service=FakeYourDayService(_sample_brief()))

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/actions/draft_reply")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["created_item_id"].startswith("msg_")

    updated = load_daily_brief(date.today())
    email_item = next(item for item in updated["items"] if item["id"] == "email_digest_1")
    draft_item = next(item for item in updated["items"] if item["id"] == payload["created_item_id"])

    assert email_item["state"] == "drafted"
    assert email_item["content"]["draft_item_id"] == draft_item["id"]
    assert draft_item["state"] == "ready"
    assert draft_item["content"]["subject"] == "Re: Need a quick answer"
    assert updated["summary"]["counts"]["draft_messages"] == 2


def test_your_day_item_action_dismisses_item_and_refreshes_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=None, safety=None, your_day_service=None)

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/actions/dismiss")

    assert response.status_code == 200
    updated = load_daily_brief(date.today())
    email_item = next(item for item in updated["items"] if item["id"] == "email_digest_1")
    assert email_item["state"] == "dismissed"
    assert updated["summary"]["counts"]["unread_emails"] == 0


def test_your_day_item_action_shows_original_email_on_request(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=FakeAgent(), safety=None, your_day_service=None)
    monkeypatch.setattr(
        "interface.web.fetch_email_message_by_uid",
        lambda uid, account="": {
            "uid": uid,
            "from": "Client <client@example.com>",
            "subject": "Need a quick answer",
            "date": "Thu, 02 Apr 2026 09:00:00 +0000",
            "body": "Original full email body.",
        },
    )

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/actions/show_original")

    assert response.status_code == 200
    updated = load_daily_brief(date.today())
    email_item = next(item for item in updated["items"] if item["id"] == "email_digest_1")
    assert email_item["content"]["original_email"]["body"] == "Original full email body."


def test_your_day_item_action_opens_email_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=FakeAgent(), safety=None, your_day_service=None)
    monkeypatch.setattr(
        "interface.web.fetch_email_thread",
        lambda uid, account="": {
            "original": {
                "uid": uid,
                "from": "Client <client@example.com>",
                "subject": "Need a quick answer",
                "date": "Thu, 02 Apr 2026 09:00:00 +0000",
                "body": "Original full email body.",
            },
            "messages": [
                {
                    "uid": uid,
                    "from": "Client <client@example.com>",
                    "subject": "Need a quick answer",
                    "date": "Thu, 02 Apr 2026 09:00:00 +0000",
                    "snippet": "Original request.",
                },
                {
                    "uid": "email-2",
                    "from": "Frank <frank@example.com>",
                    "subject": "Re: Need a quick answer",
                    "date": "Thu, 02 Apr 2026 10:00:00 +0000",
                    "snippet": "Previous reply.",
                },
            ],
        },
    )

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/actions/open_thread")

    assert response.status_code == 200
    updated = load_daily_brief(date.today())
    email_item = next(item for item in updated["items"] if item["id"] == "email_digest_1")
    assert len(email_item["content"]["thread_messages"]) == 2
    assert email_item["content"]["thread_messages"][1]["snippet"] == "Previous reply."


def test_your_day_item_action_snoozes_email_item(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=FakeAgent(), safety=None, your_day_service=None)

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/actions/snooze")

    assert response.status_code == 200
    updated = load_daily_brief(date.today())
    email_item = next(item for item in updated["items"] if item["id"] == "email_digest_1")
    assert email_item["state"] == "snoozed"
    assert updated["summary"]["counts"]["unread_emails"] == 0


def test_your_day_item_ask_endpoint_returns_item_focused_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    set_runtime(agent=FakeAgent(), safety=None, your_day_service=None)

    client = TestClient(app)
    response = client.post("/api/your-day/items/email_digest_1/ask", json={"question": "What matters here?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "quick decision" in payload["answer"]


def test_your_day_draft_update_and_send_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    save_daily_brief(_sample_brief())
    agent = FakeAgent()
    set_runtime(agent=agent, safety=None, your_day_service=None)

    client = TestClient(app)
    update_response = client.put(
        "/api/your-day/items/draft_1/draft",
        json={"to": "client@example.com", "subject": "Re: Need a quick answer", "body": "Updated draft body"},
    )
    assert update_response.status_code == 200
    updated = load_daily_brief(date.today())
    draft_item = next(item for item in updated["items"] if item["id"] == "draft_1")
    assert draft_item["content"]["to"] == "client@example.com"
    assert draft_item["content"]["body"] == "Updated draft body"
    assert "send" in draft_item["actions"]

    send_response = client.post("/api/your-day/items/draft_1/actions/send")
    assert send_response.status_code == 200
    sent_payload = send_response.json()
    assert sent_payload["ok"] is True
    assert "Reply sent" in sent_payload["result"]

    final = load_daily_brief(date.today())
    draft_item = next(item for item in final["items"] if item["id"] == "draft_1")
    assert draft_item["state"] == "sent"
    assert agent._tools["reply_email"].calls


def test_models_api_returns_router_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    set_runtime(router=FakeDiagnosticsRouter())

    client = TestClient(app)
    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["configured"]["ollama_model"] == "gemma4:e4b"
    assert payload["runs"][0]["provider"] == "ollama"


def test_models_page_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    set_runtime(router=FakeDiagnosticsRouter())

    client = TestClient(app)
    response = client.get("/models")

    assert response.status_code == 200
    assert "Model Diagnostics" in response.text
    assert "/api/models" in response.text


def test_models_api_falls_back_to_agent_llm_router_when_runtime_router_is_mesh_only(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")

    class AgentWithRouter:
        def __init__(self) -> None:
            self._llm = type("FakeLLM", (), {"_router": FakeDiagnosticsRouter()})()

    set_runtime(agent=AgentWithRouter(), router=FakeMeshOnlyRouter())

    client = TestClient(app)
    response = client.get("/api/models")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["configured"]["ollama_model"] == "gemma4:e4b"
