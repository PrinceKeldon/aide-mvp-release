import asyncio

import pytest

from core.llm import LLMClient
from core.settings import settings
from mesh.identity import DeviceIdentity
from mesh.node import MeshDiscovery, MeshNode, resolve_mesh_ipv4_addresses
from main import bootstrap_discovered_peer


class _FakeMemory:
    def store_fact(self, key, value):
        return None

    def log_action(self, action, tier, outcome, reversible=True):
        return None


class _FakeInfo:
    def __init__(self, *, peer_id: str, addresses: list[str], port: int) -> None:
        self.properties = {b"agent_id": peer_id.encode()}
        self._addresses = list(addresses)
        self.port = port

    def parsed_addresses(self) -> list[str]:
        return list(self._addresses)


class _FakeZeroconf:
    def __init__(self, info) -> None:
        self._info = info

    def get_service_info(self, type_, name):
        return self._info


class _FakeLoop:
    def __init__(self) -> None:
        self.calls = []

    def call_soon_threadsafe(self, func, *args) -> None:
        self.calls.append((func, args))


class _BootstrapRegistry:
    def __init__(self, trusted_peer_id: str, public_key_hex: str) -> None:
        self._trusted_peer_id = trusted_peer_id
        self._public_key_hex = public_key_hex
        self.bound = []

    def device_exists(self, peer_id: str) -> bool:
        return peer_id == self._trusted_peer_id

    def is_trusted(self, peer_id: str) -> bool:
        return peer_id == self._trusted_peer_id

    def get_mesh_endpoint(self, peer_id: str):
        return None

    def bind_mesh(self, peer_id: str, host: str, port: int) -> None:
        self.bound.append((peer_id, host, port))

    def get_by_device_id(self, peer_id: str):
        return {"public_key": self._public_key_hex}


class _BootstrapNode:
    def __init__(self) -> None:
        self.trusted = []
        self.outbound = []
        self.revoked = []

    def trust_peer(self, agent_id: str, public_key: bytes) -> None:
        self.trusted.append((agent_id, public_key))

    def revoke_peer(self, agent_id: str) -> None:
        self.revoked.append(agent_id)

    async def ensure_outbound_session(self, peer: dict) -> None:
        self.outbound.append(peer)


async def _false_ollama() -> bool:
    return False


def test_available_providers_accepts_cloud_only_config(monkeypatch):
    llm = LLMClient()
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(llm, "is_ollama_running", _false_ollama)

    providers = __import__("asyncio").run(llm.available_providers())

    assert providers == ["gemini"]


def test_mesh_discovery_ignores_peers_without_routable_addresses():
    identity = DeviceIdentity.generate("Local Discovery Node")
    discovery = MeshDiscovery(identity)
    peer = DeviceIdentity.generate("Peer Discovery Node")
    info = _FakeInfo(peer_id=peer.device_id, addresses=[], port=7432)

    discovery.add_service(_FakeZeroconf(info), "_aide._tcp.local.", "aide-peer._aide._tcp.local.")

    assert discovery.get_peers() == []


def test_mesh_discovery_remove_service_uses_service_name_mapping():
    identity = DeviceIdentity.generate("Local Discovery Node")
    discovery = MeshDiscovery(identity)
    peer = DeviceIdentity.generate("Peer Discovery Node")
    service_name = f"aide-{peer.device_id[:12]}._aide._tcp.local."
    info = _FakeInfo(peer_id=peer.device_id, addresses=["10.0.0.8"], port=7432)

    discovery.add_service(_FakeZeroconf(info), "_aide._tcp.local.", service_name)
    assert discovery.get_peers()[0]["agent_id"] == peer.device_id

    discovery.remove_service(_FakeZeroconf(info), "_aide._tcp.local.", service_name)

    assert discovery.get_peers() == []


def test_mesh_discovery_schedules_peer_callback_on_captured_loop():
    identity = DeviceIdentity.generate("Local Discovery Node")
    discovery = MeshDiscovery(identity)
    peer = DeviceIdentity.generate("Peer Discovery Node")
    service_name = f"aide-{peer.device_id[:12]}._aide._tcp.local."
    info = _FakeInfo(peer_id=peer.device_id, addresses=["10.0.0.8"], port=7432)
    loop = _FakeLoop()
    seen = []

    async def _peer_callback(peer_record):
        seen.append(peer_record)

    discovery._loop = loop
    discovery.set_peer_callback(_peer_callback)
    discovery.add_service(_FakeZeroconf(info), "_aide._tcp.local.", service_name)

    assert len(loop.calls) == 1
    func, args = loop.calls[0]
    assert func is asyncio.create_task
    assert args[0].cr_code.co_name == "_peer_callback"
    args[0].close()


@pytest.mark.asyncio
async def test_bootstrap_discovered_peer_skips_loopback_bind():
    peer = DeviceIdentity.generate("Peer Discovery Node")
    registry = _BootstrapRegistry(peer.device_id, peer.public_key.hex())
    mesh_node = _BootstrapNode()

    await bootstrap_discovered_peer(
        {"agent_id": peer.device_id, "address": "127.0.0.1", "port": 7432},
        device_registry=registry,
        mesh_node=mesh_node,
    )

    assert registry.bound == []
    assert mesh_node.outbound == [{"agent_id": peer.device_id, "address": "127.0.0.1", "port": 7432}]


def test_resolve_mesh_ipv4_addresses_filters_loopback(monkeypatch):
    class _FakeSocket:
        def __init__(self, *args, **kwargs):
            self.closed = False

        def connect(self, target):
            return None

        def getsockname(self):
            return ("192.168.1.24", 55555)

        def close(self):
            self.closed = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

    monkeypatch.setattr("mesh.node.socket.socket", lambda *args, **kwargs: _FakeSocket())
    monkeypatch.setattr(
        "mesh.node.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, None, None, None, ("127.0.0.1", 0)),
            (2, None, None, None, ("192.168.1.24", 0)),
            (2, None, None, None, ("10.0.0.4", 0)),
        ],
    )

    assert resolve_mesh_ipv4_addresses() == ["192.168.1.24", "10.0.0.4"]


@pytest.mark.asyncio
async def test_session_hello_registers_reverse_route(monkeypatch):
    identities = {
        "Mac Node": DeviceIdentity.generate("Mac Node"),
        "Android Node": DeviceIdentity.generate("Android Node"),
    }

    monkeypatch.setattr("mesh.node.get_or_create_identity", lambda name, device_type: identities[name])
    monkeypatch.setattr("mesh.node.MemoryManager", lambda: _FakeMemory())

    mac = MeshNode("Mac Node")
    android = MeshNode("Android Node")

    mac.trust_peer(android.device_id, android.public_key)

    fake_websocket = object()
    hello = {
        "kind": "session_hello",
        "sender_id": android.device_id,
        "timestamp": "2026-04-08T09:00:00",
    }
    hello["signature"] = android.identity.sign(mac._signature_payload(hello)).hex()

    response, sender_id = await mac._handle_session_envelope(fake_websocket, hello)

    assert sender_id == android.device_id
    assert mac._active_sessions[android.device_id] is fake_websocket
    assert response["kind"] == "session_ack"


@pytest.mark.asyncio
async def test_send_intent_prefers_active_session(monkeypatch):
    identities = {
        "Mac Node": DeviceIdentity.generate("Mac Node"),
        "Android Node": DeviceIdentity.generate("Android Node"),
    }

    monkeypatch.setattr("mesh.node.get_or_create_identity", lambda name, device_type: identities[name])
    monkeypatch.setattr("mesh.node.MemoryManager", lambda: _FakeMemory())

    mac = MeshNode("Mac Node")
    android = MeshNode("Android Node")
    mac.trust_peer(android.device_id, android.public_key)

    class _FakeSession:
        async def send(self, raw):
            payload = __import__("json").loads(raw)
            future = mac._pending_requests[payload["request_id"]]
            future.set_result({"status": "pong", "target": payload["intent"]["parameters"]["to_device_id"]})

    mac._active_sessions[android.device_id] = _FakeSession()

    async def _unexpected_connect(*args, **kwargs):  # pragma: no cover - diagnostic guard
        raise AssertionError("direct connect should not be used when reverse session is active")

    monkeypatch.setattr("mesh.node.websockets.connect", _unexpected_connect)

    result = await mac.send_intent(
        {
            "agent_id": android.device_id,
            "address": "127.0.0.1",
            "port": 8742,
        },
        "PING",
        {"to_device_id": android.device_id},
    )

    assert result == {"status": "pong", "target": android.device_id}


@pytest.mark.asyncio
async def test_ensure_outbound_session_restarts_worker_when_target_changes(monkeypatch):
    identities = {
        "Mac Node": DeviceIdentity.generate("Mac Node"),
    }

    monkeypatch.setattr("mesh.node.get_or_create_identity", lambda name, device_type: identities[name])
    monkeypatch.setattr("mesh.node.MemoryManager", lambda: _FakeMemory())

    mac = MeshNode("Mac Node")
    peer = DeviceIdentity.generate("Peer Node")
    starts = []
    cancellations = []

    async def _fake_maintain(agent_id: str) -> None:
        starts.append((agent_id, mac._session_targets[agent_id]))
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancellations.append(agent_id)
            raise

    monkeypatch.setattr(mac, "_maintain_outbound_session", _fake_maintain)

    await mac.ensure_outbound_session(
        {"agent_id": peer.device_id, "address": "192.168.6.154", "port": 7432}
    )
    await asyncio.sleep(0)
    first_worker = mac._session_workers[peer.device_id]

    await mac.ensure_outbound_session(
        {"agent_id": peer.device_id, "address": "192.168.6.35", "port": 7432}
    )
    await asyncio.sleep(0)
    second_worker = mac._session_workers[peer.device_id]

    assert first_worker is not second_worker
    assert starts == [
        (peer.device_id, ("192.168.6.154", 7432)),
        (peer.device_id, ("192.168.6.35", 7432)),
    ]
    assert cancellations == [peer.device_id]
    assert mac._session_targets[peer.device_id] == ("192.168.6.35", 7432)

    await mac.clear_outbound_session(peer.device_id)
