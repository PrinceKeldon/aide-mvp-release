"""
AIDE Identity — Device Manifest
Injects live device context into AIDE's system prompt at reasoning time.

The core insight: the LLM must know "Midas = Android" BEFORE it decides
whether to call a tool. If the device manifest is only available inside
the tool, the LLM may reason incorrectly before the tool is ever invoked.

This module builds a device manifest string that is injected into the
system prompt on every conversation turn when devices are registered.
"""
from __future__ import annotations
from loguru import logger


class DeviceManifest:
    """
    Builds a real-time device context string for injection into AIDE's system prompt.

    Example output:
        OWNER MESH DEVICES (canonical identities — use these for routing):
        • Vera Desk [mac_primary] — FULL NODE, PRIMARY RUNTIME
          Aliases: topd, mac, macbook, desk
        • Midas [device_4c7844a1709d] — PROXIED TERMINAL, online via Telegram
          Aliases: midas, android, phone, mobile

        ROUTING RULE: When the user mentions any device name or alias above,
        you MUST call the route_to_device tool with that exact target string.
        Never infer the target. Never default to Vera Desk unless explicitly requested.
        If the target is ambiguous, ask for clarification — never substitute.
    """

    def __init__(self, alias_registry, device_registry):
        self._aliases  = alias_registry
        self._registry = device_registry

    def build(self) -> str:
        """Build the device manifest string for system prompt injection."""
        try:
            aliases  = self._aliases.list_all()
            devices  = {d["device_id"]: d for d in self._registry.list_all()}
        except Exception as e:
            logger.warning(f"DeviceManifest: could not build manifest: {e}")
            return ""

        if not aliases:
            return ""

        lines = [
            "\nOWNER MESH DEVICES (use these for routing — never guess):"
        ]

        for a in aliases:
            did    = a["device_id"]
            device = devices.get(did, {})
            dtype  = device.get("device_type", "unknown")
            transport = device.get("transport_type")
            last_seen = device.get("last_seen_at")

            if a["is_default"]:
                role = f"{dtype.upper()}, PRIMARY RUNTIME (self)"
            else:
                if transport:
                    seen = "seen" if last_seen else "registered"
                    role = f"{dtype.upper()} — {seen} via {transport}"
                else:
                    role = f"{dtype.upper()} — trusted identity, no transport bound"

            # Only show meaningful aliases (not duplicates of canonical name)
            meaningful_aliases = [
                al for al in a["aliases"]
                if al.lower() != a["canonical_name"].lower()
            ][:5]

            line = f"• {a['canonical_name']} [device_id: {did[:16]}...] — {role}"
            if meaningful_aliases:
                line += f"\n  Aliases: {', '.join(meaningful_aliases)}"
            lines.append(line)

        lines.append(
            "\nROUTING RULES (non-negotiable):\n"
            "1. When the user mentions any device name or alias above, "
            "call route_to_device with the exact target string the user used.\n"
            "2. Never infer or substitute a different device.\n"
            "3. Never default to Vera Desk / Mac unless the user explicitly says so.\n"
            "4. If a device target is ambiguous, ask — do not guess.\n"
            "5. If you are unsure which device is meant, call list_mesh_devices first."
        )

        return "\n".join(lines)

    def get_explicit_targets(self) -> set[str]:
        """
        Return all known device names and aliases as a flat set.
        Used by the intent classifier to detect explicit device targets.
        """
        targets = set()
        try:
            for entry in self._aliases.list_all():
                targets.add(entry["canonical_name"].lower())
                for alias in entry["aliases"]:
                    targets.add(alias.lower())
        except Exception:
            pass
        return targets
