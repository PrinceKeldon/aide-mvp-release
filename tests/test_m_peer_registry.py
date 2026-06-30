from mesh.device_registry import DeviceRegistry
from mesh.peer_templates import default_scope_templates


def test_peer_scope_templates_can_be_seeded_and_listed(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")

    count = registry.seed_default_peer_scope_templates()
    scopes = registry.list_scope_templates()

    assert count == len(default_scope_templates())
    assert {scope["scope_id"] for scope in scopes} >= {
        "research_partner",
        "calendar_coordination",
        "project_collaboration",
        "assistant_introduction",
    }


def test_can_create_and_revoke_sovereign_peer_without_creating_owner_device(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()

    registry.create_peer_agent(
        peer_agent_id="marco_agent_xyz",
        public_key="abcd1234",
        display_name="Marco's AIDE",
        owner_name="Marco",
        trust_scope_id="research_partner",
        capabilities=["research", "summarization"],
        metadata={"region": "EU"},
    )

    peer = registry.get_peer_agent("marco_agent_xyz")
    assert peer is not None
    assert peer["display_name"] == "Marco's AIDE"
    assert peer["trust_scope_id"] == "research_partner"
    assert registry.peer_agent_exists("marco_agent_xyz") is True
    assert registry.device_exists("marco_agent_xyz") is False
    assert all(device["device_id"] != "marco_agent_xyz" for device in registry.list_all())

    registry.revoke_peer_agent("marco_agent_xyz", reason="Owner revoked peer access")
    revoked = registry.get_peer_agent("marco_agent_xyz")
    assert revoked["revoked_at"] is not None
    assert revoked["revocation_reason"] == "Owner revoked peer access"

    registry.restore_peer_agent("marco_agent_xyz")
    restored = registry.get_peer_agent("marco_agent_xyz")
    assert restored["revoked_at"] is None
    assert restored["revocation_reason"] is None


def test_peer_task_log_persists_lifecycle(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    registry.create_peer_agent(
        peer_agent_id="peer_a",
        public_key="key-a",
        display_name="Peer A",
        trust_scope_id="assistant_introduction",
    )
    registry.create_peer_agent(
        peer_agent_id="peer_b",
        public_key="key-b",
        display_name="Peer B",
        trust_scope_id="research_partner",
    )

    request_id = registry.create_peer_task_log(
        request_id="req_123",
        requesting_peer_id="peer_a",
        responding_peer_id="peer_b",
        task_type="research",
        current_state="pending",
        title="Research request",
        instruction="Summarize the latest public material.",
        context_provided={"facts": ["public only"]},
    )

    task = registry.get_peer_task_log(request_id)
    assert task is not None
    assert task["current_state"] == "pending"

    registry.update_peer_task_log_state(
        request_id,
        current_state="completed",
        result_summary="Research completed",
        result_data={"summary": "Done"},
        execution_time_ms=850,
    )

    updated = registry.get_peer_task_log(request_id)
    assert updated["current_state"] == "completed"
    assert updated["result_summary"] == "Research completed"
    assert updated["execution_time_ms"] == 850
    assert updated["completed_at"] is not None
