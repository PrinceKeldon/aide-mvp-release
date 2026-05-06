"""
AIDE Tools — Device Resolver Tool
Gives AIDE's agent loop the ability to look up and route to devices by name.

This is the bridge between AIDE's conversational layer and the mesh router.
It enforces strict resolution — no silent fallback to default node.

AIDE uses this tool when the user says things like:
  "Send an approval request to Midas"
  "Ping my Android"
  "Send the brief to AIDE Mobile"
  "What devices are connected?"
"""

import json
import uuid
from datetime import datetime, timezone
from tools.base import BaseTool, SafetyTier
from loguru import logger


class DeviceRouterTool(BaseTool):
    """
    Routes messages to specific devices by name.
    AIDE calls this instead of routing directly — ensures alias resolution
    happens before any message is dispatched.
    """

    @property
    def name(self) -> str:
        return "route_to_device"

    @property
    def description(self) -> str:
        return (
            "Send a message or action to a specific device in the owner mesh. "
            "Input: JSON with 'target' (device name or alias), 'message_type' "
            "(PING, ASK_PEER, APPROVAL_REQUEST, BRIEF_SHARE, EXECUTION_STATE), "
            "and 'payload' (dict). "
            'Example: {"target": "Midas", "message_type": "PING", "payload": {}}'
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.NOTIFY

    def __init__(
        self,
        alias_registry,
        resolution_guard,
        message_router,
        device_registry=None,
        memory=None,
    ):
        self._aliases = alias_registry
        self._guard = resolution_guard
        self._router = message_router
        self._registry = device_registry
        self._memory = memory

    def _set_active_peer_thread(self, device_id: str, canonical_name: str) -> None:
        if self._memory is None or not hasattr(self._memory, "store_fact"):
            return
        self._memory.store_fact("active_peer_thread_device_id", device_id)
        self._memory.store_fact("active_peer_thread_device_name", canonical_name)
        self._memory.store_fact("active_peer_thread_mode", "armed")
        self._memory.store_fact(
            "active_peer_thread_updated_at",
            datetime.now(timezone.utc).isoformat(),
        )

    async def execute(self, input_text) -> str:
        try:
            if isinstance(input_text, dict):
                data = input_text
            else:
                data = json.loads(str(input_text))
        except Exception:
            return "Error: input must be JSON with 'target', 'message_type', 'payload'."

        target = data.get("target", "").strip()
        message_type = data.get("message_type", "PING")
        payload = dict(data.get("payload", {}) or {})

        if not target:
            # ── Automatic Target Recovery ──────────────────────────
            if self._memory:
                recovered_id = self._memory.get_fact("last_explicit_target_id")
                if recovered_id:
                    logger.info(
                        f"DeviceRouterTool: Recovered target {recovered_id} from memory"
                    )
                    # We use the ID directly in the resolution guard
                    # but for the log we'll try to get the name
                    target = recovered_id

            if not target:
                return "Error: 'target' is required. Specify a device name."

        # ── Strict resolution ──────────────────────────────────
        device_id, clarification = self._guard.resolve_or_clarify(target)

        if clarification:
            # Cannot resolve — return the clarification message to AIDE
            # who will relay it to the user
            logger.warning(f"DeviceRouterTool: could not resolve {target!r}")
            return clarification

        # ── Routing assertion ──────────────────────────────────
        # Log the resolved target so misrouting is instantly visible
        canonical = self._aliases.get_canonical_name(device_id) or device_id
        logger.info(
            f"DeviceRouterTool routing:\n"
            f"  Requested target:    {target!r}\n"
            f"  Resolved device_id:  {device_id}\n"
            f"  Canonical name:      {canonical}\n"
            f"  Message type:        {message_type}"
        )

        try:
            self._guard.assert_not_default_substitution(target, device_id)
        except Exception as e:
            return f"Routing blocked: {e}"

        correlation_id = None
        if message_type == "ASK_PEER":
            correlation_id = str(payload.get("correlation_id") or uuid.uuid4().hex)
            payload["correlation_id"] = correlation_id
            self._set_active_peer_thread(device_id, canonical)
            if self._registry is not None and hasattr(
                self._registry, "append_peer_conversation_log"
            ):
                try:
                    self._registry.append_peer_conversation_log(
                        device_id,
                        role="local",
                        text=payload.get("prompt", ""),
                        via_transport="mesh",
                        correlation_id=correlation_id,
                        metadata={"source": "agent_route_tool"},
                    )
                except Exception:
                    logger.exception(
                        "DeviceRouterTool could not log outbound peer conversation"
                    )

        # ── Dispatch ───────────────────────────────────────────
        response = await self._router.send_with_response(
            to_device_id=device_id,
            message_type=message_type,
            payload=payload,
        )

        if response:
            if message_type == "ASK_PEER":
                if response.get("status") == "completed" and response.get("response"):
                    if self._registry is not None and hasattr(
                        self._registry, "append_peer_conversation_log"
                    ):
                        try:
                            self._registry.append_peer_conversation_log(
                                device_id,
                                role="peer",
                                text=str(response["response"]).strip(),
                                via_transport="mesh",
                                correlation_id=correlation_id,
                                metadata={"source": "agent_route_tool"},
                            )
                        except Exception:
                            logger.exception(
                                "DeviceRouterTool could not log inbound peer response"
                            )
                    return str(response["response"]).strip()
                if response.get("status") == "error":
                    return (
                        f"{canonical} could not answer. "
                        f"{response.get('error', 'The device returned an error.')}"
                    )
            return (
                f"Message sent to {canonical} ({message_type}). "
                f"Device ID: {device_id}"
            )
        else:
            return (
                f"Failed to deliver to {canonical}. "
                f"Device may be offline or transport not bound."
            )


class ListDevicesTool(BaseTool):
    """
    Lists all known devices in the owner mesh with their aliases.
    AIDE uses this to answer questions like "what devices do I have?"
    """

    @property
    def name(self) -> str:
        return "list_mesh_devices"

    @property
    def description(self) -> str:
        return (
            "List all devices in the owner mesh with their names, aliases, "
            "and online status. Use when user asks about connected devices."
        )

    @property
    def safety_tier(self) -> SafetyTier:
        return SafetyTier.AUTONOMOUS

    def __init__(self, alias_registry, device_registry):
        self._aliases = alias_registry
        self._registry = device_registry

    async def execute(self, input_text=None) -> str:
        aliases = self._aliases.list_all()
        devices = {d["device_id"]: d for d in self._registry.list_all()}

        if not aliases:
            return (
                "No devices registered in the mesh yet. "
                "Send /pair from any device to add it."
            )

        lines = ["Devices in your owner mesh:\n"]
        for a in aliases:
            did = a["device_id"]
            device = devices.get(did, {})
            online = "🟢 online" if device.get("online") else "⚫ offline"
            default = " (primary)" if a["is_default"] else ""
            transport = device.get("transport_type", "unknown")

            lines.append(
                f"• {a['canonical_name']}{default} — {online}\n"
                f"  ID: {did}\n"
                f"  Transport: {transport}\n"
                f"  Aliases: {', '.join(a['aliases'])}"
            )

        return "\n\n".join(lines)
