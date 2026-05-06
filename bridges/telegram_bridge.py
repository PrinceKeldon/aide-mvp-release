"""
AIDE Bridges — Telegram Bridge v2
Implements proper three-layer device registration.

The critical fix from the audit:
  /pair now creates a device identity FIRST, then binds Telegram as transport.
  Telegram chat_id is resolved to device_id at the edge — never used internally.

Flow:
  1. User sends /pair from Android
  2. Bridge creates device_id (identity layer)
  3. Bridge creates trust record (trust layer)
  4. Bridge binds telegram_chat_id to device_id (transport layer)
  5. All subsequent messages addressed to device_id, not chat_id

The decisive tests now pass:
  Test A: unbind_telegram() — device still exists in trust graph ✓
  Test B: rebind_telegram() — same device_id, different chat_id ✓
  Test C: router.send("android_device_01", ...) — no chat_id needed ✓
"""
from datetime import datetime, timezone
from loguru import logger
from typing import Optional, Callable, Awaitable

from mesh.device_registry import DeviceRegistry


class TelegramBridge:
    ACTIVE_PEER_THREAD_TIMEOUT_SECONDS = 600
    LOCAL_CHAT_ESCAPE_PREFIXES = ("local:", "aide:")

    DEVICE_COMMANDS = {
        "/pair", "/approve", "/approved",
        "/deny", "/denied", "/decline",
        "/edit", "/pong", "/ack", "/status", "/myid",
        "/peer", "/askpeer", "/replypeer", "/peerthread", "/donepeer",
    }

    def __init__(
        self,
        registry: DeviceRegistry,
        message_router=None,
        safety_gate=None,
        alias_registry=None,
        memory=None,
    ):
        self._registry = registry
        self._alias_registry = alias_registry
        self._router   = message_router
        self._safety   = safety_gate
        self._memory   = memory
        self._pending_approvals: dict = {}

    def set_router(self, router):
        self._router = router

    def _thread_fact_key(self, source_device_id: str, field: str) -> str:
        return f"telegram_peer_thread::{source_device_id}::{field}"

    def _set_active_peer_thread(self, source_device_id: str, target_device_id: str, target_name: str) -> None:
        if not self._memory:
            return
        self._memory.store_fact(self._thread_fact_key(source_device_id, "target_device_id"), target_device_id)
        self._memory.store_fact(self._thread_fact_key(source_device_id, "target_name"), target_name)
        self._memory.store_fact(self._thread_fact_key(source_device_id, "mode"), "armed")
        self._memory.store_fact(
            self._thread_fact_key(source_device_id, "updated_at"),
            datetime.now(timezone.utc).isoformat(),
        )

    def _clear_active_peer_thread(self, source_device_id: str) -> None:
        if not self._memory:
            return
        self._memory.store_fact(self._thread_fact_key(source_device_id, "target_device_id"), "")
        self._memory.store_fact(self._thread_fact_key(source_device_id, "target_name"), "")
        self._memory.store_fact(self._thread_fact_key(source_device_id, "mode"), "idle")
        self._memory.store_fact(
            self._thread_fact_key(source_device_id, "updated_at"),
            datetime.now(timezone.utc).isoformat(),
        )

    def _peer_thread_snapshot(self, source_device_id: str) -> dict:
        snapshot = {
            "device_id": "",
            "device_name": "",
            "mode": "idle",
            "status": "idle",
            "active": False,
            "updated_at": "",
            "expires_in_seconds": None,
        }
        if not self._memory:
            return snapshot

        device_id = (self._memory.get_fact(self._thread_fact_key(source_device_id, "target_device_id")) or "").strip()
        device_name = (self._memory.get_fact(self._thread_fact_key(source_device_id, "target_name")) or "").strip()
        mode = (self._memory.get_fact(self._thread_fact_key(source_device_id, "mode")) or "idle").strip().lower() or "idle"
        updated_at = self._memory.get_fact(self._thread_fact_key(source_device_id, "updated_at")) or ""
        snapshot.update(
            {
                "device_id": device_id,
                "device_name": device_name,
                "mode": mode,
                "status": mode,
                "updated_at": updated_at,
            }
        )
        if not device_id or not device_name:
            return snapshot
        if mode != "armed":
            return snapshot
        try:
            last = datetime.fromisoformat(updated_at)
        except Exception:
            snapshot["status"] = "stale"
            return snapshot

        age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
        if age > self.ACTIVE_PEER_THREAD_TIMEOUT_SECONDS:
            snapshot["status"] = "expired"
            snapshot["expires_in_seconds"] = 0
            return snapshot

        snapshot["active"] = True
        snapshot["status"] = "armed"
        snapshot["expires_in_seconds"] = max(0, int(self.ACTIVE_PEER_THREAD_TIMEOUT_SECONDS - age))
        return snapshot

    def _peer_thread_footer(self, source_device_id: str) -> str:
        snapshot = self._peer_thread_snapshot(source_device_id)
        if not snapshot["device_id"] or not snapshot["device_name"]:
            return ""
        if snapshot["active"]:
            remaining_seconds = snapshot["expires_in_seconds"] or 0
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            continuation = f"Automatic continuation: on for about {remaining_minutes} more min."
        elif snapshot["status"] == "expired":
            continuation = "Automatic continuation: off because the 10 minute peer window expired."
        else:
            continuation = "Automatic continuation: off."
        return (
            f"\n\nPeer thread: {snapshot['device_name']}\n"
            f"{continuation}\n"
            f"Use /replypeer to continue, /donepeer to exit, or prefix one message with local: for local AIDE chat."
        )

    def _extract_local_chat_override(self, text: str) -> str | None:
        lowered = text.lower()
        for prefix in self.LOCAL_CHAT_ESCAPE_PREFIXES:
            if lowered.startswith(prefix):
                return text[len(prefix):].strip()
        return None

    def _resolve_target(self, target: str) -> tuple[str | None, str | None]:
        target = (target or "").strip()
        if not target:
            return None, "Usage: /peer <device> <question>"
        if self._alias_registry:
            resolution = self._alias_registry.resolve_with_confidence(target)
            status = resolution.get("status")
            if status == "resolved":
                return resolution["device_id"], None
            if status == "ambiguous":
                names = ", ".join(candidate["canonical_name"] for candidate in resolution.get("candidates", []))
                return None, f"Target is ambiguous: {names}. Use a more specific device name."
            if status == "fuzzy":
                names = ", ".join(candidate["canonical_name"] for candidate in resolution.get("candidates", []))
                return None, f"Did you mean: {names}? Use the exact device name."
        device = next(
            (
                row for row in self._registry.list_all()
                if (row.get("device_name") or "").strip().lower() == target.lower()
            ),
            None,
        )
        if device:
            return device["device_id"], None
        return None, f"Could not resolve '{target}' to a known device."

    async def _mediate_peer_chat(
        self,
        *,
        source_device_id: str,
        source_device_name: str,
        chat_id: int,
        target_phrase: str,
        prompt: str,
    ) -> bool:
        if not self._router:
            await self._send_raw(chat_id, "Peer chat is unavailable because the message router is not wired.")
            return True

        target_device_id, error = self._resolve_target(target_phrase)
        if error:
            await self._send_raw(chat_id, error)
            return True
        if not target_device_id:
            await self._send_raw(chat_id, "Could not resolve that peer.")
            return True
        if target_device_id == source_device_id:
            await self._send_raw(chat_id, "This chat already represents that device. Use normal messages for local AIDE chat.")
            return True

        target_device = self._registry.get_by_device_id(target_device_id)
        if not target_device or not self._registry.is_trusted(target_device_id):
            await self._send_raw(chat_id, "That peer is not trusted right now.")
            return True
        if target_device.get("device_type") == "proxied_terminal":
            await self._send_raw(
                chat_id,
                f"{target_device['device_name']} is a proxied Telegram terminal, not a full peer node. "
                "Peer chat currently works with full nodes or mesh-bound executors only.",
            )
            return True
        if not self._registry.get_mesh_endpoint(target_device_id):
            await self._send_raw(chat_id, f"{target_device['device_name']} is not mesh-bound right now.")
            return True

        payload = {
            "prompt": prompt,
            "proxied_from_device_id": source_device_id,
            "proxied_from_device_name": source_device_name,
            "mirror_to_telegram": False,
        }
        response = await self._router.send_with_response(
            to_device_id=target_device_id,
            message_type="ASK_PEER",
            payload=payload,
            from_device_id=source_device_id,
        )
        if not response:
            await self._send_raw(
                chat_id,
                f"Failed to deliver to {target_device['device_name']}. Device may be offline or transport not bound.",
            )
            return True
        if response.get("status") == "error":
            await self._send_raw(
                chat_id,
                f"{target_device['device_name']} could not answer. {response.get('error', 'The device returned an error.')}",
            )
            return True

        answer = str(response.get("response") or "").strip() or "Peer answered without a text reply."
        self._set_active_peer_thread(source_device_id, target_device_id, target_device["device_name"])
        await self._send_raw(chat_id, f"{answer}{self._peer_thread_footer(source_device_id)}")
        return True

    # ── Pairing — the critical fix ─────────────────────────────

    async def handle_pair(
        self,
        chat_id: int,
        username: Optional[str],
        user_provided_name: Optional[str] = None,
    ) -> str:
        """
        /pair handler — three-layer registration.

        Step 1: Check if this chat_id is already bound to a device.
        Step 2: If yes, reconnect. Update last_seen. Return status.
        Step 3: If no, create device identity → trust → bind transport.

        The device_id is independent of telegram_chat_id.
        """

        # ── Already registered? ────────────────────────────────
        existing_device_id = self._registry.get_device_id_by_telegram(chat_id)
        if existing_device_id:
            device = self._registry.get_by_device_id(existing_device_id)
            self._registry.update_last_seen(existing_device_id)
            self._ensure_alias_registration(existing_device_id, device["device_name"])
            if device.get("device_type") == "proxied_terminal":
                self._registry.update_capabilities(
                    existing_device_id,
                    can_execute=0,
                    can_receive_memory=0,
                    can_receive_context="summary_only",
                )
            logger.info(f"Device reconnected: {device['device_name']} ({existing_device_id})")
            return (
                f"✅ *{device['device_name']}* reconnected.\n\n"
                f"This device is a trusted node in your AIDE mesh.\n"
                f"Device ID: `{existing_device_id}`\n\n"
                f"Commands:\n"
                f"/peer — ask a full node peer\n"
                f"/replypeer — continue the active peer thread\n"
                f"/peerthread — show the active peer thread\n"
                f"/donepeer — exit peer mode\n"
                f"/approve — approve a pending request\n"
                f"/deny — cancel a request\n"
                f"/status — show this device's capabilities\n"
                f"/myid — show your device identity"
            )

        # ── New device — three-layer registration ──────────────
        device_name = (
            user_provided_name
            or (f"@{username}'s Phone" if username else "Android Device")
        )

        # Layer 1 + 2: Create identity and trust
        device_id = self._registry.create_device(
            device_name=device_name,
            device_type="proxied_terminal",
        )

        # Layer 3: Bind transport
        self._registry.bind_telegram(device_id, chat_id, username)
        self._ensure_alias_registration(device_id, device_name)

        logger.info(f"New device registered: {device_name} → {device_id} ↔ chat_id {chat_id}")

        return (
            f"📱 *{device_name}* is now in your AIDE mesh.\n\n"
            f"Device ID: `{device_id}`\n"
            f"Role: Proxied terminal\n"
            f"Transport: Telegram\n\n"
            f"This device can:\n"
            f"✅ Approve and deny requests\n"
            f"☀️ Receive your daily brief\n"
            f"⏳ Receive task updates\n\n"
            f"_Your private data stays on your Mac. "
            f"Nothing else is shared unless you change permissions._\n\n"
            f"Commands:\n"
            f"/peer — ask a full node peer\n"
            f"/replypeer — continue the active peer thread\n"
            f"/peerthread — show the active peer thread\n"
            f"/donepeer — exit peer mode\n"
            f"/approve — approve pending request\n"
            f"/deny — cancel request\n"
            f"/status — show capabilities\n"
            f"/myid — show device identity"
        )

    # ── Inbound message handler ────────────────────────────────

    async def handle_incoming(self, chat_id: int, text: str) -> bool:
        """
        Handle an incoming Telegram message.
        Returns True if handled as a device-agent message.
        Returns False if it should be handled as normal user chat.

        Critical: chat_id is resolved to device_id here, at the edge.
        Everything downstream works with device_id only.
        """
        text_stripped = text.strip()
        cmd = text_stripped.split()[0].lower() if text_stripped.startswith("/") else ""
        logger.info(f"TelegramBridge incoming from chat_id {chat_id}: cmd={cmd!r} text={text_stripped!r}")

        if cmd == "/pair":
            parts = text_stripped.split(maxsplit=1)
            name  = parts[1] if len(parts) > 1 else None
            username = None  # will be filled by telegram_bot.py
            response = await self.handle_pair(chat_id, username, name)
            await self._send_raw(chat_id, response)
            return True

        device_id = self._registry.get_device_id_by_telegram(chat_id)
        if not device_id:
            if cmd in self.DEVICE_COMMANDS:
                await self._send_raw(
                    chat_id,
                    "❓ This device is not registered.\n\nSend /pair to connect it to your AIDE mesh."
                )
                return True
            return False

        # Resolve device (identity-first from here)
        device = self._registry.get_by_device_id(device_id)
        self._registry.update_last_seen(device_id)

        if not cmd:
            local_override = self._extract_local_chat_override(text_stripped)
            if local_override is not None:
                return False
            snapshot = self._peer_thread_snapshot(device_id)
            if snapshot["active"]:
                return await self._mediate_peer_chat(
                    source_device_id=device_id,
                    source_device_name=device["device_name"],
                    chat_id=chat_id,
                    target_phrase=snapshot["device_name"],
                    prompt=text_stripped,
                )
            return False

        if cmd not in self.DEVICE_COMMANDS:
            return False

        if cmd in {"/approve", "/approved"}:
            await self._handle_approve(device_id, device, text_stripped)
        elif cmd in {"/deny", "/denied", "/decline"}:
            await self._handle_deny(device_id, device, text_stripped)
        elif cmd in {"/peer", "/askpeer"}:
            parts = text_stripped.split(maxsplit=2)
            if len(parts) < 3 or not parts[2].strip():
                await self._send_raw(chat_id, "Usage: /peer <device> <question>")
                return True
            await self._mediate_peer_chat(
                source_device_id=device_id,
                source_device_name=device["device_name"],
                chat_id=chat_id,
                target_phrase=parts[1],
                prompt=parts[2].strip(),
            )
        elif cmd == "/replypeer":
            snapshot = self._peer_thread_snapshot(device_id)
            if not snapshot["device_id"] or not snapshot["device_name"]:
                await self._send_raw(chat_id, "No active peer thread. Use /peer <device> <question> first.")
                return True
            parts = text_stripped.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await self._send_raw(chat_id, f"Active peer thread: {snapshot['device_name']}\nUsage: /replypeer <message>")
                return True
            await self._mediate_peer_chat(
                source_device_id=device_id,
                source_device_name=device["device_name"],
                chat_id=chat_id,
                target_phrase=snapshot["device_name"],
                prompt=parts[1].strip(),
            )
        elif cmd == "/peerthread":
            snapshot = self._peer_thread_snapshot(device_id)
            if not snapshot["device_id"] or not snapshot["device_name"]:
                await self._send_raw(chat_id, "No active peer thread. Use /peer <device> <question> first.")
                return True
            if snapshot["active"]:
                remaining_seconds = snapshot["expires_in_seconds"] or 0
                remaining_minutes = max(1, (remaining_seconds + 59) // 60)
                continuation = f"on ({remaining_minutes} min left)"
            elif snapshot["status"] == "expired":
                continuation = "off (expired)"
            else:
                continuation = f"off ({snapshot['status']})"
            await self._send_raw(
                chat_id,
                f"Active peer thread\n"
                f"Device: {snapshot['device_name']}\n"
                f"ID: `{snapshot['device_id']}`\n"
                f"Automatic continuation: {continuation}\n"
                f"Reply with: /replypeer <message>\n"
                f"Exit peer mode: /donepeer\n"
                f"Force local chat once: prefix with local:",
            )
        elif cmd == "/donepeer":
            self._clear_active_peer_thread(device_id)
            await self._send_raw(chat_id, "Peer thread mode closed. Telegram is back to local AIDE chat.")
        elif cmd == "/pong":
            await self._send_raw(chat_id, f"🔵 *{device['device_name']}* confirmed in mesh.")
            owner_chat_id = self._get_owner_chat_id()
            if owner_chat_id and self._router and hasattr(self._router, "_telegram"):
                try:
                    await self._router._telegram.send_message(
                        chat_id=owner_chat_id,
                        text=f"🟢 *{device['device_name']}* is online and responded to ping.",
                    )
                except Exception as e:
                    logger.warning(f"Pong notify failed: {e}")
        elif cmd == "/status":
            await self._send_raw(chat_id, self._format_status(device))
        elif cmd == "/myid":
            await self._send_raw(
                chat_id,
                f"🪪 *Device Identity*\n\n"
                f"Name: {device['device_name']}\n"
                f"ID: `{device_id}`\n"
                f"Type: {device['device_type']}\n"
                f"Transport: {device['transport_type'] or 'none'}\n"
                f"Relationship: {device['relationship_type']}\n"
                f"Last seen: {device['last_seen_at'][:19] if device['last_seen_at'] else 'now'}"
            )

        return True

    # ── Approval management ────────────────────────────────────

    def register_approval(
        self,
        approval_id: str,
        action_description: str,
        callback: Callable[[str, str], Awaitable[None]],
        timeout_seconds: int = 300,
    ):
        """Register a pending approval. Called by the safety gate."""
        import time
        self._pending_approvals[approval_id] = {
            "callback":           callback,
            "action_description": action_description,
            "expires_at":         time.time() + timeout_seconds,
        }
        logger.info(f"Approval pending: {approval_id} — {action_description}")

    async def _handle_approve(self, device_id: str, device: dict, text: str):
        parts = text.split()
        approval_id = parts[1] if len(parts) > 1 else None
        logger.info(
            f"Approval command from {device['device_name']} ({device_id}); "
            f"approval_id={approval_id!r}; pending safety approvals={list(self._safety._pending.keys()) if self._safety else []}"
        )
        resolved = await self._resolve_approval(device_id, device["device_name"], "approved", approval_id)
        chat_id = device["telegram_chat_id"]
        msg = "✅ Approved. AIDE will proceed." if resolved else "✅ Got it — no pending approvals to resolve."
        await self._send_raw(chat_id, msg)

    async def _handle_deny(self, device_id: str, device: dict, text: str):
        parts = text.split()
        approval_id = parts[1] if len(parts) > 1 else None
        await self._resolve_approval(device_id, device["device_name"], "denied", approval_id)
        await self._send_raw(device["telegram_chat_id"], "❌ Cancelled. AIDE will not proceed.")

    async def _resolve_approval(
        self, device_id: str, device_name: str, decision: str, approval_id: str = None
    ) -> bool:
        import time
        now = time.time()
        logger.info(
            f"Resolving approval: device={device_name} decision={decision} "
            f"approval_id={approval_id!r} bridge_pending={list(self._pending_approvals.keys())} "
            f"safety_pending={list(self._safety._pending.keys()) if self._safety else []}"
        )

        if approval_id and approval_id in self._pending_approvals:
            entry = self._pending_approvals.pop(approval_id)
            if entry["expires_at"] > now:
                await entry["callback"](decision, device_name)
                logger.info(f"Approval resolved: {approval_id} → {decision} by {device_name}")
                return True

        # Resolve most recent
        for aid in sorted(self._pending_approvals.keys(), reverse=True):
            entry = self._pending_approvals[aid]
            if entry["expires_at"] > now:
                self._pending_approvals.pop(aid)
                await entry["callback"](decision, device_name)
                logger.info(f"Approval resolved: {aid} → {decision} by {device_name}")
                return True
            # Also resolve safety gate futures
        if self._safety:
            if approval_id:
                resolved = self._safety.resolve_approval(
                    approval_id,
                    decision == "approved",
                    resolved_by=device_name,
                )
                if resolved:
                    logger.info(f"Safety gate resolved: {approval_id} → {decision}")
                    return True
            else:
                # Try resolving any pending safety gate approval
                for aid in list(self._safety._pending.keys()):
                    resolved = self._safety.resolve_approval(
                        aid,
                        decision == "approved",
                        resolved_by=device_name,
                    )
                    if resolved:
                        logger.info(f"Safety gate resolved: {aid} → {decision}")
                        return True

        return False

    def _format_status(self, device: dict) -> str:
        lines = [
            f"📱 *{device['device_name']}*",
            f"",
            f"Identity",
            f"  ID: `{device['device_id']}`",
            f"  Type: {device['device_type']}",
            f"",
            f"Trust",
            f"  Relationship: {device['relationship_type']}",
            f"  {'✅' if device['can_approve'] else '✗'} Approve requests",
            f"  {'✅' if device['can_receive_brief'] else '✗'} Receive daily brief",
            f"  {'✅' if device['can_receive_execution_updates'] else '✗'} Task updates",
            f"  {'✅' if device['can_execute'] else '✗'} Execute locally",
            f"",
            f"Transport",
            f"  Type: {device['transport_type'] or 'none'}",
            f"  {'Bound' if device['telegram_chat_id'] else 'Unbound'}",
        ]
        return "\n".join(lines)

    async def _send_raw(self, chat_id: int, text: str):
        """Send directly to a chat_id. Used only for bridge responses."""
        if self._router and hasattr(self._router, "_telegram"):
            try:
                await self._router._telegram.send_message(chat_id, text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Bridge send failed to chat_id {chat_id}: {e}")
        else:
            logger.warning(f"Bridge: no Telegram available to send to chat_id {chat_id}")

    def _ensure_alias_registration(self, device_id: str, device_name: str) -> None:
        if not self._alias_registry:
            return

        existing = next(
            (entry for entry in self._alias_registry.list_all() if entry["device_id"] == device_id),
            None,
        )
        if existing:
            return

        aliases = [device_name.lower(), "android", "phone", "mobile"]
        self._alias_registry.register(
            device_id=device_id,
            canonical_name=device_name,
            aliases=aliases,
            is_default=False,
        )

    def _get_owner_chat_id(self) -> Optional[int]:
        if not self._memory:
            return None
        chat_id = self._memory.get_fact("telegram_chat_id")
        if not chat_id:
            return None
        try:
            return int(chat_id)
        except ValueError:
            logger.warning(f"Invalid owner Telegram chat_id in memory: {chat_id}")
            return None

def build_bridge(registry, alias_registry=None, safety_gate=None, message_router=None, memory=None):
    return TelegramBridge(
        registry=registry,
        message_router=message_router,
        safety_gate=safety_gate,
        alias_registry=alias_registry,
        memory=memory,
    )
