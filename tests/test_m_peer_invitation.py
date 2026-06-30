from mesh.device_registry import DeviceRegistry
from mesh.identity import DeviceIdentity
from mesh.pairing import PairingManager, PeerInvitationManager
from mesh.peer_invitation import build_peer_invitation_payload, parse_peer_invitation_payload
from mesh.ui.pairing_ui import build_pairing_payload


def test_peer_invitation_round_trip():
    identity = DeviceIdentity.generate("Marco's Mac")

    payload = build_peer_invitation_payload(
        identity,
        display_name="Marco's AIDE",
        owner_name="Marco",
        capabilities=["research", "analysis"],
    )
    parsed = parse_peer_invitation_payload(payload)

    assert parsed["peer_agent_id"] == identity.device_id
    assert parsed["display_name"] == "Marco's AIDE"
    assert parsed["owner_name"] == "Marco"
    assert parsed["capabilities"] == ["research", "analysis"]


def test_owner_device_pairing_rejects_peer_invitation_payload(tmp_path):
    local_identity = DeviceIdentity.generate("Frank's Mac")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    manager = PairingManager(local_identity, device_registry=registry)

    peer_identity = DeviceIdentity.generate("Marco's Mac")
    invitation = build_peer_invitation_payload(peer_identity, display_name="Marco's AIDE")

    ok = manager.complete_pairing(invitation)

    assert ok is False
    assert registry.peer_agent_exists(peer_identity.device_id) is False
    assert registry.device_exists(peer_identity.device_id) is False


def test_peer_invitation_creates_peer_agent_not_owner_device(tmp_path):
    local_identity = DeviceIdentity.generate("Frank's Mac")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    manager = PeerInvitationManager(local_identity, device_registry=registry)

    peer_identity = DeviceIdentity.generate("Marco's Mac")
    invitation = build_peer_invitation_payload(
        peer_identity,
        display_name="Marco's AIDE",
        owner_name="Marco",
        capabilities=["research"],
    )

    ok = manager.complete_invitation(
        invitation,
        trust_scope_id="research_partner",
        paired_by="Frank",
    )

    assert ok is True
    peer = registry.get_peer_agent(peer_identity.device_id)
    assert peer is not None
    assert peer["display_name"] == "Marco's AIDE"
    assert peer["trust_scope_id"] == "research_partner"
    assert registry.device_exists(peer_identity.device_id) is False


def test_peer_invitation_manager_rejects_owner_device_pairing_payload(tmp_path):
    local_identity = DeviceIdentity.generate("Frank's Mac")
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    registry.seed_default_peer_scope_templates()
    manager = PeerInvitationManager(local_identity, device_registry=registry)

    device_identity = DeviceIdentity.generate("Frank's Android")
    payload = build_pairing_payload(device_identity)

    ok = manager.complete_invitation(payload)

    assert ok is False
    assert registry.peer_agent_exists(device_identity.device_id) is False
