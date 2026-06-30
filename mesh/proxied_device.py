"""
AIDE Mesh — Proxied Device Model
A proxied device-agent is a first-class device identity in the owner mesh
whose transport is handled by an external bridge (e.g. Telegram) instead
of a local runtime.

Identity belongs to the device.
Transport is just plumbing.

This model sits between:
  today:  one Mac runtime, Android is a proxied endpoint
  later:  Android runs its own local AIDE node

The identity and trust model stay identical in both cases.
Only the transport field changes on upgrade.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TransportType(str, Enum):
    TELEGRAM  = "telegram"   # proxied via Telegram bot (current)
    MESH      = "mesh"       # direct local mesh WebSocket (future)
    LOCAL     = "local"      # same-process (for tests)


class DeviceRole(str, Enum):
    FULL_NODE           = "full_node"           # runs Ollama, full agent
    PROXIED_TERMINAL    = "proxied_terminal"    # approval + brief only, no local runtime
    SOVEREIGN_TERMINAL  = "sovereign_terminal"  # future: offline BB10-style


@dataclass
class TransportBinding:
    """How to reach this device."""
    transport_type: TransportType
    # Telegram
    telegram_chat_id: Optional[int] = None
    telegram_username: Optional[str] = None
    # Mesh (future)
    mesh_host: Optional[str] = None
    mesh_port: Optional[int] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict) -> "TransportBinding":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def telegram(cls, chat_id: int, username: str = None) -> "TransportBinding":
        return cls(
            transport_type=TransportType.TELEGRAM,
            telegram_chat_id=chat_id,
            telegram_username=username,
        )


@dataclass
class DeviceCapabilities:
    """What this device is allowed to do in the owner mesh."""
    can_approve:                bool = True
    can_receive_brief:          bool = True
    can_receive_execution_updates: bool = True
    can_execute:                bool = False   # no local runtime
    can_receive_memory:         bool = False
    can_receive_context:        str  = "summary_only"  # none | summary_only | full

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def for_proxied_terminal(cls) -> "DeviceCapabilities":
        """Default for Android-via-Telegram: approve, brief, updates. No execution."""
        return cls(
            can_approve=True,
            can_receive_brief=True,
            can_receive_execution_updates=True,
            can_execute=False,
            can_receive_memory=False,
            can_receive_context="summary_only",
        )

    @classmethod
    def for_full_node(cls) -> "DeviceCapabilities":
        return cls(
            can_approve=True,
            can_receive_brief=True,
            can_receive_execution_updates=True,
            can_execute=True,
            can_receive_memory=True,
            can_receive_context="full",
        )


@dataclass
class ProxiedDevice:
    """
    A device-agent in the owner mesh.
    May be local (full node) or proxied (terminal via Telegram).
    """
    device_id:         str
    device_name:       str
    role:              DeviceRole
    relationship_type: str                  # always "owner_device" for owner mesh
    transport:         TransportBinding
    capabilities:      DeviceCapabilities
    created_at:        str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_seen_at:      Optional[str] = None
    online:            bool = False

    def to_dict(self) -> dict:
        return {
            "device_id":         self.device_id,
            "device_name":       self.device_name,
            "role":              self.role.value,
            "relationship_type": self.relationship_type,
            "transport":         self.transport.to_dict(),
            "capabilities":      self.capabilities.to_dict(),
            "created_at":        self.created_at,
            "last_seen_at":      self.last_seen_at,
            "online":            self.online,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProxiedDevice":
        return cls(
            device_id         = d["device_id"],
            device_name       = d["device_name"],
            role              = DeviceRole(d["role"]),
            relationship_type = d["relationship_type"],
            transport         = TransportBinding.from_dict(d["transport"]),
            capabilities      = DeviceCapabilities(**d["capabilities"]),
            created_at        = d.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_seen_at      = d.get("last_seen_at"),
            online            = d.get("online", False),
        )

    @classmethod
    def create_proxied_terminal(
        cls,
        device_name: str,
        transport: TransportBinding,
        device_id: str = None,
    ) -> "ProxiedDevice":
        """Create a new proxied terminal device (e.g. Android via Telegram)."""
        import uuid
        return cls(
            device_id         = device_id or f"proxied_{uuid.uuid4().hex[:12]}",
            device_name       = device_name,
            role              = DeviceRole.PROXIED_TERMINAL,
            relationship_type = "owner_device",
            transport         = transport,
            capabilities      = DeviceCapabilities.for_proxied_terminal(),
        )