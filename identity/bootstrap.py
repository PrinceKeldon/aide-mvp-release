"""
AIDE identity bootstrap helpers.
"""
import json

from mesh.identity import DeviceIdentity


def ensure_local_device_registered(identity: DeviceIdentity, device_registry, alias_registry) -> str:
    """
    Materialize the local runtime as a canonical device record before aliases exist.
    """
    device_registry.create_device(
        device_id=identity.device_id,
        device_name=identity.device_name,
        device_type=identity.device_type.value,
        public_key=identity.public_key.hex(),
    )
    alias_registry.register(
        device_id=identity.device_id,
        canonical_name="AIDE Desk",
        aliases=[
            "topd",
            "mac",
            "macbook",
            "desk",
            "aide desk",
            "computer",
        ],
        is_default=True,
    )
    _normalize_local_alias_conflicts(identity.device_id, device_registry, alias_registry)
    return identity.device_id


def _normalize_local_alias_conflicts(local_device_id: str, device_registry, alias_registry) -> None:
    reserved = {"topd", "mac", "macbook", "desk", "aide desk", "computer"}
    entries = alias_registry.list_all()

    with alias_registry._conn() as conn:
        for entry in entries:
            device_id = entry["device_id"]
            if device_id == local_device_id:
                continue

            canonical_name = entry["canonical_name"]
            filtered_aliases = [
                alias for alias in entry["aliases"] if alias.lower() not in reserved
            ]

            if canonical_name.lower() in reserved:
                record = device_registry.get_by_device_id(device_id) or {}
                canonical_name = record.get("device_name") or canonical_name

            conn.execute(
                """
                UPDATE device_aliases
                SET canonical_name = ?, aliases = ?, is_default = 0
                WHERE device_id = ?
                """,
                (canonical_name, json.dumps(filtered_aliases), device_id),
            )
