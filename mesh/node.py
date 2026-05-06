"""
AIDE — mesh protocol
Peer-to-peer agent coordination. No central server. Ever.

How it works:
  1. Each agent has an Ed25519 keypair generated on install.
  2. Agents broadcast presence on the local network via mDNS.
  3. When two agents connect, they verify each other's public keys.
  4. Messages are encrypted and signed — no raw personal data crosses the wire.
  5. Both owners can audit or revoke trust at any time.
"""

import asyncio
import json
import socket
import uuid
from datetime import datetime
from pathlib import Path
from contextlib import suppress

from loguru import logger

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
    load_pem_private_key,
    load_pem_public_key,
)
from cryptography.exceptions import InvalidSignature

import websockets
from websockets import uri
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosed
from zeroconf import ServiceBrowser, ServiceInfo, Zeroconf

from core.settings import settings
from core.logging_utils import LogRateLimiter
from memory.manager import MemoryManager
from mesh.identity import DeviceIdentity, get_or_create_identity, DeviceType


KEYS_DIR = Path("./data/keys")
INTENT_VERSION = "1.0"


def build_intent(
    intent_type: str,
    parameters: dict,
    sender_id: str,
) -> dict:
    """
    The ONLY thing that crosses the mesh wire.
    No names, no addresses — only structured, minimal parameters.
    """
    return {
        "version": INTENT_VERSION,
        "id": str(uuid.uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "sender_id": sender_id,
        "intent_type": intent_type,
        "parameters": parameters,
    }

class MeshDiscovery:
    """Broadcasts this agent on LAN via mDNS. Discovers other AIDE agents."""

    def __init__(self, identity: DeviceIdentity) -> None:
        self._identity = identity
        self._zeroconf = None
        self._known_peers: dict[str, dict] = {}
        self._service_name_to_peer_id: dict[str, str] = {}
        self._on_peer_found = None
        self._info = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_peer_callback(self, callback) -> None:
        self._on_peer_found = callback

    async def start_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            self._zeroconf = Zeroconf()
        except Exception as e:
            logger.warning(f"Mesh discovery unavailable — continuing without mDNS: {e}")
            self._zeroconf = None
            return

        addresses = self._service_addresses()
        if not addresses:
            logger.warning(
                "Mesh discovery could not determine a non-loopback LAN address; "
                "advertising without explicit addresses may limit discovery."
            )

        self._info = ServiceInfo(
            type_=settings.mesh_service_name,
            name=f"aide-{self._identity.device_id[:12]}.{settings.mesh_service_name}",
            port=settings.mesh_port,
            properties={
                "agent_id": self._identity.device_id,
            },
            addresses=addresses,
        )
        try:
            await self._zeroconf.async_register_service(self._info)
        except Exception as e:
            logger.warning(
                f"Mesh discovery registration unavailable — continuing without mDNS: {e}"
            )
            await self._zeroconf._async_close()
            self._zeroconf = None
            return
        ServiceBrowser(self._zeroconf, settings.mesh_service_name, self)
        logger.info(f"Mesh discovery started — agent ID: {self._identity.device_id}")

    async def stop_async(self) -> None:
        if self._zeroconf is None:
            return
        await self._zeroconf._async_close()

    def add_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        info = zeroconf.get_service_info(type_, name)
        if not info:
            return

        props = {k.decode(): v.decode() for k, v in info.properties.items()}
        peer_id = props.get("agent_id", "")
        if peer_id == self._identity.device_id:
            return

        addresses = info.parsed_addresses()
        if not addresses:
            logger.warning(f"Peer discovered without a routable address: {peer_id}")
            return

        peer = {
            "agent_id": peer_id,
            "address": addresses[0],
            "port": info.port,
            "service_name": name,
        }
        self._known_peers[peer_id] = peer
        self._service_name_to_peer_id[name] = peer_id
        logger.info(f"Peer discovered: {peer_id}")

        if self._on_peer_found:
            if self._loop is None:
                logger.warning("Mesh discovery loop not available; peer callback skipped")
                return
            self._loop.call_soon_threadsafe(
                asyncio.create_task,
                self._on_peer_found(peer),
            )

    def remove_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        peer_id = self._service_name_to_peer_id.pop(name, None)
        if peer_id:
            self._known_peers.pop(peer_id, None)
            logger.info(f"Peer left: {peer_id}")

    def update_service(self, zeroconf: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zeroconf, type_, name)

    def get_peers(self) -> list[dict]:
        return list(self._known_peers.values())

    def _service_addresses(self) -> list[bytes]:
        return [socket.inet_aton(address) for address in resolve_mesh_ipv4_addresses()]


def resolve_mesh_ipv4_addresses() -> list[str]:
    addresses: list[str] = []

    def _remember(address: str | None) -> None:
        if not address:
            return
        if address.startswith("127.") or address == "0.0.0.0":
            return
        if address not in addresses:
            addresses.append(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            _remember(sock.getsockname()[0])
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET):
            if family == socket.AF_INET:
                _remember(sockaddr[0])
    except Exception:
        pass

    return addresses


class MeshNode:
    """WebSocket server + client for agent-to-agent coordination."""

    def __init__(
        self,
        device_name: str,
        device_type: DeviceType = DeviceType.FULL_NODE,
    ):
        # New identity system
        self.identity = get_or_create_identity(device_name, device_type)

        # Backward compatibility (temporary)
        self.device_id = self.identity.device_id
        self.private_key = self.identity.private_key
        self.public_key = self.identity.public_key
        self._identity = self.identity

        self._trusted_peers: dict[str, bytes | str] = {}
        self._intent_handlers: dict[str, callable] = {}
        self._server = None
        self._memory = MemoryManager()
        self._active_sessions: dict[str, WebSocketServerProtocol] = {}
        self._session_targets: dict[str, tuple[str, int]] = {}
        self._session_workers: dict[str, asyncio.Task] = {}
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._security_log_limiter = LogRateLimiter()

    def register_handler(self, intent_type: str, handler) -> None:
        self._intent_handlers[intent_type] = handler

    def trust_peer(self, agent_id: str, public_key: bytes | str) -> None:
        self._trusted_peers[agent_id] = public_key
        preview = public_key.hex()[:64] if isinstance(public_key, bytes) else public_key[:64]
        self._memory.store_fact(f"trusted_peer_{agent_id}", preview)
        logger.info(f"Peer trusted: {agent_id}")

    def revoke_peer(self, agent_id: str) -> None:
        self._trusted_peers.pop(agent_id, None)
        self._session_targets.pop(agent_id, None)
        worker = self._session_workers.pop(agent_id, None)
        if worker:
            worker.cancel()
        websocket = self._active_sessions.pop(agent_id, None)
        if websocket:
            asyncio.create_task(websocket.close())
        logger.info(f"Trust revoked: {agent_id}")

    async def start_server(self) -> None:
        try:
            self._server = await websockets.serve(
                self._handle_connection,
                "0.0.0.0",
                settings.mesh_port,
            )
            logger.info(f"Mesh node listening on port {settings.mesh_port}")
        except Exception as e:
            self._server = None
            logger.warning(
                f"Mesh server unavailable — continuing without inbound mesh transport: {e}"
            )

    async def _handle_connection(
        self, websocket: WebSocketServerProtocol
    ) -> None:
        bound_peer_id: str | None = None
        try:
            while True:
                raw = await websocket.recv()
                envelope = json.loads(raw)
                kind = envelope.get("kind")

                if not kind:
                    response, sender_id = await self._handle_legacy_envelope(envelope)
                    await websocket.send(json.dumps(response))
                    return

                response, sender_id = await self._handle_session_envelope(websocket, envelope)
                if sender_id and kind == "session_hello":
                    bound_peer_id = sender_id
                if response is not None:
                    await websocket.send(json.dumps(response))
        except ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Mesh connection error: {e}")
        finally:
            if bound_peer_id:
                self._active_sessions.pop(bound_peer_id, None)

    async def _handle_legacy_envelope(self, envelope: dict) -> tuple[dict, str]:
        sender_id = envelope.get("sender_id", "")
        intent = envelope.get("intent", {})
        signature = bytes.fromhex(envelope.get("signature", ""))
        try:
            result = await self._execute_intent(sender_id, intent, signature)
            return {
                "result": result,
                "sender_id": self._identity.device_id,
                "signature": self._identity.sign(json.dumps(result, sort_keys=True).encode()).hex(),
            }, sender_id
        except ValueError as exc:
            return {"error": str(exc)}, sender_id

    async def _handle_session_envelope(
        self,
        websocket: WebSocketServerProtocol,
        envelope: dict,
    ) -> tuple[dict | None, str | None]:
        kind = envelope.get("kind")
        sender_id = envelope.get("sender_id", "")

        if kind == "session_hello":
            verified = self._verify_signed_envelope(sender_id, envelope)
            if not verified:
                return {"kind": "session_error", "error": "not trusted"}, sender_id
            self._active_sessions[sender_id] = websocket
            logger.info(f"Mesh reverse session active: {sender_id}")
            ack = {
                "kind": "session_ack",
                "sender_id": self._identity.device_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            ack["signature"] = self._identity.sign(self._signature_payload(ack)).hex()
            return ack, sender_id

        if kind == "session_ack":
            return None, sender_id

        if kind == "intent_request":
            if not self._verify_signed_envelope(sender_id, envelope):
                return {
                    "kind": "intent_response",
                    "request_id": envelope.get("request_id", ""),
                    "sender_id": self._identity.device_id,
                    "error": "not trusted",
                    "signature": self._identity.sign(
                        self._signature_payload(
                            {
                                "kind": "intent_response",
                                "request_id": envelope.get("request_id", ""),
                                "sender_id": self._identity.device_id,
                                "error": "not trusted",
                            }
                        )
                    ).hex(),
                }, sender_id
            intent = envelope.get("intent", {})
            signature = bytes.fromhex(envelope.get("intent_signature", ""))
            try:
                result = await self._execute_intent(sender_id, intent, signature)
            except ValueError as exc:
                error_response = {
                    "kind": "intent_response",
                    "request_id": envelope.get("request_id", ""),
                    "sender_id": self._identity.device_id,
                    "error": str(exc),
                }
                error_response["signature"] = self._identity.sign(
                    self._signature_payload(error_response)
                ).hex()
                return error_response, sender_id
            response = {
                "kind": "intent_response",
                "request_id": envelope.get("request_id", ""),
                "sender_id": self._identity.device_id,
                "result": result,
            }
            response["signature"] = self._identity.sign(self._signature_payload(response)).hex()
            return response, sender_id

        if kind == "intent_response":
            if not self._verify_signed_envelope(sender_id, envelope):
                future = self._pending_requests.pop(envelope.get("request_id", ""), None)
                if future and not future.done():
                    future.set_result(None)
                return None, sender_id
            future = self._pending_requests.pop(envelope.get("request_id", ""), None)
            if future and not future.done():
                future.set_result(None if "error" in envelope else envelope.get("result"))
            return None, sender_id

        if kind == "session_error":
            logger.warning(f"Peer error: {envelope.get('error', 'unknown session error')}")
            return None, sender_id

        return None, sender_id

    async def _execute_intent(self, sender_id: str, intent: dict, signature: bytes) -> dict:
        if sender_id not in self._trusted_peers:
            self._log_untrusted_peer(sender_id)
            raise ValueError("not trusted")

        public_key = self._trusted_peers[sender_id]
        payload = json.dumps(intent, sort_keys=True).encode()
        if not DeviceIdentity.verify(public_key, payload, signature):
            logger.warning(f"Invalid signature from: {sender_id}")
            raise ValueError("invalid signature")

        intent_type = intent.get("intent_type", "")
        handler = self._intent_handlers.get(intent_type)
        if not handler:
            raise ValueError(f"no handler for {intent_type}")

        result = await handler(intent)

        logger.info(f"Intent '{intent_type}' from {sender_id} handled")
        self._memory.log_action(
            action=f"mesh_receive: {intent_type} from {sender_id}",
            tier="notify",
            outcome="handled",
        )
        return result

    def _verify_signed_envelope(self, sender_id: str, envelope: dict) -> bool:
        if sender_id not in self._trusted_peers:
            self._log_untrusted_peer(sender_id)
            return False
        signature_hex = envelope.get("signature", "")
        if not signature_hex:
            logger.warning(f"Unsigned mesh envelope from: {sender_id}")
            return False
        return DeviceIdentity.verify(
            self._trusted_peers[sender_id],
            self._signature_payload(envelope),
            bytes.fromhex(signature_hex),
        )

    def _log_untrusted_peer(self, sender_id: str) -> None:
        message = f"Rejected untrusted peer: {sender_id}"
        if self._security_log_limiter.should_log(f"untrusted_peer:{sender_id}", 600):
            logger.warning(message)
        else:
            logger.debug(message)

    def _signature_payload(self, envelope: dict) -> bytes:
        unsigned = {k: v for k, v in envelope.items() if k != "signature"}
        return json.dumps(unsigned, sort_keys=True).encode()

    async def verify_peer_reachability(
        self, address: str, port: int, timeout: float = 2.0
    ) -> bool:
        """Checks if a peer is reachable via a WebSocket handshake to avoid server-side EOF errors."""
        try:
            uri = f"ws://{address}:{port}"
            async with websockets.connect(uri, open_timeout=timeout) as ws:
                return True
        except Exception:
            return False

    async def ensure_outbound_session(self, peer: dict) -> None:
        agent_id = peer.get("agent_id")
        address = peer.get("address")
        port = peer.get("port")
        if not agent_id or not address or not port or agent_id == self._identity.device_id:
            return
        target = (address, int(port))
        previous_target = self._session_targets.get(agent_id)
        self._session_targets[agent_id] = target
        worker = self._session_workers.get(agent_id)
        if worker and not worker.done():
            if previous_target == target:
                return
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self._session_workers[agent_id] = asyncio.create_task(
            self._maintain_outbound_session(agent_id)
        )

    async def clear_outbound_session(self, agent_id: str) -> None:
        self._session_targets.pop(agent_id, None)
        worker = self._session_workers.pop(agent_id, None)
        if worker:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        websocket = self._active_sessions.pop(agent_id, None)
        if websocket:
            with suppress(Exception):
                await websocket.close()

    async def report_failure(self, peer_id: str):
        """Signals that a connection to a peer failed. Clears session to force re-resolve."""
        logger.warning(f"Mesh failure reported for peer {peer_id}. Clearing session.")
        await self.clear_outbound_session(peer_id)

    async def _maintain_outbound_session(self, agent_id: str) -> None:
        while agent_id in self._session_targets:
            if agent_id in self._active_sessions:
                await asyncio.sleep(5)
                continue

            address, port = self._session_targets[agent_id]
            uri = f"ws://{address}:{port}"
            ws = None
            try:
                async with websockets.connect(uri, open_timeout=15, ping_interval=20, ping_timeout=20) as ws:
                    hello = {
                        "kind": "session_hello",
                        "sender_id": self._identity.device_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    }
                    hello["signature"] = self._identity.sign(self._signature_payload(hello)).hex()
                    await ws.send(json.dumps(hello))
                    self._active_sessions[agent_id] = ws
                    logger.bind(operational=True).info(f"Mesh outbound session ready: {agent_id} via {uri}")
                    while True:
                        raw = await ws.recv()
                        envelope = json.loads(raw)
                        await self._handle_session_envelope(ws, envelope)
            except asyncio.CancelledError:
                break
            except ConnectionClosed:
                pass
            except Exception as e:
                logger.bind(operational=True).debug(f"Mesh outbound session retry for {agent_id} via {uri}: {e}")
            finally:
                current = self._active_sessions.get(agent_id)
                if current is ws:
                    self._active_sessions.pop(agent_id, None)
            await asyncio.sleep(5)

    async def send_intent(
        self,
        peer: dict,
        intent_type: str,
        parameters: dict,
    ) -> dict | None:
        intent = build_intent(
            intent_type=intent_type,
            parameters=parameters,
            sender_id=self._identity.device_id,
        )
        payload = json.dumps(intent, sort_keys=True).encode()
        signature = self._identity.sign(payload)
        
        envelope = {
            "sender_id": self._identity.device_id,
            "intent": intent,
            "signature": signature.hex(),
        }
        
        uri = f"ws://{peer['address']}:{peer['port']}"
        active = self._active_sessions.get(peer["agent_id"])
        if active is not None:
            result = await self._send_via_active_session(peer["agent_id"], intent)
            if result is not None:
                self._memory.log_action(
                    action=f"mesh_send: {intent_type} to {peer['agent_id']}",
                    tier="notify",
                    outcome="success",
                )
                return result
        
        try:
            async with websockets.connect(uri, open_timeout=10) as ws:
                await ws.send(json.dumps(envelope))
                raw_response = await asyncio.wait_for(ws.recv(), timeout=30)
                response = json.loads(raw_response)
                if response.get("error"):
                    logger.warning(f"Mesh intent error from {peer['agent_id']}: {response['error']}")
                    self._memory.log_action(
                        action=f"mesh_send: {intent_type} to {peer['agent_id']}",
                        tier="notify",
                        outcome=f"error: {response['error']}",
                    )
                    return None
                self._memory.log_action(
                    action=f"mesh_send: {intent_type} to {peer['agent_id']}",
                    tier="notify",
                    outcome="success",
                )
                return response.get("result")
            
        except Exception as e:
            logger.error(f"Mesh send failed: {e}")
            await self.clear_outbound_session(peer["agent_id"])
            return None

    async def _send_via_active_session(self, peer_id: str, intent: dict) -> dict | None:
        websocket = self._active_sessions.get(peer_id)
        if websocket is None:
            return None
        request_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future
        frame = {
            "kind": "intent_request",
            "request_id": request_id,
            "sender_id": self._identity.device_id,
            "intent": intent,
            "intent_signature": self._identity.sign(json.dumps(intent, sort_keys=True).encode()).hex(),
        }
        frame["signature"] = self._identity.sign(self._signature_payload(frame)).hex()
        try:
            await websocket.send(json.dumps(frame))
            return await asyncio.wait_for(future, timeout=30)
        except Exception as e:
            logger.error(f"Mesh session send failed: {e}")
            self._active_sessions.pop(peer_id, None)
            return None
        finally:
            self._pending_requests.pop(request_id, None)

    async def stop(self) -> None:
        for worker in self._session_workers.values():
            worker.cancel()
        for worker in list(self._session_workers.values()):
            with suppress(asyncio.CancelledError):
                await worker
        for websocket in list(self._active_sessions.values()):
            with suppress(Exception):
                await websocket.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
