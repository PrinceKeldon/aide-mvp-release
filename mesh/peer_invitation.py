"""
AIDE Mesh - Sovereign Peer Invitation
Signed invitation payloads for agent-to-agent trust bootstrap.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from mesh.identity import DeviceIdentity


def build_peer_invitation_payload(
    identity: DeviceIdentity,
    *,
    display_name: str | None = None,
    owner_name: str | None = None,
    primary_device_id: str | None = None,
    capabilities: list[str] | None = None,
    agent_version: str | None = None,
) -> str:
    payload = {
        "v": 1,
        "kind": "sovereign_peer_invitation",
        "peer_agent_id": identity.device_id,
        "pk": base64.b64encode(identity.public_key).decode("utf-8"),
        "display_name": display_name or f"{identity.device_name}'s AIDE",
        "owner_name": owner_name or "",
        "primary_device_id": primary_device_id or identity.device_id,
        "agent_version": agent_version or "",
        "capabilities": capabilities or [],
        "ts": int(datetime.now().timestamp()),
    }
    payload_json = json.dumps(payload, sort_keys=True)
    signature = identity.sign(payload_json.encode("utf-8"))
    payload["sig"] = base64.b64encode(signature).decode("utf-8")
    return json.dumps(payload)


def parse_peer_invitation_payload(payload_str: str) -> dict:
    try:
        payload = json.loads(payload_str)
        if payload.get("kind") != "sovereign_peer_invitation":
            raise ValueError("Payload is not a sovereign peer invitation")

        signature = base64.b64decode(payload.pop("sig"))
        payload_json = json.dumps(payload, sort_keys=True)
        public_key = base64.b64decode(payload["pk"])

        if not DeviceIdentity.verify(public_key, payload_json.encode("utf-8"), signature):
            raise ValueError("Invalid signature")

        qr_time = datetime.fromtimestamp(payload["ts"])
        now = datetime.now()
        if abs((now - qr_time).total_seconds()) > 300:
            raise ValueError("Invitation expired (older than 5 minutes)")

        return payload
    except Exception as exc:
        raise ValueError(f"Invalid peer invitation: {exc}")
