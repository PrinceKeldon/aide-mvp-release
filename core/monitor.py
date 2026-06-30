import asyncio
import logging
import random
from typing import Any, Callable, Dict, Optional
from datetime import datetime

logger = logging.getLogger("aide.monitor")


class DeviceMonitor:
    """
    Sprint 7: System-Level Hooks.
    Monitors device state in real-time and triggers proactive actions.
    """

    def __init__(
        self,
        device_id: str,
        on_state_change: Callable[[str, Any], asyncio.Future] = None,
    ):
        self._device_id = device_id
        self._on_state_change = on_state_change
        self._states: Dict[str, Any] = {
            "battery_level": 100,
            "cpu_load": 10,
            "connectivity": "wifi",
            "memory_pressure": "low",
        }
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start monitoring device state."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(f"Device monitor started for {self._device_id}")

    async def stop(self):
        """Stop monitoring and cancel background task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Device monitor stopped for {self._device_id}")

    def _sample_system_state(self) -> Dict[str, Any]:
        """Mock system state sampling."""
        # Simulate some random changes
        return {
            "battery_level": random.randint(0, 100)
            if random.random() > 0.9
            else self._states["battery_level"],
            "cpu_load": random.randint(0, 100)
            if random.random() > 0.9
            else self._states["cpu_load"],
            "connectivity": random.choice(["wifi", "cellular", "none"])
            if random.random() > 0.95
            else self._states["connectivity"],
            "memory_pressure": random.choice(["low", "medium", "high"])
            if random.random() > 0.95
            else self._states["memory_pressure"],
        }

    async def _monitor_loop(self):
        while self._running:
            # In a real implementation, these would be system calls
            new_states = self._sample_system_state()

            for key, value in new_states.items():
                if self._states.get(key) != value:
                    logger.info(
                        f"State change detected: {key} {self._states.get(key)} -> {value}"
                    )
                    self._states[key] = value
                    if self._on_state_change:
                        await self._on_state_change(key, value)

            await asyncio.sleep(30)  # Check every 30 seconds

    def get_state(self, key: str) -> Any:
        return self._states.get(key)

    def get_all_states(self) -> Dict[str, Any]:
        return self._states.copy()

    def get_state(self, key: str) -> Any:
        return self._states.get(key)

    def get_all_states(self) -> Dict[str, Any]:
        return self._states.copy()
