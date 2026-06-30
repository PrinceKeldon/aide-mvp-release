import logging
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger("aide.state_manager")


@dataclass
class StateEntry:
    value: Any
    updated_at: str
    updated_by: str
    version: int = 1


class MeshStateManager:
    """
    Sprint 7: Shared Global State Manager.
    Synchronizes a unified state across the mesh, allowing AIDE to act as a single entity.
    """

    def __init__(self, device_id: str, coordinator=None):
        self._device_id = device_id
        self._coordinator = coordinator
        self._global_state: Dict[str, StateEntry] = {}
        self._lock = asyncio.Lock()

    async def set_state(self, key: str, value: Any, broadcast: bool = True):
        """Update a state value and optionally broadcast it to the mesh."""
        async with self._lock:
            current = self._global_state.get(key)
            version = (current.version + 1) if current else 1

            entry = StateEntry(
                value=value,
                updated_at=datetime.utcnow().isoformat(),
                updated_by=self._device_id,
                version=version,
            )
            self._global_state[key] = entry

            if broadcast and self._coordinator:
                logger.info(f"Broadcasting state update: {key}={value}")
                await self._coordinator.broadcast_state_update(key, entry)

    async def get_state(self, key: str) -> Optional[Any]:
        """Retrieve a value from the global state."""
        entry = self._global_state.get(key)
        return entry.value if entry else None

    async def update_from_peer(self, key: str, entry_dict: Dict[str, Any]):
        """Update local state based on a peer's update, using versioning for conflict resolution."""
        async with self._lock:
            current = self._global_state.get(key)
            new_version = entry_dict.get("version", 0)

            if not current or new_version > current.version:
                self._global_state[key] = StateEntry(**entry_dict)
                logger.debug(f"State updated from peer: {key} (v{new_version})")
            elif new_version == current.version:
                # Tie-break with timestamp or device_id
                if entry_dict.get("updated_at", "") > current.updated_at:
                    self._global_state[key] = StateEntry(**entry_dict)
            else:
                logger.debug(
                    f"Ignored stale state update for {key}: peer v{new_version} < local v{current.version}"
                )

    def get_full_state(self) -> Dict[str, Any]:
        """Returns a serializable version of the current global state."""
        return {k: vars(v) for k, v in self._global_state.items()}

    async def sync_full_state(self, full_state_dict: Dict[str, Any]):
        """Merge a full state snapshot from another device."""
        for key, entry_dict in full_state_dict.items():
            await self.update_from_peer(key, entry_dict)
