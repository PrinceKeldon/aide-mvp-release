"""
AIDE Mesh — Message Router v2
Always addresses by device_id. Resolves transport at the last moment.

This is the correct flow (Test C from the audit):
  1. Caller: router.send("android_device_01", "APPROVAL_REQUEST", payload)
  2. Router: look up device record by device_id
  3. Router: check capability in trust layer
  4. Router: resolve transport (Telegram chat_id, mesh WebSocket, etc.)
  5. Router: deliver

The Telegram chat_id never appears in the caller's code.
"""

from datetime import datetime, timezone
from loguru import logger
from typing import Optional

from mesh.device_registry import DeviceRegistry


# Which capability gates each message type
CAPABILITY_GATE = {
    "APPROVAL_REQUEST": "can_approve",
    "BRIEF_AVAILABLE": "can_receive_brief",
    "BRIEF_SHARE": "can_receive_brief",
    "EXECUTION_STATE": "can_receive_execution_updates",
    "TASK_REQUEST": "can_execute",
    "PEER_TASK_REQUEST": "can_execute",
    "CONTEXT_SHARE": "can_receive_memory",
    "ASK_PEER": None,
    "PING": None,  # ungated
    "PONG": None,
    "DELEGATE_TASK": "can_execute",
    "TASK_RESULT": None,
    "MEMORY_SYNC": "can_receive_memory",
    "PEER_RESPONSE": None,
    "HANDOFF_REQUEST": "can_execute",
    "HANDOFF_ACCEPT": "can_execute",
    "HANDOFF_DECLINE": "can_execute",
    "HANDOFF_RESULT": None,
    "STATE_UPDATE": "can_receive_state",
    "STATE_SYNC": "can_receive_state",
}


class MessageRouter:
    EXECUTION_MESSAGE_TYPES = {
        "TASK_REQUEST",
        "DELEGATE_TASK",
        "PEER_TASK_REQUEST",
        "ASK_PEER",
    }
    TELEGRAM_PREFERRED_TYPES = {
        "APPROVAL_REQUEST",
        "BRIEF_AVAILABLE",
        "BRIEF_SHARE",
        "EXECUTION_STATE",
    }

    def __init__(self, registry: DeviceRegistry, telegram_bot=None, mesh_node=None):
        self._registry = registry
        self._telegram = telegram_bot
        self._mesh_node = mesh_node

    def set_telegram(self, telegram_bot):
        self._telegram = telegram_bot

    def set_mesh_node(self, mesh_node):
        self._mesh_node = mesh_node

    def _select_transport(
        self,
        device: dict,
        message_type: str,
        mesh_endpoint: tuple[str, int] | None,
        telegram_chat_id: int | None,
    ) -> str | None:
        device_type = (device.get("device_type") or "").lower()

        if telegram_chat_id is not None:
            if device_type == "proxied_terminal":
                return "telegram"
            if message_type in {"PING", "PONG"}:
                return "telegram"

        if mesh_endpoint and message_type not in self.TELEGRAM_PREFERRED_TYPES:
            return "mesh"
        if telegram_chat_id is not None:
            return "telegram"
        if mesh_endpoint:
            return "mesh"
        return None

    async def send(
        self,
        to_device_id: str,
        message_type: str,
        payload: dict,
        from_device_id: str = "mac_primary",
    ) -> bool:
        result = await self.send_with_response(
            to_device_id=to_device_id,
            message_type=message_type,
            payload=payload,
            from_device_id=from_device_id,
        )
        return result is not None

    async def send_with_response(
        self,
        to_device_id: str,
        message_type: str,
        payload: dict,
        from_device_id: str = "mac_primary",
    ) -> dict | None:
        """
        Route a message to a device by its device_id.
        Chat_id is resolved internally — callers never use it.
        """
        # Step 1: resolve device (identity layer)
        device = self._registry.get_by_device_id(to_device_id)
        if not device:
            logger.warning(f"Router: device_id {to_device_id!r} not found")
            return False

        # Step 2: trust + capability check (trust layer)
        if not self._registry.is_trusted(to_device_id):
            logger.warning(
                f"Router: {message_type} blocked for {device['device_name']} "
                f"(peer is not trusted or has been revoked)"
            )
            return None
        gate = CAPABILITY_GATE.get(message_type)
        if gate and not device.get(gate):
            logger.warning(
                f"Router: {message_type} blocked for {device['device_name']} "
                f"(capability {gate!r} is off)"
            )
            return False

        # Step 3: resolve transport (transport layer)
        mesh_endpoint = self._registry.get_mesh_endpoint(to_device_id)
        telegram_chat_id = device.get("telegram_chat_id")
        if telegram_chat_id is None:
            telegram_chat_id = self._registry.get_telegram_chat_id(to_device_id)
        transport = self._select_transport(
            device,
            message_type,
            mesh_endpoint,
            telegram_chat_id,
        )

        if transport == "telegram":
            if message_type in self.EXECUTION_MESSAGE_TYPES:
                logger.warning(
                    f"Router: {message_type} blocked for {device['device_name']} "
                    "(telegram terminals cannot execute delegated tasks)"
                )
                return None
            device = {**device, "telegram_chat_id": telegram_chat_id}
            ok = await self._via_telegram(device, message_type, payload, from_device_id)
            return {"status": "sent"} if ok else None
        elif transport == "mesh":
            result = await self._via_mesh(device, message_type, payload)
            if result is None and telegram_chat_id is not None:
                logger.info(
                    f"Mesh failed for {device['device_name']}, falling back to Telegram"
                )
                ok = await self._via_telegram(
                    device, message_type, payload, from_device_id
                )
                return {"status": "sent"} if ok else None
            return result
        elif transport is None:
            logger.warning(f"Router: {device['device_name']} has no transport bound")
            return None
        else:
            logger.error(f"Router: unknown transport {transport!r}")
            return None

    async def broadcast(
        self,
        message_type: str,
        payload: dict,
        capability_filter: str = None,
    ) -> dict:
        """Send to all eligible devices. Returns delivery summary."""
        results = {"delivered": [], "blocked": [], "no_transport": []}

        devices = (
            self._registry.list_by_capability(capability_filter)
            if capability_filter
            else self._registry.list_all()
        )

        for d in devices:
            if capability_filter and not d.get(capability_filter):
                results["blocked"].append(d["device_name"])
                continue
            if not d.get("telegram_chat_id") and not d.get("mesh_host"):
                results["no_transport"].append(d["device_name"])
                continue
            ok = await self.send(d["device_id"], message_type, payload)
            (results["delivered"] if ok else results["blocked"]).append(
                d["device_name"]
            )

        logger.info(f"Broadcast {message_type}: {results}")
        return results

    async def _via_telegram(
        self, device: dict, message_type: str, payload: dict, from_device: str
    ) -> bool:
        chat_id = device.get("telegram_chat_id")
        if not chat_id:
            logger.warning(f"Router: {device['device_name']} has no Telegram chat_id")
            return False
        if not self._telegram:
            logger.error("Router: Telegram bot not wired")
            return False

        text = self._format_telegram(message_type, payload, device)
        try:
            await self._telegram.send_message(chat_id, text, parse_mode="Markdown")
            if message_type == "BRIEF_SHARE":
                await self._send_brief_approval_cards(device, payload)
            self._registry.update_last_seen(device["device_id"])
            logger.info(
                f"Router → Telegram: {message_type} → {device['device_name']} (chat_id {chat_id})"
            )
            return True
        except Exception as e:
            logger.error(
                f"Router: Telegram delivery failed to {device['device_name']}: {e}"
            )
            return False

    async def _send_brief_approval_cards(self, device: dict, payload: dict) -> None:
        if not hasattr(self._telegram, "send_message_with_keyboard"):
            return

        approval_items = [
            item
            for item in payload.get("items", [])
            if item.get("type") == "approval_request"
        ]
        for item in approval_items[:3]:
            content = item.get("content", {})
            approval_id = content.get("approval_id", "")
            if not approval_id:
                continue
            text = self._format_approval_card(item)
            keyboard = [
                [
                    {"text": "Approve", "callback_data": f"approve:{approval_id}"},
                    {"text": "Deny", "callback_data": f"decline:{approval_id}"},
                ]
            ]
            await self._telegram.send_message_with_keyboard(
                text,
                keyboard,
                chat_id=device.get("telegram_chat_id"),
            )

    def _format_approval_card(self, item: dict) -> str:
        content = item.get("content", {})
        lines = [
            "🛡 *Approval Needed*",
            "",
            f"*Action:* {content.get('action_description', item.get('title', 'Owner approval required'))}",
        ]
        action_type = content.get("action_type")
        if action_type:
            lines.append(f"*Tool:* `{action_type}`")
        consequence = content.get("consequence")
        if consequence:
            lines.append(f"*Impact:* {consequence}")
        expires_at = content.get("expires_at")
        if expires_at:
            lines.append(f"*Expires:* `{expires_at}`")
        lines.append("")
        lines.append("Choose an action below.")
        return "\n".join(lines)

    async def _via_mesh(
        self, device: dict, message_type: str, payload: dict
    ) -> dict | None:
        if not self._mesh_node:
            logger.error("Router: Mesh node not wired")
            return None
        endpoint = self._registry.get_mesh_endpoint(device["device_id"])
        if not endpoint:
            logger.warning(f"Router: {device['device_name']} has no mesh endpoint")
            return None
        host, port = endpoint
        peer = {
            "agent_id": device["device_id"],
            "address": host,
            "port": port,
        }
        parameters = {
            "to_device_id": device["device_id"],
            "message_type": message_type,
            "payload": payload,
        }
        result = await self._mesh_node.send_intent(peer, message_type, parameters)
        if result is None:
            logger.warning(f"Router: mesh delivery failed to {device['device_name']}")
            await self._mesh_node.report_failure(device["device_id"])
            return None
        self._registry.update_last_seen(device["device_id"])
        logger.info(
            f"Router → Mesh: {message_type} → {device['device_name']} ({host}:{port})"
        )
        if message_type == "ASK_PEER":
            await self._maybe_mirror_ask_peer_to_telegram(device, payload, result)
        return result


    async def _maybe_mirror_ask_peer_to_telegram(
        self, device: dict, payload: dict, result: dict | None
    ) -> None:
        if not payload.get("mirror_to_telegram"):
            return
        if not self._telegram:
            return
        chat_id = self._registry.get_telegram_chat_id(device["device_id"])
        if chat_id is None:
            return
        prompt = (payload.get("prompt") or "").strip()
        reply = ((result or {}).get("response") or "").strip()
        if not prompt and not reply:
            return
        lines = [f"Peer thread mirror: {device['device_name']}"]
        if prompt:
            lines.append("")
            lines.append(f"You asked: {prompt}")
        if reply:
            lines.append("")
            lines.append(f"{device['device_name']} replied: {reply}")
        try:
            await self._telegram.send_message(
                chat_id, "\n".join(lines), parse_mode=None
            )
        except Exception as e:
            logger.warning(
                f"ASK_PEER mirror to Telegram failed for {device['device_name']}: {e}"
            )

    def _format_telegram(self, message_type: str, payload: dict, device: dict) -> str:
        if message_type == "APPROVAL_REQUEST":
            action = payload.get("action_description", "perform an action")
            reason = payload.get("reason", "")
            approval_id = payload.get("approval_id", "")
            text = f"🛡 *Approval required*\n\n"
            text += f"AIDE wants to: *{action}*\n"
            if reason:
                text += f"\n_{reason}_\n"
            text += f"\nReply:\n"
            text += f"✅ `/approve {approval_id}` — confirm\n"
            text += f"❌ `/deny {approval_id}` — cancel"
            return text

        elif message_type in ("BRIEF_AVAILABLE", "BRIEF_SHARE"):
            items = payload.get("items", [])
            summary = payload.get("summary", {})
            text = f"☀️ *{summary.get('headline', 'Your day is prepared.')}*\n\n"
            icons = {
                "calendar_agenda": "🗓",
                "email_digest": "📬",
                "draft_message": "✉️",
                "schedule_suggestion": "📅",
                "prepared_task": "✅",
                "approval_request": "🛡",
            }
            for item in items[:5]:
                text += (
                    f"{icons.get(item.get('type',''), '•')} {item.get('title','')}\n"
                )
            text += f"\n_Open AIDE on Mac to act on items._"
            return text

        elif message_type == "EXECUTION_STATE":
            state = payload.get("state", "running")
            step = payload.get("current_step", "")
            icon = {
                "executing": "⏳",
                "completed": "✅",
                "failed": "❌",
                "queued": "🕐",
            }.get(state, "📡")
            return f"{icon} *Task update*\n{step}" if step else f"{icon} *Task {state}*"

        elif message_type == "PING":
            return f"🔵 AIDE mesh ping from Mac → {device['device_name']}\n\nReply /pong to confirm."

        else:
            import json

            return (
                f"📡 *{message_type}*\n```\n{json.dumps(payload, indent=2)[:300]}\n```"
            )
