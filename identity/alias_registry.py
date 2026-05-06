"""
AIDE Identity — Alias Registry
Maps human-friendly names and aliases to canonical device_ids.

This is a resolution layer, not a transport layer.
Completely separate from trust records, transport bindings, and chat IDs.

The rule: natural language may be fuzzy. Routing must never be.
Only device_id may be passed to the router. Never a nickname.
"""
import json
import sqlite3
from pathlib import Path
from loguru import logger
from typing import Optional


class AliasRegistry:
    """
    Stores canonical names and aliases for every device in the owner mesh.

    Schema per device:
      device_id       — the one true routing key (FK to device_registry)
      canonical_name  — human display name (e.g. "Midas", "AIDE Desk")
      aliases         — JSON list of alternative names the user may say
      is_default      — True for the primary local runtime (Mac)

    Resolution is always: phrase → device_id
    Routing always uses: device_id
    """

    def __init__(self, db_path: Path = None):
        if db_path is None:
            db_path = Path.home() / ".aide" / "data" / "device_registry.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_aliases (
                    device_id      TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    aliases        TEXT NOT NULL DEFAULT '[]',
                    is_default     INTEGER NOT NULL DEFAULT 0
                )
            """)
        logger.debug("Alias registry ready")

    # ── Registration ───────────────────────────────────────────

    def register(
        self,
        device_id: str,
        canonical_name: str,
        aliases: list[str] = None,
        is_default: bool = False,
    ):
        """
        Register or update a device's canonical name and aliases.
        Call this after pairing a new device.
        """
        if not self._device_exists(device_id):
            raise ValueError(
                f"Cannot register aliases for unknown device {device_id}. "
                "Create the device identity first."
            )

        all_aliases = sorted(
            {
                canonical_name.lower(),
                *[
                    a.lower().strip()
                    for a in (aliases or [])
                    if str(a).strip()
                ],
            }
        )
        with self._conn() as conn:
            if is_default:
                conn.execute("UPDATE device_aliases SET is_default = 0 WHERE is_default = 1")
            conn.execute("""
                INSERT INTO device_aliases (device_id, canonical_name, aliases, is_default)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    aliases        = excluded.aliases,
                    is_default     = excluded.is_default
            """, (device_id, canonical_name, json.dumps(all_aliases), int(is_default)))

        logger.info(f"Alias registered: {canonical_name!r} → {device_id} (aliases: {all_aliases})")

    def add_alias(self, device_id: str, new_alias: str):
        """Add an alias to an existing device without changing its canonical name."""
        row = self._get_row(device_id)
        if not row:
            raise ValueError(f"Device {device_id} not in alias registry")
        aliases = json.loads(row["aliases"])
        alias_normalized = new_alias.lower().strip()
        if alias_normalized not in aliases:
            aliases.append(alias_normalized)
            with self._conn() as conn:
                conn.execute(
                    "UPDATE device_aliases SET aliases = ? WHERE device_id = ?",
                    (json.dumps(aliases), device_id)
                )
            logger.info(f"Alias added: {new_alias!r} → {device_id}")

    def rename(self, device_id: str, new_canonical_name: str):
        """
        Rename a device. Old name becomes an alias automatically.
        Identity (device_id) is never changed.
        """
        row = self._get_row(device_id)
        if not row:
            raise ValueError(f"Device {device_id} not in alias registry")

        old_name = row["canonical_name"]
        aliases  = json.loads(row["aliases"])

        # Old name becomes alias
        if old_name.lower() not in aliases:
            aliases.append(old_name.lower())
        # New name added to aliases
        if new_canonical_name.lower() not in aliases:
            aliases.append(new_canonical_name.lower())

        with self._conn() as conn:
            conn.execute(
                "UPDATE device_aliases SET canonical_name = ?, aliases = ? WHERE device_id = ?",
                (new_canonical_name, json.dumps(aliases), device_id)
            )
        logger.info(f"Device renamed: {old_name!r} → {new_canonical_name!r} ({device_id})")

    def unregister(self, device_id: str):
        """Remove a device's alias record completely."""
        with self._conn() as conn:
            conn.execute("DELETE FROM device_aliases WHERE device_id = ?", (device_id,))
        logger.info(f"Alias unregistered for {device_id}")

    # ── Lookup ─────────────────────────────────────────────────

    def resolve(self, phrase: str) -> Optional[str]:
        """
        Resolve a human phrase to exactly one device_id.
        Returns device_id if exactly one match found.
        Returns None if no match or ambiguous.

        Callers must check the return value — never assume.
        """
        normalized = self._normalize(phrase)

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT device_id, canonical_name, aliases FROM device_aliases"
            ).fetchall()

        matches = []
        for row in rows:
            if self._phrase_matches(normalized, row):
                matches.append(row["device_id"])

        if len(matches) == 1:
            return matches[0]
        return None   # 0 = no match, 2+ = ambiguous — caller must handle both

    def resolve_with_confidence(self, phrase: str) -> dict:
        """
        Full resolution result with confidence metadata.
        Used by the resolution guard before routing.
        """
        normalized = self._normalize(phrase)

        with self._conn() as conn:
            rows = conn.execute(
                "SELECT device_id, canonical_name, aliases, is_default "
                "FROM device_aliases"
            ).fetchall()

        exact_matches = []
        fuzzy_matches = []

        for row in rows:
            if self._phrase_matches(normalized, row):
                exact_matches.append({
                    "device_id":      row["device_id"],
                    "canonical_name": row["canonical_name"],
                    "is_default":     bool(row["is_default"]),
                })
            elif self._fuzzy_match(normalized, row):
                fuzzy_matches.append({
                    "device_id":      row["device_id"],
                    "canonical_name": row["canonical_name"],
                    "is_default":     bool(row["is_default"]),
                })

        if len(exact_matches) == 1:
            return {
                "status":       "resolved",
                "device_id":    exact_matches[0]["device_id"],
                "canonical":    exact_matches[0]["canonical_name"],
                "phrase":       phrase,
                "confidence":   "exact",
            }
        elif len(exact_matches) > 1:
            return {
                "status":       "ambiguous",
                "candidates":   exact_matches,
                "phrase":       phrase,
                "confidence":   "low",
            }
        elif fuzzy_matches:
            return {
                "status":       "fuzzy",
                "candidates":   fuzzy_matches,
                "phrase":       phrase,
                "confidence":   "low",
            }
        else:
            return {
                "status":       "not_found",
                "phrase":       phrase,
                "confidence":   "none",
            }

    def get_default(self) -> Optional[str]:
        """Return the device_id of the default local node (Mac)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT device_id FROM device_aliases WHERE is_default = 1"
            ).fetchone()
        return row["device_id"] if row else None

    def get_canonical_name(self, device_id: str) -> Optional[str]:
        row = self._get_row(device_id)
        return row["canonical_name"] if row else None

    def get_explicit_targets(self) -> set[str]:
        targets = set()
        for entry in self.list_all():
            targets.add(entry["canonical_name"].lower())
            for alias in entry["aliases"]:
                targets.add(alias.lower())
        return targets

    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT device_id, canonical_name, aliases, is_default "
                "FROM device_aliases"
            ).fetchall()
        return [
            {
                "device_id":      r["device_id"],
                "canonical_name": r["canonical_name"],
                "aliases":        json.loads(r["aliases"]),
                "is_default":     bool(r["is_default"]),
            }
            for r in rows
        ]

    # ── Internal helpers ───────────────────────────────────────

    def _get_row(self, device_id: str):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM device_aliases WHERE device_id = ?", (device_id,)
            ).fetchone()

    def _device_exists(self, device_id: str) -> bool:
        with self._conn() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'device_identities'"
            ).fetchone()
            if not table:
                return False
            row = conn.execute(
                "SELECT 1 FROM device_identities WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _normalize(phrase: str) -> str:
        import re
        return re.sub(r"[^\w\s]", "", phrase.lower()).strip()

    def _phrase_matches(self, normalized: str, row) -> bool:
        aliases = json.loads(row["aliases"])
        return (
            normalized == self._normalize(row["canonical_name"])
            or normalized in [self._normalize(a) for a in aliases]
        )

    def _fuzzy_match(self, normalized: str, row) -> bool:
        """Substring match for suggestions only — never used for routing."""
        canonical = self._normalize(row["canonical_name"])
        aliases   = [self._normalize(a) for a in json.loads(row["aliases"])]
        targets   = [canonical] + aliases
        return any(
            normalized in t or t in normalized
            for t in targets
            if len(t) > 2 and len(normalized) > 2
        )
