"""
AIDE -- Offline mode manager (Sprint 2 Option B)
AIDE detects internet loss and switches gracefully.

When offline:
- All reasoning routes to Ollama/mistral:7b
- Internet-dependent tasks are queued
- User is notified once
- No repeated error messages

When back online:
- Queued tasks execute automatically
- User notified of completion
- Normal routing resumes

Enhanced with:
- Network error detection from providers
- Automatic fallback on cloud provider failures
- Pre-flight Ollama availability checks
"""
import asyncio
import json
from datetime import datetime
from loguru import logger
from memory.manager import MemoryManager
from core.network_utils import is_network_error, NetworkError
from core.settings import settings


class OfflineManager:

    CHECK_INTERVAL = 30   # seconds between connectivity checks
    TEST_URL       = "https://1.1.1.1"

    def __init__(
        self,
        memory: MemoryManager,
        notify_callback=None,
    ) -> None:
        self._memory       = memory
        self._notify       = notify_callback
        self._online       = True
        self._was_online   = True
        self._queue: list[dict] = self._load_queue()
        self._agent        = None   # wired after agent created
        self._monitor_task = None

    # ── Connectivity ─────────────────────────────────────────────

    async def check_internet(self) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                await client.get(self.TEST_URL)
            return True
        except Exception:
            return False

    async def start_monitor(self) -> None:
        """Background task — checks connectivity every 30 seconds."""
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Offline monitor started")

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.CHECK_INTERVAL)
                now_online = await self.check_internet()

                if now_online and not self._online:
                    # Just came back online
                    self._online = True
                    logger.info("Internet restored — processing queued tasks")
                    await self._on_reconnect()

                elif not now_online and self._online:
                    # Just went offline
                    self._online = False
                    logger.warning("Internet lost — switching to offline mode")
                    await self._on_disconnect()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Offline monitor error: {e}")

    async def _on_disconnect(self) -> None:
        if self._notify:
            await self._notify(
                "No internet connection. I'll keep working offline "
                "and catch up when you're back online."
            )

    async def _on_reconnect(self) -> None:
        queued = self._queue.copy()
        if not queued:
            if self._notify:
                await self._notify("Back online.")
            return

        if self._notify:
            await self._notify(
                f"Back online. Processing {len(queued)} queued task(s)..."
            )

        completed = 0
        for item in queued:
            try:
                if self._agent:
                    reply = await self._agent.run(item["message"])
                    if self._notify:
                        await self._notify(
                            f"Queued task complete:\n{item['message'][:60]}...\n\n{reply[:200]}"
                        )
                    completed += 1
            except Exception as e:
                logger.error(f"Queued task failed: {e}")

        self._queue = []
        self._save_queue()

        if self._notify and completed > 0:
            await self._notify(f"Done. {completed} queued task(s) completed.")

    # ── Queue management ─────────────────────────────────────────

    def queue_task(self, message: str) -> None:
        """Queue a task to run when internet returns."""
        self._queue.append({
            "message":   message,
            "queued_at": datetime.utcnow().isoformat(),
        })
        self._save_queue()
        logger.info(f"Task queued for when online: {message[:60]}")

    def _save_queue(self) -> None:
        try:
            queue_path = settings.memory_db_path.parent / "offline_queue.json"
            with open(queue_path, "w") as f:
                json.dump(self._queue, f)
        except Exception as e:
            logger.warning(f"Could not save offline queue: {e}")

    def _load_queue(self) -> list:
        try:
            queue_path = settings.memory_db_path.parent / "offline_queue.json"
            with open(queue_path) as f:
                return json.load(f)
        except Exception:
            return []

    # ── Public interface ─────────────────────────────────────────

    def is_online(self) -> bool:
        return self._online

    def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()

    def mark_network_error(self, provider: str, error: Exception) -> None:
        """
        Called by router when it detects a network error from a cloud provider.
        Forces offline mode if applicable.
        """
        if not is_network_error(error):
            return
        
        if not self._online:
            # Already offline
            return
        
        logger.warning(f"Network error from {provider}: {error} — likely internet issue")
        # Mark as offline without waiting for monitor
        self._online = False
        if self._notify:
            # Will be caught by monitor loop, but trigger immediately
            logger.info("Switching to offline mode due to provider network error")
