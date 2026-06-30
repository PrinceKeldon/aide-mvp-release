from types import SimpleNamespace

from fastapi.testclient import TestClient

from core.settings import settings
from interface.web import app, set_runtime
from mesh.device_registry import DeviceRegistry
from mesh.identity import DeviceIdentity


class FakeRouter:
    def __init__(self, response=None):
        self.response = response or {"status": "completed", "result_summary": "Peer finished the task.", "result_data": {"response": "ok"}}
        self.sent = []

    async def send_with_response(self, to_device_id, message_type, payload, from_device_id=""):
        self.sent.append((to_device_id, message_type, payload, from_device_id))
        return self.response

    async def send(self, to_device_id, message_type, payload, from_device_id=""):
        self.sent.append((to_device_id, message_type, payload, from_device_id))
        return True


class FakeSafety:
    async def process(self, action, executor):
        return await executor()


def test_m_peer_page_renders_plain_language_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    set_runtime(registry=registry, mesh_identity=DeviceIdentity.generate("Local Node"))

    client = TestClient(app)
    response = client.get("/m-peer")

    assert response.status_code == 200
    assert "Sovereign Peer Agents" in response.text
    assert "These peers are not your devices" in response.text
    assert "Choose How Another AIDE Can Work With You" in response.text
    assert "Invite Your AIDE" in response.text
    assert "Connect Another AIDE" in response.text
    assert "How This Works In 3 Steps" in response.text
    assert "Ask Another AIDE For Help" in response.text
    assert "Relationship notes" in response.text
    assert "People You've Connected" in response.text
    assert "Recent Peer Tasks" in response.text


def test_m_peer_api_lists_scope_templates_and_peer_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
        capabilities=["research", "summarization"],
    )
    set_runtime(registry=registry, mesh_identity=DeviceIdentity.generate("Local Node"))

    client = TestClient(app)
    response = client.get("/api/m-peer")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert any(template["scope_id"] == "research_partner" for template in payload["scope_templates"])
    assert any(peer["peer_agent_id"] == "marco_agent_xyz" for peer in payload["peers"])
    assert payload["intro"]["headline"] == "Sovereign peer agents are not your devices."
    assert payload["invitation"]["available"] is True
    assert payload["onboarding_steps"][0] == "Share your invitation or paste theirs."
    template = next(template for template in payload["scope_templates"] if template["scope_id"] == "research_partner")
    assert "This peer can ask for:" in template["allows_summary"]
    assert "memory" in template["memory_summary"].lower()
    assert "recent_tasks" in payload


def test_m_peer_api_surfaces_revoked_peer_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
    )
    registry.revoke_peer_agent("marco_agent_xyz", reason="Owner paused collaboration")
    set_runtime(registry=registry, mesh_identity=DeviceIdentity.generate("Local Node"))

    client = TestClient(app)
    response = client.get("/api/m-peer")

    assert response.status_code == 200
    peer = next(peer for peer in response.json()["peers"] if peer["peer_agent_id"] == "marco_agent_xyz")
    assert peer["revoked_at"] is not None
    assert peer["revocation_reason"] == "Owner paused collaboration"


def test_m_peer_api_includes_recent_peer_task_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
    )
    registry.create_peer_task_log(
        request_id="req_1",
        requesting_peer_id="marco_agent_xyz",
        responding_peer_id="local_aide",
        task_type="research",
        current_state="completed",
        title="Research request",
        result_summary="Done",
    )
    set_runtime(registry=registry, mesh_identity=DeviceIdentity.generate("Local Node"))

    client = TestClient(app)
    response = client.get("/api/m-peer")

    assert response.status_code == 200
    task = next(task for task in response.json()["recent_tasks"] if task["request_id"] == "req_1")
    assert task["requesting_peer_name"] == "Marco's AIDE"
    assert task["current_state"] == "completed"


def test_m_peer_task_creation_routes_outbound_request(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_device(
        device_id="peer_device_1",
        device_name="Marco Phone",
        device_type="full_node",
        public_key="abcd",
    )
    registry.mark_paired("peer_device_1")
    registry.bind_mesh("peer_device_1", "127.0.0.1", 7444)
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
        primary_device_id="peer_device_1",
    )
    router = FakeRouter()
    set_runtime(
        registry=registry,
        router=router,
        mesh_identity=SimpleNamespace(device_id="local_mesh"),
    )

    client = TestClient(app)
    response = client.post(
        "/api/m-peer/peers/marco_agent_xyz/tasks",
        json={
            "title": "Research overlap",
            "instruction": "Summarize the explicit facts.",
            "task_type": "research",
            "context_type": "explicit_facts",
            "context_text": "Budget memo and availability notes.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert router.sent[0][1] == "PEER_TASK_REQUEST"
    tasks = payload["tasks"]
    created = next(task for task in tasks if task["title"] == "Research overlap")
    assert created["requesting_peer_name"] == "Local AIDE"
    assert created["responding_peer_name"] == "Marco's AIDE"
    assert created["current_state"] == "completed"


def test_m_peer_task_creation_defaults_to_no_requester_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_device(
        device_id="peer_device_1",
        device_name="Marco Phone",
        device_type="full_node",
        public_key="abcd",
    )
    registry.mark_paired("peer_device_1")
    registry.bind_mesh("peer_device_1", "127.0.0.1", 7444)
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="project_collaboration",
        primary_device_id="peer_device_1",
    )
    router = FakeRouter(response={"status": "accepted"})
    set_runtime(
        registry=registry,
        router=router,
        mesh_identity=SimpleNamespace(device_id="local_mesh"),
    )

    client = TestClient(app)
    response = client.post(
        "/api/m-peer/peers/marco_agent_xyz/tasks",
        json={
            "title": "Project check-in",
            "instruction": "Summarize the current project blockers.",
            "task_type": "project_coordination",
            "context_type": "project_context",
            "context_text": "Current sprint board and latest status notes.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    created = next(task for task in payload["tasks"] if task["title"] == "Project check-in")
    assert created["requesting_approval_state"] == "not_required"
    assert created["current_state"] == "accepted"


def test_m_peer_task_cancel_updates_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_device(
        device_id="peer_device_1",
        device_name="Marco Phone",
        device_type="full_node",
        public_key="abcd",
    )
    registry.mark_paired("peer_device_1")
    registry.bind_mesh("peer_device_1", "127.0.0.1", 7444)
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
        primary_device_id="peer_device_1",
    )
    registry.create_peer_task_log(
        request_id="req_cancel_web",
        requesting_peer_id="local_mesh",
        responding_peer_id="marco_agent_xyz",
        task_type="research",
        current_state="executing",
        title="Cancelable request",
    )
    router = FakeRouter()
    set_runtime(
        registry=registry,
        router=router,
        mesh_identity=SimpleNamespace(device_id="local_mesh"),
    )

    client = TestClient(app)
    response = client.post("/api/m-peer/tasks/req_cancel_web/cancel", json={"reason": "Stop this request."})

    assert response.status_code == 200
    payload = response.json()
    cancelled = next(task for task in payload["tasks"] if task["request_id"] == "req_cancel_web")
    assert cancelled["current_state"] == "cancelled"
    assert any(entry[1] == "PEER_TASK_CANCEL" for entry in router.sent)


def test_m_peer_invitation_accept_creates_peer_with_selected_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    local = DeviceIdentity.generate("Frank's Mac")
    remote = DeviceIdentity.generate("Marco's Mac")
    set_runtime(registry=registry, mesh_identity=local)

    client = TestClient(app)
    invitation = client.get("/api/m-peer/invitation")
    assert invitation.status_code == 200

    from mesh.peer_invitation import build_peer_invitation_payload

    payload = build_peer_invitation_payload(
        remote,
        display_name="Marco's AIDE",
        owner_name="Marco",
        primary_device_id="marco_device_1",
        capabilities=["research"],
    )
    response = client.post(
        "/api/m-peer/invitations/accept",
        json={"payload": payload, "trust_scope_id": "research_partner"},
    )

    assert response.status_code == 200
    peer = registry.get_peer_agent(remote.device_id)
    assert peer is not None
    assert peer["display_name"] == "Marco's AIDE"
    assert peer["trust_scope_id"] == "research_partner"


def test_m_peer_peer_controls_update_state_and_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    local = DeviceIdentity.generate("Frank's Mac")
    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="assistant_introduction",
    )
    set_runtime(registry=registry, mesh_identity=local)

    client = TestClient(app)

    rename = client.put("/api/m-peer/peers/marco_agent_xyz/name", json={"name": "Marco Collaboration"})
    assert rename.status_code == 200

    scope = client.put("/api/m-peer/peers/marco_agent_xyz/scope", json={"trust_scope_id": "research_partner"})
    assert scope.status_code == 200

    notes = client.put("/api/m-peer/peers/marco_agent_xyz/notes", json={"notes": "Use this relationship for launch research only."})
    assert notes.status_code == 200

    pause = client.post("/api/m-peer/peers/marco_agent_xyz/pause", json={"reason": "Waiting until next week."})
    assert pause.status_code == 200

    payload = client.get("/api/m-peer").json()
    peer = next(peer for peer in payload["peers"] if peer["peer_agent_id"] == "marco_agent_xyz")
    assert peer["display_name"] == "Marco Collaboration"
    assert peer["scope_id"] == "research_partner"
    assert peer["trust_notes"] == "Use this relationship for launch research only."
    assert peer["paused"] is True
    assert peer["pause_reason"] == "Waiting until next week."
    assert "This peer can ask for:" in peer["allows_summary"]

    resume = client.post("/api/m-peer/peers/marco_agent_xyz/resume")
    assert resume.status_code == 200
    restore = client.post("/api/m-peer/peers/marco_agent_xyz/restore")
    assert restore.status_code == 200
    revoke = client.post("/api/m-peer/peers/marco_agent_xyz/revoke", json={"reason": "Trust ended."})
    assert revoke.status_code == 200

    payload = client.get("/api/m-peer").json()
    peer = next(peer for peer in payload["peers"] if peer["peer_agent_id"] == "marco_agent_xyz")
    assert peer["paused"] is False
    assert peer["revoked_at"] is not None
