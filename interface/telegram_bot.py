"""
AIDE — Telegram interface
Handles owner chat, sovereign terminal commands, approval buttons, and emergency stop.
"""

import asyncio
import time
from datetime import datetime, timezone

from loguru import logger
from telegram.error import BadRequest
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bridges.telegram_bridge import TelegramBridge
from core.agent import AideAgent
from core.logging_utils import LogRateLimiter
from core.onboarding import Onboarding
from core.safety import SafetyGate
from core.settings import settings


class TelegramInterface:
    STREAM_EDIT_INTERVAL_SECONDS = 0.2
    STREAM_EDIT_MIN_CHARS = 12
    SHUTDOWN_TIMEOUT_SECONDS = 5.0
    ACTIVE_PEER_THREAD_TIMEOUT_SECONDS = 600
    LOCAL_CHAT_ESCAPE_PREFIXES = ("local:", "aide:", "aide:")

    async def _edit_placeholder_text(
        self, placeholder, text: str, *, last_sent: str = ""
    ) -> bool:
        if not text or text == last_sent:
            return False
        try:
            await placeholder.edit_text(text)
            return True
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                return False
            raise

    def __init__(
        self,
        agent: AideAgent,
        onboarding: Onboarding,
        safety: SafetyGate,
        bridge: TelegramBridge = None,
    ):
        self._agent = agent
        self._onboarding = onboarding
        self._safety = safety
        self._bridge = bridge
        self._app = None
        if settings.telegram_bot_token.strip():
            self._app = (
                Application.builder()
                .token(settings.telegram_bot_token)
                .concurrent_updates(True)
                .build()
            )
        self._chat_id: int | None = None
        self._telegram_available = False
        self._notify_log_limiter = LogRateLimiter()
        safety.register_notify_callback(self._notify)
        self._register_handlers()

        saved_id = agent._memory.get_fact("telegram_chat_id")
        if saved_id:
            self._chat_id = int(saved_id)
            logger.info(f"Loaded chat_id from memory: {saved_id}")

    def _register_handlers(self) -> None:
        if self._app is None:
            return
        a = self._app
        a.add_handler(CommandHandler("start", self._cmd_start))
        a.add_handler(CommandHandler("pair", self._cmd_pair))
        a.add_handler(CommandHandler("reset", self._cmd_reset))
        a.add_handler(CommandHandler("status", self._cmd_status))
        a.add_handler(CommandHandler("stop", self._cmd_stop))
        a.add_handler(CommandHandler("resume", self._cmd_resume))
        a.add_handler(CommandHandler("memory", self._cmd_memory))
        a.add_handler(CommandHandler("pong", self._cmd_pong))
        a.add_handler(CommandHandler("approve", self._cmd_approve))
        a.add_handler(CommandHandler("approved", self._cmd_approve))
        a.add_handler(CommandHandler("deny", self._cmd_deny))
        a.add_handler(CommandHandler("denied", self._cmd_deny))
        a.add_handler(CommandHandler("decline", self._cmd_deny))
        a.add_handler(CommandHandler("myid", self._cmd_myid))
        a.add_handler(CommandHandler(["peer", "askpeer"], self._cmd_peer))
        a.add_handler(CommandHandler("replypeer", self._cmd_replypeer))
        a.add_handler(CommandHandler("peerthread", self._cmd_peerthread))
        a.add_handler(CommandHandler("donepeer", self._cmd_donepeer))
        a.add_handler(CallbackQueryHandler(self._on_callback))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        a.add_error_handler(self._on_error)

    def _is_owner_chat(self, chat_id: int) -> bool:
        memory = getattr(self._agent, "_memory", None)
        if memory is None:
            return False
        owner_chat_id = memory.get_fact("telegram_chat_id")
        return owner_chat_id is not None and str(chat_id) == str(owner_chat_id)

    def _paired_terminal_device(self, chat_id: int):
        if not self._bridge or not hasattr(self._bridge, "_registry"):
            return None
        try:
            device_id = self._bridge._registry.get_device_id_by_telegram(chat_id)
            if not device_id:
                return None
            return self._bridge._registry.get_by_device_id(device_id)
        except Exception:
            return None

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return

        try:
            owner_chat_id = self._agent._memory.get_fact("telegram_chat_id")
            if not owner_chat_id:
                self._chat_id = msg.chat_id
                self._agent._memory.store_fact("telegram_chat_id", str(msg.chat_id))
                if not self._onboarding.is_complete():
                    reply = await self._onboarding.start()
                else:
                    name = self._agent._memory.get_fact("user_name") or ""
                    reply = (
                        f"AIDE online{', ' + name if name else ''}.\n"
                        "Running on your device. What do you need?"
                    )
            elif str(msg.chat_id) == str(owner_chat_id):
                self._chat_id = msg.chat_id
                if not self._onboarding.is_complete():
                    reply = await self._onboarding.start()
                else:
                    name = self._agent._memory.get_fact("user_name") or ""
                    reply = (
                        f"AIDE online{', ' + name if name else ''}.\n"
                        "Running on your device. What do you need?"
                    )
            else:
                terminal = self._paired_terminal_device(msg.chat_id)
                if terminal:
                    terminal_name = terminal.get("device_name") or "This terminal"
                    reply = (
                        f"{terminal_name} is online.\n"
                        "This chat is paired as a sovereign terminal.\n"
                        "Use /peer <device> <question> to talk to another node, /peerthread to inspect the active peer thread, or /status for this terminal."
                    )
                else:
                    reply = (
                        "This chat is not your owner control channel.\n"
                        "Send /pair here to register it as a sovereign terminal."
                    )
            await msg.reply_text(reply)
        except Exception as e:
            logger.exception(f"Telegram /start failed: {e}")
            await msg.reply_text("Something went wrong. Send /start to try again.")

    async def _cmd_pair(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return

        if self._is_owner_chat(msg.chat_id):
            await msg.reply_text(
                "This chat is your owner control channel. Use /pair from the device you want to add."
            )
            return

        if not self._bridge:
            await msg.reply_text("AIDE device bridge not available.")
            return

        username = msg.from_user.username if msg.from_user else None
        provided_name = " ".join(ctx.args).strip() if ctx.args else None
        response = await self._bridge.handle_pair(
            chat_id=msg.chat_id,
            username=username,
            user_provided_name=provided_name or None,
        )
        await msg.reply_text(response, parse_mode="Markdown")

    async def _cmd_reset(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._agent.clear_history()
        self._clear_active_peer_thread()
        await update.message.reply_text(  # type: ignore[union-attr]
            "Conversation reset. Memory intact."
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return

        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/status"
            )
            if handled:
                return

        from core.llm import LLMClient

        llm = LLMClient()
        ollama_up = await llm.is_ollama_running()
        name = self._agent._memory.get_fact("user_name") or "—"
        tier = (
            self._agent._memory.get_fact("default_safety_tier")
            or settings.default_safety_tier
        )
        await msg.reply_text(
            f"AIDE status\n"
            f"User: {name}\n"
            f"LLM: {'Ollama (local)' if ollama_up else 'Groq fallback'}\n"
            f"Model: {settings.ollama_model if ollama_up else settings.groq_model}\n"
            f"Safety tier: {tier}\n"
            f"Memory: online"
        )

    async def _cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._safety.emergency_stop()
        await update.message.reply_text(  # type: ignore[union-attr]
            "All autonomous actions stopped. Send /resume when ready."
        )

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._safety.resume()
        await update.message.reply_text("Resumed.")  # type: ignore[union-attr]

    async def _cmd_memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        facts = self._agent._memory.get_all_facts()
        lines = [
            f"{k}: {v}" for k, v in facts.items() if not k.startswith("onboarding")
        ]
        await update.message.reply_text(  # type: ignore[union-attr]
            ("What I know about you:\n" + "\n".join(lines))
            if lines
            else "Nothing stored yet."
        )

    async def _cmd_pong(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._forward_bridge_command(update, "/pong")

    async def _cmd_approve(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if not msg:
            return
        logger.info(
            f"Telegram approve command received from chat_id {msg.chat_id}: {msg.text!r}"
        )

        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/approve"
            )
            if handled:
                return

        await self._resolve_owner_approval(update, approved=True)

    async def _cmd_deny(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return
        logger.info(
            f"Telegram deny command received from chat_id {msg.chat_id}: {msg.text!r}"
        )

        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/deny"
            )
            if handled:
                return

        await self._resolve_owner_approval(update, approved=False)

    async def _cmd_myid(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return

        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/myid"
            )
            if handled:
                return

        await msg.reply_text("This chat is not a paired sovereign terminal.")

    def _active_peer_thread(self) -> tuple[str | None, str | None]:
        memory = getattr(self._agent, "_memory", None)
        if memory is None:
            return None, None
        device_id = memory.get_fact("active_peer_thread_device_id")
        device_name = memory.get_fact("active_peer_thread_device_name")
        return device_id, device_name

    def _peer_thread_snapshot(self) -> dict:
        memory = getattr(self._agent, "_memory", None)
        snapshot = {
            "device_id": None,
            "device_name": None,
            "mode": "idle",
            "updated_at": "",
            "active": False,
            "status": "idle",
            "expires_in_seconds": None,
        }
        if memory is None:
            return snapshot

        device_id, device_name = self._active_peer_thread()
        mode = (
            memory.get_fact("active_peer_thread_mode") or ""
        ).strip().lower() or "idle"
        updated_at = memory.get_fact("active_peer_thread_updated_at") or ""
        snapshot.update(
            {
                "device_id": device_id,
                "device_name": device_name,
                "mode": mode,
                "updated_at": updated_at,
            }
        )
        if not device_id or not device_name:
            return snapshot
        if mode != "armed":
            snapshot["status"] = mode
            return snapshot
        try:
            last = datetime.fromisoformat(updated_at)
        except Exception:
            snapshot["status"] = "stale"
            return snapshot

        age = (
            datetime.now(timezone.utc) - last.astimezone(timezone.utc)
        ).total_seconds()
        if age > self.ACTIVE_PEER_THREAD_TIMEOUT_SECONDS:
            snapshot["status"] = "expired"
            snapshot["expires_in_seconds"] = 0
            return snapshot

        snapshot["active"] = True
        snapshot["status"] = "armed"
        snapshot["expires_in_seconds"] = max(
            0, int(self.ACTIVE_PEER_THREAD_TIMEOUT_SECONDS - age)
        )
        return snapshot

    def _set_active_peer_thread_mode(self, mode: str) -> None:
        memory = getattr(self._agent, "_memory", None)
        if memory is None or not hasattr(memory, "store_fact"):
            return
        memory.store_fact("active_peer_thread_mode", mode)
        memory.store_fact(
            "active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat()
        )

    def _clear_active_peer_thread(self) -> None:
        memory = getattr(self._agent, "_memory", None)
        if memory is None or not hasattr(memory, "store_fact"):
            return
        memory.store_fact("active_peer_thread_mode", "idle")
        memory.store_fact("active_peer_thread_device_id", "")
        memory.store_fact("active_peer_thread_device_name", "")
        memory.store_fact(
            "active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat()
        )

    def _peer_thread_state(self) -> tuple[bool, str | None, str | None]:
        snapshot = self._peer_thread_snapshot()
        return snapshot["active"], snapshot["device_id"], snapshot["device_name"]

    def _peer_thread_footer(self) -> str:
        snapshot = self._peer_thread_snapshot()
        if not snapshot["device_id"] or not snapshot["device_name"]:
            return ""

        if snapshot["active"]:
            remaining_seconds = snapshot["expires_in_seconds"] or 0
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            continuation = (
                f"Automatic continuation: on for about {remaining_minutes} more min."
            )
        elif snapshot["status"] == "expired":
            continuation = (
                "Automatic continuation: off because the 10 minute peer window expired."
            )
        else:
            continuation = "Automatic continuation: off."

        return (
            f"\n\nPeer thread: {snapshot['device_name']}\n"
            f"{continuation}\n"
            f"Use /replypeer to continue explicitly, /donepeer to exit, or prefix one message with local: for local chat."
        )

    def _extract_local_chat_override(self, text: str) -> str | None:
        lowered = text.lower()
        for prefix in self.LOCAL_CHAT_ESCAPE_PREFIXES:
            if lowered.startswith(prefix):
                return text[len(prefix) :].strip()
        return None

    def _has_explicit_target_override(
        self, text: str, active_device_id: str | None
    ) -> bool:
        interceptor = getattr(self._agent, "_target_interceptor", None)
        if interceptor is None:
            return False
        try:
            resolution = interceptor.detect_and_resolve(text)
        except Exception:
            return False
        if not getattr(resolution, "has_explicit_target", False):
            return False
        if getattr(resolution, "status", "") != "resolved":
            return True
        return getattr(resolution, "device_id", None) != active_device_id

    def _should_route_to_active_peer(self, chat_id: int, text: str) -> bool:
        if not self._is_owner_chat(chat_id):
            return False
        if self._extract_local_chat_override(text) is not None:
            return False
        active, device_id, _device_name = self._peer_thread_state()
        if active and self._has_explicit_target_override(text, device_id):
            return False
        return active and bool(device_id)

    async def _cmd_peer(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        if not msg:
            return
        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/peer"
            )
            if handled:
                return
        if not self._is_owner_chat(msg.chat_id):
            await msg.reply_text("Use /peer from your owner control channel.")
            return
        if len(ctx.args) < 2:
            await msg.reply_text("Usage: /peer <device> <question>")
            return

        target = ctx.args[0]
        prompt = " ".join(ctx.args[1:]).strip()
        if not prompt:
            await msg.reply_text("Usage: /peer <device> <question>")
            return

        await msg.chat.send_action("typing")
        try:
            reply = await self._agent.run(f"Ask {target} {prompt}")
            self._set_active_peer_thread_mode("armed")
            reply = f"{reply}{self._peer_thread_footer()}"
        except RuntimeError as e:
            reply = f"I can't think right now: {e}"
        except Exception as e:
            reply = "Something went wrong. Try again."
            logger.exception(e)
        await msg.reply_text(reply)

    async def _cmd_replypeer(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if not msg:
            return
        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/replypeer"
            )
            if handled:
                return
        if not self._is_owner_chat(msg.chat_id):
            await msg.reply_text("Use /replypeer from your owner control channel.")
            return
        device_id, device_name = self._active_peer_thread()
        if not device_id or not device_name:
            await msg.reply_text(
                "No active peer thread. Use /peer <device> <question> first, or open Ask Device in /mesh."
            )
            return

        prompt = " ".join(ctx.args).strip()
        if not prompt:
            await msg.reply_text(
                f"Active peer thread: {device_name}\nUsage: /replypeer <message>"
            )
            return

        await msg.chat.send_action("typing")
        try:
            reply = await self._agent.run(f"Ask {device_name} {prompt}")
            self._set_active_peer_thread_mode("armed")
            reply = f"{reply}{self._peer_thread_footer()}"
        except RuntimeError as e:
            reply = f"I can't think right now: {e}"
        except Exception as e:
            reply = "Something went wrong. Try again."
            logger.exception(e)
        await msg.reply_text(reply)

    async def _cmd_peerthread(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if not msg:
            return
        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/peerthread"
            )
            if handled:
                return
        if not self._is_owner_chat(msg.chat_id):
            await msg.reply_text("Use /peerthread from your owner control channel.")
            return
        snapshot = self._peer_thread_snapshot()
        active, device_id, device_name = (
            snapshot["active"],
            snapshot["device_id"],
            snapshot["device_name"],
        )
        if not device_id or not device_name:
            await msg.reply_text(
                "No active peer thread. Use /peer <device> <question> first."
            )
            return
        if active:
            remaining_seconds = snapshot["expires_in_seconds"] or 0
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            continuation = f"on ({remaining_minutes} min left)"
        elif snapshot["status"] == "expired":
            continuation = "off (expired)"
        else:
            continuation = f"off ({snapshot['status']})"
        await msg.reply_text(
            f"Active peer thread\n"
            f"Device: {device_name}\n"
            f"ID: {device_id}\n"
            f"Automatic continuation: {continuation}\n"
            f"Reply with: /replypeer <message>\n"
            f"Exit peer mode: /donepeer\n"
            f"Force local chat once: prefix with local:"
        )

    async def _cmd_donepeer(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        if not msg:
            return
        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or "/donepeer"
            )
            if handled:
                return
        if not self._is_owner_chat(msg.chat_id):
            await msg.reply_text("Use /donepeer from your owner control channel.")
            return
        self._clear_active_peer_thread()
        await msg.reply_text(
            "Peer thread mode closed. Telegram is back to normal local chat."
        )

    async def _forward_bridge_command(self, update: Update, fallback_text: str) -> None:
        msg = update.message
        if not msg:
            return

        if self._bridge:
            handled = await self._bridge.handle_incoming(
                msg.chat_id, msg.text or fallback_text
            )
            if handled:
                return

        await msg.reply_text(
            "This chat is not a paired sovereign terminal. Send /pair from that device first."
        )

    async def _resolve_owner_approval(self, update: Update, approved: bool) -> None:
        msg = update.message
        if not msg:
            return

        parts = (msg.text or "").split()
        action_id = parts[1] if len(parts) > 1 else None

        if action_id is None and len(self._safety._approval_callbacks) == 1:
            action_id = next(iter(self._safety._approval_callbacks))

        if not action_id:
            await msg.reply_text(
                "No approval ID provided and no single pending approval exists."
            )
            return

        resolved = self._safety.resolve_approval(
            action_id, approved, resolved_by="owner"
        )
        if resolved:
            await msg.reply_text("Approved." if approved else "Cancelled.")
        else:
            await msg.reply_text("This action already expired.")

    async def _on_message(self, update: Update, ctx) -> None:
        msg = update.message
        if not msg or not msg.text:
            return

        raw_text = msg.text.strip()
        local_override = self._extract_local_chat_override(raw_text)
        text = local_override if local_override is not None else raw_text

        if not self._onboarding.is_complete():
            reply, _ = await self._onboarding.handle(text)
            await msg.reply_text(reply)
            return

        if self._bridge and not self._is_owner_chat(msg.chat_id):
            handled = await self._bridge.handle_incoming(msg.chat_id, raw_text)
            if handled:
                return

        terminal = None if self._is_owner_chat(msg.chat_id) else self._paired_terminal_device(msg.chat_id)
        if terminal:
            await msg.chat.send_action("typing")
            try:
                reply = await self._agent.run_terminal_chat(
                    terminal["device_id"],
                    terminal.get("device_name") or terminal["device_id"],
                    text,
                )
            except RuntimeError as e:
                reply = f"I can't think right now: {e}"
            except Exception as e:
                reply = "Something went wrong. Try again."
                logger.exception(e)
            await msg.reply_text(reply)
            return

        if self._should_route_to_active_peer(msg.chat_id, raw_text):
            _active, _device_id, device_name = self._peer_thread_state()
            await msg.chat.send_action("typing")
            try:
                reply = await self._agent.run(f"Ask {device_name} {text}")
                self._set_active_peer_thread_mode("armed")
                reply = f"{reply}{self._peer_thread_footer()}"
            except RuntimeError as e:
                reply = f"I can't think right now: {e}"
            except Exception as e:
                reply = "Something went wrong. Try again."
                logger.exception(e)
            await msg.reply_text(reply)
            return

        await msg.chat.send_action("typing")
        try:
            if settings.telegram_stream_replies and self._agent.can_stream(text):
                await self._stream_reply(msg, text)
                return
            reply = await self._agent.run(text)
        except RuntimeError as e:
            reply = f"I can't think right now: {e}"
        except Exception as e:
            reply = "Something went wrong. Try again."
            logger.exception(e)

        if len(reply) > 4000:
            chunks = [reply[i : i + 4000] for i in range(0, len(reply), 4000)]
            for chunk in chunks:
                await msg.reply_text(chunk)
        else:
            await msg.reply_text(reply)

    async def _stream_reply(self, msg, text: str) -> None:
        placeholder = await msg.reply_text("Thinking...")
        reply = ""
        last_sent = ""
        last_edit_at = 0.0
        editable = True

        try:
            async for chunk in self._agent.run_stream(text):
                if not chunk:
                    continue
                reply += chunk

                if len(reply) > 3900:
                    editable = False
                    continue

                now = time.monotonic()
                if not last_sent:
                    if await self._edit_placeholder_text(
                        placeholder, reply, last_sent=last_sent
                    ):
                        last_sent = reply
                        last_edit_at = now
                    continue
                if (
                    len(reply) - len(last_sent) < self.STREAM_EDIT_MIN_CHARS
                    and now - last_edit_at < self.STREAM_EDIT_INTERVAL_SECONDS
                ):
                    continue

                if await self._edit_placeholder_text(
                    placeholder, reply, last_sent=last_sent
                ):
                    last_sent = reply
                    last_edit_at = now
        except RuntimeError as e:
            reply = f"I can't think right now: {e}"
            editable = True
        except Exception as e:
            logger.exception(e)
            reply = "Something went wrong. Try again."
            editable = True

        if not reply:
            reply = "Something went wrong. Try again."

        if len(reply) > 4000:
            try:
                await placeholder.delete()
            except Exception:
                pass
            chunks = [reply[i : i + 4000] for i in range(0, len(reply), 4000)]
            for chunk in chunks:
                await msg.reply_text(chunk)
            return

        if editable and reply == last_sent:
            return

        if editable and reply != last_sent:
            try:
                if await self._edit_placeholder_text(
                    placeholder, reply, last_sent=last_sent
                ):
                    return
            except Exception as e:
                logger.warning(f"Telegram stream final edit failed: {e}")

        if not editable:
            try:
                if await self._edit_placeholder_text(
                    placeholder, reply, last_sent=last_sent
                ):
                    return
            except Exception as e:
                logger.warning(f"Telegram stream fallback edit failed: {e}")

        await msg.reply_text(reply)

    async def _on_callback(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if not query:
            return
        await query.answer()
        data = query.data or ""

        if data.startswith("approve:") or data.startswith("decline:"):
            action, action_id = data.split(":", 1)
            approved = action == "approve"
            resolved = self._safety.resolve_approval(
                action_id, approved, resolved_by="owner"
            )
            label = "Approved." if approved else "Cancelled."
            if not resolved:
                label = "This action already expired."
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(label)  # type: ignore[union-attr]

    async def _on_error(self, update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception(f"Telegram handler error: {ctx.error}")

    async def _notify(self, message: str) -> None:
        if not self._chat_id:
            logger.warning("No chat_id — notification dropped")
            return
        if not self._telegram_available:
            logger.warning("Telegram unavailable — notification dropped")
            return
        if message.startswith("approval_request:"):
            _, action_id, description = message.split(":", 2)
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Approve", callback_data=f"approve:{action_id}"
                        ),
                        InlineKeyboardButton(
                            "Cancel", callback_data=f"decline:{action_id}"
                        ),
                    ]
                ]
            )
            try:
                await self._app.bot.send_message(
                    chat_id=self._chat_id,
                    text=(
                        "🛡 *Approval Needed*\n\n"
                        f"*Action:* {description}\n"
                        f"*Approval ID:* `{action_id}`\n\n"
                        "Choose an action below."
                    ),
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception as e:
                self._log_notify_failure(e)
        else:
            try:
                await self._app.bot.send_message(
                    chat_id=self._chat_id,
                    text=message,
                )
            except Exception as e:
                self._log_notify_failure(e)

    def _log_notify_failure(self, error: Exception) -> None:
        key = f"telegram_notify:{type(error).__name__}:{str(error)[:80]}"
        message = f"Telegram notify failed: {error}"
        if self._notify_log_limiter.should_log(key, 300):
            logger.warning(message)
        else:
            logger.debug(message)

    async def send_message(
        self, chat_id: int, text: str, parse_mode: str = "Markdown"
    ) -> None:
        if not self._telegram_available:
            logger.warning("Telegram unavailable — send_message dropped")
            return
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as e:
            logger.warning(f"Telegram send_message failed: {e}")

    async def send_message_with_keyboard(
        self,
        text: str,
        keyboard: list[list[dict]],
        *,
        chat_id: int | None = None,
        parse_mode: str = "Markdown",
    ) -> None:
        target_chat_id = chat_id or self._chat_id
        if not target_chat_id:
            logger.warning("No chat_id — keyboard message dropped")
            return
        if not self._telegram_available:
            logger.warning("Telegram unavailable — keyboard message dropped")
            return

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        button["text"], callback_data=button["callback_data"]
                    )
                    for button in row
                ]
                for row in keyboard
            ]
        )
        try:
            await self._app.bot.send_message(
                chat_id=target_chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=markup,
            )
        except Exception as e:
            logger.warning(f"Telegram send_message_with_keyboard failed: {e}")

    async def start(self) -> None:
        if self._app is None:
            self._telegram_available = False
            logger.info("Telegram not configured — mobile interface disabled")
            return
        try:
            await self._app.bot.set_my_commands(
                [
                    BotCommand("start", "Introduction and setup"),
                    BotCommand("pair", "Pair a sovereign terminal"),
                    BotCommand("peer", "Ask a mesh peer from the owner chat"),
                    BotCommand("askpeer", "Ask a mesh peer"),
                    BotCommand("replypeer", "Continue the active peer thread"),
                    BotCommand("peerthread", "Show the active peer thread"),
                    BotCommand("donepeer", "Exit automatic peer thread mode"),
                    BotCommand("reset", "Fresh conversation"),
                    BotCommand("status", "System or device status"),
                    BotCommand("memory", "What I know about you"),
                    BotCommand("stop", "Emergency stop"),
                    BotCommand("resume", "Resume after stop"),
                ]
            )
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(  # type: ignore[union-attr]
                drop_pending_updates=True
            )
            self._telegram_available = True
            logger.info("Telegram interface online")
        except Exception as e:
            self._telegram_available = False
            logger.warning(
                f"Telegram unavailable — continuing without Telegram interface: {e}"
            )

    async def stop(self) -> None:
        if not self._telegram_available or self._app is None:
            return
        try:
            updater = getattr(self._app, "updater", None)
            if updater is not None:
                await asyncio.wait_for(
                    updater.stop(),
                    timeout=self.SHUTDOWN_TIMEOUT_SECONDS,
                )
            await asyncio.wait_for(
                self._app.stop(),
                timeout=self.SHUTDOWN_TIMEOUT_SECONDS,
            )
            await asyncio.wait_for(
                self._app.shutdown(),
                timeout=self.SHUTDOWN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Telegram shutdown timed out — forcing process exit")
        finally:
            self._telegram_available = False
