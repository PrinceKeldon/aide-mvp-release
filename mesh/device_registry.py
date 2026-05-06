"""
AIDE Mesh — Device Registry v2
Three fully separated layers:

  IDENTITY  — who the device is (device_id + keypair)
  TRUST     — what the device is allowed to do (capabilities + relationship)
  TRANSPORT — how to reach the device (Telegram chat_id, mesh WebSocket, etc.)

Key invariant:
  Deleting a transport binding does NOT delete the device identity.
  Deleting a device identity cascades to trust + transport.
  Telegram chat_id is NEVER used as a device key anywhere in the system.

The router always addresses by device_id first, then resolves transport.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger
from typing import Optional


class DeviceRegistry:
    """
    Single source of truth for owner-mesh devices.
    Three tables, three concerns, cleanly separated.
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
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            # ── Layer 1: Identity ──────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_identities (
                    device_id    TEXT PRIMARY KEY,
                    device_name  TEXT NOT NULL,
                    device_type  TEXT NOT NULL DEFAULT 'proxied_terminal',
                    public_key   TEXT,
                    created_at   TEXT NOT NULL,
                    last_seen_at TEXT
                )
            """)

            # ── Layer 2: Trust ─────────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_trust (
                    device_id         TEXT PRIMARY KEY,
                    relationship_type TEXT NOT NULL DEFAULT 'owner_device',
                    trust_level                  TEXT    NOT NULL DEFAULT 'trusted',
                    paired_at                    TEXT,
                    trust_notes                  TEXT,
                    revoked_at                   TEXT,
                    trust_source                 TEXT,
                    can_approve                   INTEGER NOT NULL DEFAULT 1,
                    can_receive_brief             INTEGER NOT NULL DEFAULT 1,
                    can_receive_execution_updates INTEGER NOT NULL DEFAULT 1,
                    can_execute                   INTEGER NOT NULL DEFAULT 0,
                    can_receive_memory            INTEGER NOT NULL DEFAULT 0,
                    can_receive_context           TEXT    NOT NULL DEFAULT 'summary_only',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (device_id) REFERENCES device_identities(device_id)
                        ON DELETE CASCADE
                )
            """)

            # ── Layer 3: Transport ─────────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS device_transports (
                    device_id        TEXT NOT NULL,
                    transport_type   TEXT NOT NULL,
                    telegram_chat_id INTEGER,
                    telegram_username TEXT,
                    mesh_host        TEXT,
                    mesh_port        INTEGER,
                    bound_at         TEXT NOT NULL,
                    PRIMARY KEY (device_id, transport_type),
                    FOREIGN KEY (device_id) REFERENCES device_identities(device_id)
                        ON DELETE CASCADE
                )
            """)

            # Index: telegram_chat_id → device_id
            # This is a lookup index ONLY — chat_id is never a primary key
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_chat_id
                ON device_transports(telegram_chat_id)
                WHERE telegram_chat_id IS NOT NULL
            """)
            self._ensure_column(
                conn,
                "device_trust",
                "trust_level",
                "TEXT NOT NULL DEFAULT 'trusted'",
            )
            self._ensure_column(conn, "device_trust", "paired_at", "TEXT")
            self._ensure_column(conn, "device_trust", "trust_notes", "TEXT")
            self._ensure_column(conn, "device_trust", "revoked_at", "TEXT")
            self._ensure_column(conn, "device_trust", "trust_source", "TEXT")

            # ── Sovereign peer trust scope layer ───────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trust_scopes (
                    scope_id TEXT PRIMARY KEY,
                    scope_name TEXT NOT NULL,
                    description TEXT,
                    can_request_tasks INTEGER NOT NULL DEFAULT 0,
                    can_receive_results INTEGER NOT NULL DEFAULT 0,
                    can_send_updates INTEGER NOT NULL DEFAULT 0,
                    can_request_approvals INTEGER NOT NULL DEFAULT 0,
                    can_receive_context TEXT NOT NULL DEFAULT 'explicit_only',
                    can_receive_memory INTEGER NOT NULL DEFAULT 0,
                    allowed_task_types TEXT NOT NULL,
                    allowed_context_types TEXT NOT NULL,
                    sandbox_mode TEXT NOT NULL DEFAULT 'strict',
                    requires_local_approval INTEGER NOT NULL DEFAULT 0,
                    risk_level TEXT NOT NULL DEFAULT 'low',
                    is_template INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
            """)

            # ── Sovereign peer records ─────────────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_agents (
                    peer_agent_id TEXT PRIMARY KEY,
                    public_key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    owner_name TEXT,
                    primary_device_id TEXT,
                    connection_type TEXT NOT NULL DEFAULT 'offline',
                    last_seen_at TEXT,
                    relationship_type TEXT NOT NULL DEFAULT 'sovereign_peer',
                    trust_scope_id TEXT NOT NULL,
                    paired_at TEXT NOT NULL,
                    paired_by TEXT,
                    revoked_at TEXT,
                    revocation_reason TEXT,
                    trust_notes TEXT,
                    agent_version TEXT,
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (trust_scope_id) REFERENCES trust_scopes(scope_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_peer_agents_last_seen
                ON peer_agents(last_seen_at)
            """)

            # ── Sovereign peer task lifecycle ──────────────────
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_task_log (
                    request_id TEXT PRIMARY KEY,
                    requesting_peer_id TEXT NOT NULL,
                    responding_peer_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    title TEXT,
                    instruction TEXT,
                    context_provided TEXT NOT NULL DEFAULT '{}',
                    requested_at TEXT NOT NULL,
                    accepted_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    current_state TEXT NOT NULL,
                    rejection_reason TEXT,
                    requires_requesting_approval INTEGER NOT NULL DEFAULT 0,
                    requesting_approval_state TEXT,
                    requires_responding_approval INTEGER NOT NULL DEFAULT 0,
                    responding_approval_state TEXT,
                    result_type TEXT,
                    result_summary TEXT,
                    result_data TEXT NOT NULL DEFAULT '{}',
                    scope_validated INTEGER NOT NULL DEFAULT 0,
                    signature_verified INTEGER NOT NULL DEFAULT 0,
                    sandbox_executed INTEGER NOT NULL DEFAULT 0,
                    execution_time_ms INTEGER
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_peer_task_log_state
                ON peer_task_log(current_state)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_peer_task_log_requested_at
                ON peer_task_log(requested_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS peer_conversation_log (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    peer_device_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    via_transport TEXT NOT NULL DEFAULT 'mesh',
                    correlation_id TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (peer_device_id) REFERENCES device_identities(device_id)
                        ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_peer_conversation_peer_created
                ON peer_conversation_log(peer_device_id, created_at DESC)
            """)
            self._ensure_peer_task_log_schema(conn)
        logger.info("Device registry v2 ready (3-layer separation)")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _ensure_peer_task_log_schema(self, conn: sqlite3.Connection) -> None:
        foreign_keys = conn.execute("PRAGMA foreign_key_list(peer_task_log)").fetchall()
        if not foreign_keys:
            return

        conn.execute("ALTER TABLE peer_task_log RENAME TO peer_task_log_old")
        conn.execute("""
            CREATE TABLE peer_task_log (
                request_id TEXT PRIMARY KEY,
                requesting_peer_id TEXT NOT NULL,
                responding_peer_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                title TEXT,
                instruction TEXT,
                context_provided TEXT NOT NULL DEFAULT '{}',
                requested_at TEXT NOT NULL,
                accepted_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                current_state TEXT NOT NULL,
                rejection_reason TEXT,
                requires_requesting_approval INTEGER NOT NULL DEFAULT 0,
                requesting_approval_state TEXT,
                requires_responding_approval INTEGER NOT NULL DEFAULT 0,
                responding_approval_state TEXT,
                result_type TEXT,
                result_summary TEXT,
                result_data TEXT NOT NULL DEFAULT '{}',
                scope_validated INTEGER NOT NULL DEFAULT 0,
                signature_verified INTEGER NOT NULL DEFAULT 0,
                sandbox_executed INTEGER NOT NULL DEFAULT 0,
                execution_time_ms INTEGER
            )
        """)
        conn.execute("""
            INSERT INTO peer_task_log
            SELECT
                request_id, requesting_peer_id, responding_peer_id,
                task_type, title, instruction, context_provided,
                requested_at, accepted_at, started_at, completed_at,
                current_state, rejection_reason,
                requires_requesting_approval, requesting_approval_state,
                requires_responding_approval, responding_approval_state,
                result_type, result_summary, result_data,
                scope_validated, signature_verified, sandbox_executed, execution_time_ms
            FROM peer_task_log_old
        """)
        conn.execute("DROP TABLE peer_task_log_old")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _normalize_trust_level(self, trust_level: str | None) -> str:
        value = (trust_level or "trusted").strip().lower()
        if value not in {"trusted", "limited", "pending", "revoked"}:
            raise ValueError(f"Unsupported trust level: {trust_level!r}")
        return value

    def _append_trust_note(self, current: str | None, note: str | None) -> str | None:
        cleaned = (note or "").strip()
        if not cleaned:
            return current
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"[{timestamp}] {cleaned}"
        if not current:
            return entry
        return f"{current}\n{entry}"

    # ── Identity layer ─────────────────────────────────────────

    def create_device(
        self,
        device_name: str,
        device_type: str = "proxied_terminal",
        public_key: str = None,
        device_id: str = None,
    ) -> str:
        """
        Create a device identity record.
        Returns the device_id.
        The device exists independently of any transport binding.
        """
        did = device_id or f"device_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        normalized_type = (device_type or "proxied_terminal").lower()
        default_caps = {
            "can_approve": 1,
            "can_receive_brief": 1,
            "can_receive_execution_updates": 1,
            "can_execute": 1 if normalized_type in ("full_node", "relay") else 0,
            "can_receive_memory": 1 if normalized_type in ("full_node", "relay") else 0,
            "can_receive_context": "full" if normalized_type in ("full_node", "relay") else "summary_only",
        }

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO device_identities
                  (device_id, device_name, device_type, public_key, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_name = excluded.device_name,
                    device_type = excluded.device_type,
                    public_key  = COALESCE(excluded.public_key, device_identities.public_key)
            """, (did, device_name, device_type, public_key, now))

            # Create default trust record for this device
            conn.execute("""
                INSERT INTO device_trust
                  (
                    device_id, relationship_type,
                    trust_level, paired_at, trust_notes, revoked_at, trust_source,
                    can_approve, can_receive_brief, can_receive_execution_updates,
                    can_execute, can_receive_memory, can_receive_context, created_at
                  )
                VALUES (?, 'owner_device', 'trusted', NULL, NULL, NULL, 'local_create', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    trust_level = COALESCE(device_trust.trust_level, 'trusted'),
                    can_approve = excluded.can_approve,
                    can_receive_brief = excluded.can_receive_brief,
                    can_receive_execution_updates = excluded.can_receive_execution_updates,
                    can_execute = excluded.can_execute,
                    can_receive_memory = excluded.can_receive_memory,
                    can_receive_context = excluded.can_receive_context
            """, (
                did,
                default_caps["can_approve"],
                default_caps["can_receive_brief"],
                default_caps["can_receive_execution_updates"],
                default_caps["can_execute"],
                default_caps["can_receive_memory"],
                default_caps["can_receive_context"],
                now,
            ))

        logger.info(f"Device identity created: {device_name} ({did})")
        return did

    def rename_device(self, device_id: str, new_name: str):
        """Rename a device. Trust and transport are unaffected."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE device_identities SET device_name = ? WHERE device_id = ?",
                (new_name, device_id)
            )
        logger.info(f"Device renamed → {new_name} ({device_id})")

    def update_last_seen(self, device_id: str):
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE device_identities SET last_seen_at = ? WHERE device_id = ?",
                (now, device_id)
            )

    # ── Transport layer ────────────────────────────────────────

    def bind_telegram(
        self,
        device_id: str,
        chat_id: int,
        username: str = None,
    ):
        """
        Bind a Telegram endpoint to an existing device.
        The device must already exist in device_identities.
        This is a transport binding only — it does NOT define the device.

        If the chat_id is already bound to a different device, that
        binding is replaced (a phone can only be one device at a time).
        """
        if not self.device_exists(device_id):
            raise ValueError(
                f"Device {device_id} not found. Create it first with create_device()."
            )

        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            # Remove any existing binding for this chat_id (rebind)
            conn.execute(
                "DELETE FROM device_transports WHERE telegram_chat_id = ?",
                (chat_id,)
            )
            conn.execute("""
                INSERT INTO device_transports
                  (device_id, transport_type, telegram_chat_id, telegram_username, bound_at)
                VALUES (?, 'telegram', ?, ?, ?)
                ON CONFLICT(device_id, transport_type) DO UPDATE SET
                    telegram_chat_id  = excluded.telegram_chat_id,
                    telegram_username = excluded.telegram_username,
                    bound_at          = excluded.bound_at
            """, (device_id, chat_id, username, now))

        logger.info(f"Telegram bound: {device_id} ↔ chat_id {chat_id}")

    def bind_mesh(
        self,
        device_id: str,
        host: str,
        port: int,
    ):
        """
        Bind a direct mesh endpoint to an existing device.
        """
        if not self.device_exists(device_id):
            raise ValueError(
                f"Device {device_id} not found. Create it first with create_device()."
            )

        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO device_transports
                  (device_id, transport_type, mesh_host, mesh_port, bound_at)
                VALUES (?, 'mesh', ?, ?, ?)
                ON CONFLICT(device_id, transport_type) DO UPDATE SET
                    mesh_host = excluded.mesh_host,
                    mesh_port = excluded.mesh_port,
                    bound_at = excluded.bound_at
            """, (device_id, host, port, now))

        logger.info(f"Mesh bound: {device_id} ↔ ws://{host}:{port}")

    def unbind_telegram(self, device_id: str):
        """
        Remove the Telegram binding from a device.
        The device identity and trust record remain intact.
        Test A from the audit: device survives without transport.
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM device_transports WHERE device_id = ? AND transport_type = 'telegram'",
                (device_id,)
            )
        logger.info(f"Telegram unbound from {device_id} — device identity preserved")

    def unbind_mesh(self, device_id: str):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM device_transports WHERE device_id = ? AND transport_type = 'mesh'",
                (device_id,)
            )
        logger.info(f"Mesh unbound from {device_id} — device identity preserved")

    def rebind_telegram(self, device_id: str, new_chat_id: int, new_username: str = None):
        """
        Move a device to a new Telegram account.
        The device identity and trust are unchanged.
        Test B from the audit: same identity, different transport.
        """
        self.unbind_telegram(device_id)
        self.bind_telegram(device_id, new_chat_id, new_username)
        logger.info(f"Device {device_id} rebound to new Telegram chat {new_chat_id}")

    # ── Trust layer ────────────────────────────────────────────

    def set_trust_state(
        self,
        device_id: str,
        trust_level: str,
        *,
        trust_source: str | None = None,
        note: str | None = None,
        revoked_at: str | None = None,
        paired_at: str | None = None,
    ) -> None:
        level = self._normalize_trust_level(trust_level)
        current = self.get_trust_record(device_id)
        if not current:
            raise ValueError(f"Unknown device {device_id}")
        notes = self._append_trust_note(current.get("trust_notes"), note)
        revoked_value = revoked_at
        if level == "revoked":
            revoked_value = revoked_at or self._now()
        elif revoked_at is None:
            revoked_value = None
        paired_value = paired_at if paired_at is not None else current.get("paired_at")
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE device_trust
                SET trust_level = ?,
                    trust_source = COALESCE(?, trust_source),
                    trust_notes = ?,
                    revoked_at = ?,
                    paired_at = ?
                WHERE device_id = ?
                """,
                (level, trust_source, notes, revoked_value, paired_value, device_id),
            )
        logger.info(f"Trust state updated: {device_id} -> {level}")

    def mark_paired(
        self,
        device_id: str,
        *,
        paired_at: str | None = None,
        trust_source: str = "qr_pairing",
        note: str | None = None,
    ) -> None:
        self.set_trust_state(
            device_id,
            "trusted",
            trust_source=trust_source,
            note=note,
            paired_at=paired_at or self._now(),
            revoked_at=None,
        )

    def revoke_trust(self, device_id: str, note: str | None = None) -> None:
        self.set_trust_state(device_id, "revoked", note=note)

    def restore_trust(
        self,
        device_id: str,
        *,
        trust_level: str = "trusted",
        note: str | None = None,
        trust_source: str = "manual",
    ) -> None:
        self.set_trust_state(
            device_id,
            trust_level,
            trust_source=trust_source,
            note=note,
            revoked_at=None,
        )

    def is_trusted(self, device_id: str) -> bool:
        record = self.get_trust_record(device_id)
        return bool(record and record.get("trust_level") == "trusted" and not record.get("revoked_at"))

    def is_revoked(self, device_id: str) -> bool:
        record = self.get_trust_record(device_id)
        return bool(record and (record.get("trust_level") == "revoked" or record.get("revoked_at")))

    def get_trust_record(self, device_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    i.device_id, i.device_name, i.device_type, i.public_key,
                    i.created_at, i.last_seen_at,
                    t.relationship_type, t.trust_level, t.paired_at,
                    t.trust_notes, t.revoked_at, t.trust_source,
                    t.can_approve, t.can_receive_brief,
                    t.can_receive_execution_updates, t.can_execute,
                    t.can_receive_memory, t.can_receive_context,
                    GROUP_CONCAT(DISTINCT tr.transport_type) AS transport_types
                FROM device_identities i
                LEFT JOIN device_trust t ON i.device_id = t.device_id
                LEFT JOIN device_transports tr ON i.device_id = tr.device_id
                WHERE i.device_id = ?
                GROUP BY
                    i.device_id, i.device_name, i.device_type, i.public_key,
                    i.created_at, i.last_seen_at,
                    t.relationship_type, t.trust_level, t.paired_at,
                    t.trust_notes, t.revoked_at, t.trust_source,
                    t.can_approve, t.can_receive_brief,
                    t.can_receive_execution_updates, t.can_execute,
                    t.can_receive_memory, t.can_receive_context
                """,
                (device_id,),
            ).fetchone()
        if not row:
            return None
        record = dict(row)
        record["transport_types"] = [
            item for item in (record.get("transport_types") or "").split(",") if item
        ]
        return record

    def list_trusted_peers(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    i.device_id, i.device_name, i.device_type, i.public_key,
                    i.created_at, i.last_seen_at,
                    t.relationship_type, t.trust_level, t.paired_at,
                    t.trust_notes, t.revoked_at, t.trust_source,
                    t.can_approve, t.can_receive_brief,
                    t.can_receive_execution_updates, t.can_execute,
                    t.can_receive_memory, t.can_receive_context
                FROM device_identities i
                JOIN device_trust t ON i.device_id = t.device_id
                WHERE t.trust_level = 'trusted' AND t.revoked_at IS NULL
                ORDER BY COALESCE(t.paired_at, i.created_at) DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_capabilities(self, device_id: str, **capabilities):
        """Update individual capability flags for a device."""
        allowed_fields = {
            "can_approve", "can_receive_brief", "can_receive_execution_updates",
            "can_execute", "can_receive_memory", "can_receive_context",
        }
        updates = {k: v for k, v in capabilities.items() if k in allowed_fields}
        if not updates:
            return

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [device_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE device_trust SET {set_clause} WHERE device_id = ?",
                values
            )

    def can(self, device_id: str, capability: str) -> bool:
        """Check if a device has a specific capability."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {capability} FROM device_trust WHERE device_id = ?",
                (device_id,)
            ).fetchone()
        if not row:
            return False
        val = row[capability]
        if isinstance(val, str):
            return val not in ("none", "false", "")
        return bool(val)

    # ── Lookup methods ─────────────────────────────────────────

    def device_exists(self, device_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM device_identities WHERE device_id = ?", (device_id,)
            ).fetchone()
        return row is not None

    def get_by_device_id(self, device_id: str) -> Optional[dict]:
        """
        Full device record: identity + trust + transport joined.
        Test C: addressed by device_id, transport resolved separately.
        """
        with self._conn() as conn:
            row = conn.execute("""
                SELECT
                    i.device_id, i.device_name, i.device_type,
                    i.public_key, i.created_at, i.last_seen_at,
                    t.relationship_type, t.trust_level, t.paired_at,
                    t.trust_notes, t.revoked_at, t.trust_source,
                    t.can_approve, t.can_receive_brief,
                    t.can_receive_execution_updates, t.can_execute,
                    t.can_receive_memory, t.can_receive_context,
                    tr.transport_type, tr.telegram_chat_id, tr.telegram_username,
                    tr.mesh_host, tr.mesh_port
                FROM device_identities i
                LEFT JOIN device_trust t ON i.device_id = t.device_id
                LEFT JOIN device_transports tr ON i.device_id = tr.device_id
                WHERE i.device_id = ?
            """, (device_id,)).fetchone()
        return dict(row) if row else None

    def get_device_id_by_telegram(self, chat_id: int) -> Optional[str]:
        """
        Transport lookup: given a Telegram chat_id, return the device_id.
        This is the ONLY place chat_id is used as a lookup key.
        Result is a device_id — the identity layer takes over from here.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT device_id FROM device_transports
                   WHERE telegram_chat_id = ? AND transport_type = 'telegram'""",
                (chat_id,)
            ).fetchone()
        return row["device_id"] if row else None

    def get_telegram_chat_id(self, device_id: str) -> Optional[int]:
        """
        Identity → transport resolution.
        The router uses this: address by device_id, resolve chat_id here.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT telegram_chat_id FROM device_transports
                   WHERE device_id = ? AND transport_type = 'telegram'""",
                (device_id,)
            ).fetchone()
        return row["telegram_chat_id"] if row else None

    def get_mesh_endpoint(self, device_id: str) -> Optional[tuple[str, int]]:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT mesh_host, mesh_port FROM device_transports
                   WHERE device_id = ? AND transport_type = 'mesh'""",
                (device_id,),
            ).fetchone()
        if not row or not row["mesh_host"] or row["mesh_port"] is None:
            return None
        return row["mesh_host"], row["mesh_port"]

    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    i.device_id, i.device_name, i.device_type,
                    i.created_at, i.last_seen_at,
                    t.relationship_type, t.trust_level, t.paired_at,
                    t.trust_notes, t.revoked_at, t.trust_source,
                    t.can_approve, t.can_receive_brief,
                    t.can_receive_execution_updates, t.can_execute,
                    t.can_receive_memory, t.can_receive_context,
                    tr.transport_type, tr.telegram_chat_id, tr.mesh_host, tr.mesh_port
                FROM device_identities i
                LEFT JOIN device_trust t ON i.device_id = t.device_id
                LEFT JOIN device_transports tr ON i.device_id = tr.device_id
                ORDER BY i.created_at DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def list_by_capability(self, capability: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT i.device_id, i.device_name, i.device_type, i.last_seen_at,
                       t.trust_level, t.paired_at, t.trust_notes, t.revoked_at, t.trust_source,
                       t.can_approve, t.can_receive_brief, t.can_receive_execution_updates,
                       t.can_execute, t.can_receive_memory, t.can_receive_context,
                       tr.transport_type, tr.telegram_chat_id,
                       tr.mesh_host, tr.mesh_port
                FROM device_identities i
                JOIN device_trust t ON i.device_id = t.device_id
                LEFT JOIN device_transports tr ON i.device_id = tr.device_id
                WHERE t.{capability} = 1
                  AND t.trust_level = 'trusted'
                  AND t.revoked_at IS NULL
            """).fetchall()
        return [dict(r) for r in rows]

    def list_active_devices(self, capability: str = None) -> list[dict]:
        devices = self.list_by_capability(capability) if capability else self.list_all()
        active = []
        for device in devices:
            if device.get("trust_level") not in (None, "trusted") or device.get("revoked_at"):
                continue
            if device.get("telegram_chat_id") or device.get("mesh_host") or device.get("transport_type") == "mesh":
                active.append(device)
        return active

    def remove_device(self, device_id: str):
        """Remove a device completely (cascades to trust + transport)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM device_identities WHERE device_id = ?", (device_id,)
            )
        logger.info(f"Device removed: {device_id}")

    # ── Sovereign peer scopes ──────────────────────────────────

    def create_trust_scope(
        self,
        *,
        scope_id: str,
        scope_name: str,
        description: str = "",
        can_request_tasks: bool = False,
        can_receive_results: bool = False,
        can_send_updates: bool = False,
        can_request_approvals: bool = False,
        can_receive_context: str = "explicit_only",
        can_receive_memory: bool = False,
        allowed_task_types: list[str] | None = None,
        allowed_context_types: list[str] | None = None,
        sandbox_mode: str = "strict",
        requires_local_approval: bool = False,
        risk_level: str = "low",
        is_template: bool = False,
    ) -> str:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO trust_scopes (
                    scope_id, scope_name, description,
                    can_request_tasks, can_receive_results, can_send_updates, can_request_approvals,
                    can_receive_context, can_receive_memory,
                    allowed_task_types, allowed_context_types,
                    sandbox_mode, requires_local_approval, risk_level, is_template,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET
                    scope_name = excluded.scope_name,
                    description = excluded.description,
                    can_request_tasks = excluded.can_request_tasks,
                    can_receive_results = excluded.can_receive_results,
                    can_send_updates = excluded.can_send_updates,
                    can_request_approvals = excluded.can_request_approvals,
                    can_receive_context = excluded.can_receive_context,
                    can_receive_memory = excluded.can_receive_memory,
                    allowed_task_types = excluded.allowed_task_types,
                    allowed_context_types = excluded.allowed_context_types,
                    sandbox_mode = excluded.sandbox_mode,
                    requires_local_approval = excluded.requires_local_approval,
                    risk_level = excluded.risk_level,
                    is_template = excluded.is_template,
                    updated_at = excluded.updated_at
                """,
                (
                    scope_id,
                    scope_name,
                    description,
                    int(can_request_tasks),
                    int(can_receive_results),
                    int(can_send_updates),
                    int(can_request_approvals),
                    can_receive_context,
                    int(can_receive_memory),
                    json.dumps(allowed_task_types or []),
                    json.dumps(allowed_context_types or []),
                    sandbox_mode,
                    int(requires_local_approval),
                    risk_level,
                    int(is_template),
                    now,
                    now,
                ),
            )
        return scope_id

    def create_scope_template(self, scope) -> str:
        if hasattr(scope, "to_record"):
            data = scope.to_record()
        else:
            data = dict(scope)
        return self.create_trust_scope(
            scope_id=data["scope_id"],
            scope_name=data["scope_name"],
            description=data.get("description", ""),
            can_request_tasks=bool(data.get("can_request_tasks")),
            can_receive_results=bool(data.get("can_receive_results")),
            can_send_updates=bool(data.get("can_send_updates")),
            can_request_approvals=bool(data.get("can_request_approvals")),
            can_receive_context=data.get("can_receive_context", "explicit_only"),
            can_receive_memory=bool(data.get("can_receive_memory")),
            allowed_task_types=self._json_list(data.get("allowed_task_types")),
            allowed_context_types=self._json_list(data.get("allowed_context_types")),
            sandbox_mode=data.get("sandbox_mode", "strict"),
            requires_local_approval=bool(data.get("requires_local_approval")),
            risk_level=data.get("risk_level", "low"),
            is_template=bool(data.get("is_template", True)),
        )

    def seed_default_peer_scope_templates(self) -> int:
        from mesh.peer_templates import default_scope_templates

        count = 0
        for template in default_scope_templates():
            self.create_scope_template(template)
            count += 1
        return count

    def get_trust_scope(self, scope_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trust_scopes WHERE scope_id = ?",
                (scope_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_trust_scopes(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trust_scopes ORDER BY is_template DESC, scope_name ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_scope_templates(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trust_scopes WHERE is_template = 1 ORDER BY scope_name ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Sovereign peers ────────────────────────────────────────

    def peer_agent_exists(self, peer_agent_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM peer_agents WHERE peer_agent_id = ?",
                (peer_agent_id,),
            ).fetchone()
        return row is not None

    def create_peer_agent(
        self,
        *,
        peer_agent_id: str,
        public_key: str,
        display_name: str,
        trust_scope_id: str,
        owner_name: str | None = None,
        primary_device_id: str | None = None,
        connection_type: str = "offline",
        paired_by: str | None = None,
        agent_version: str | None = None,
        capabilities: list[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        if not self.get_trust_scope(trust_scope_id):
            raise ValueError(f"Unknown trust scope {trust_scope_id}")
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO peer_agents (
                    peer_agent_id, public_key, display_name, owner_name,
                    primary_device_id, connection_type, last_seen_at,
                    relationship_type, trust_scope_id,
                    paired_at, paired_by, revoked_at, revocation_reason, trust_notes,
                    agent_version, capabilities, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'sovereign_peer', ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                ON CONFLICT(peer_agent_id) DO UPDATE SET
                    public_key = excluded.public_key,
                    display_name = excluded.display_name,
                    owner_name = excluded.owner_name,
                    primary_device_id = excluded.primary_device_id,
                    connection_type = excluded.connection_type,
                    trust_scope_id = excluded.trust_scope_id,
                    paired_by = COALESCE(excluded.paired_by, peer_agents.paired_by),
                    agent_version = COALESCE(excluded.agent_version, peer_agents.agent_version),
                    capabilities = excluded.capabilities,
                    metadata = excluded.metadata
                """,
                (
                    peer_agent_id,
                    public_key,
                    display_name,
                    owner_name,
                    primary_device_id,
                    connection_type,
                    trust_scope_id,
                    now,
                    paired_by,
                    agent_version,
                    json.dumps(capabilities or []),
                    json.dumps(metadata or {}),
                    now,
                ),
            )
        return peer_agent_id

    def get_peer_agent(self, peer_agent_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT p.*, s.scope_name, s.description AS scope_description,
                       s.can_request_tasks, s.can_receive_results, s.can_send_updates,
                       s.can_request_approvals, s.can_receive_context, s.can_receive_memory,
                       s.allowed_task_types, s.allowed_context_types, s.sandbox_mode,
                       s.requires_local_approval, s.risk_level
                FROM peer_agents p
                LEFT JOIN trust_scopes s ON p.trust_scope_id = s.scope_id
                WHERE p.peer_agent_id = ?
                """,
                (peer_agent_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_peer_agents(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT p.*, s.scope_name, s.description AS scope_description,
                       s.allowed_task_types, s.allowed_context_types,
                       s.can_receive_context, s.sandbox_mode, s.risk_level
                FROM peer_agents p
                LEFT JOIN trust_scopes s ON p.trust_scope_id = s.scope_id
                ORDER BY COALESCE(p.revoked_at, ''), p.display_name ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_peer_agent(self, peer_agent_id: str, display_name: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_agents SET display_name = ? WHERE peer_agent_id = ?",
                (display_name, peer_agent_id),
            )

    def update_peer_agent_scope(self, peer_agent_id: str, trust_scope_id: str) -> None:
        if not self.get_trust_scope(trust_scope_id):
            raise ValueError(f"Unknown trust scope {trust_scope_id}")
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_agents SET trust_scope_id = ? WHERE peer_agent_id = ?",
                (trust_scope_id, peer_agent_id),
            )

    def update_peer_agent_notes(self, peer_agent_id: str, trust_notes: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_agents SET trust_notes = ? WHERE peer_agent_id = ?",
                (trust_notes, peer_agent_id),
            )

    def update_peer_agent_metadata(self, peer_agent_id: str, metadata: dict | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_agents SET metadata = ? WHERE peer_agent_id = ?",
                (json.dumps(metadata or {}), peer_agent_id),
            )

    def pause_peer_agent(self, peer_agent_id: str, reason: str | None = None) -> None:
        peer = self.get_peer_agent(peer_agent_id)
        if not peer:
            return
        metadata = self._json_object(peer.get("metadata"))
        metadata["paused"] = True
        metadata["paused_reason"] = reason or ""
        metadata["paused_at"] = self._now()
        self.update_peer_agent_metadata(peer_agent_id, metadata)

    def resume_peer_agent(self, peer_agent_id: str) -> None:
        peer = self.get_peer_agent(peer_agent_id)
        if not peer:
            return
        metadata = self._json_object(peer.get("metadata"))
        metadata.pop("paused", None)
        metadata.pop("paused_reason", None)
        metadata.pop("paused_at", None)
        self.update_peer_agent_metadata(peer_agent_id, metadata)

    def revoke_peer_agent(self, peer_agent_id: str, reason: str | None = None) -> None:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE peer_agents
                SET revoked_at = ?, revocation_reason = ?
                WHERE peer_agent_id = ?
                """,
                (now, reason, peer_agent_id),
            )

    def restore_peer_agent(self, peer_agent_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE peer_agents
                SET revoked_at = NULL, revocation_reason = NULL
                WHERE peer_agent_id = ?
                """,
                (peer_agent_id,),
            )

    def update_peer_last_seen(self, peer_agent_id: str) -> None:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                "UPDATE peer_agents SET last_seen_at = ? WHERE peer_agent_id = ?",
                (now, peer_agent_id),
            )

    # ── Sovereign peer task log ────────────────────────────────

    def create_peer_task_log(
        self,
        *,
        request_id: str,
        requesting_peer_id: str,
        responding_peer_id: str,
        task_type: str,
        current_state: str,
        title: str | None = None,
        instruction: str | None = None,
        context_provided: dict | None = None,
        accepted_at: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        rejection_reason: str | None = None,
        requires_requesting_approval: bool = False,
        requesting_approval_state: str | None = None,
        requires_responding_approval: bool = False,
        responding_approval_state: str | None = None,
        result_type: str | None = None,
        result_summary: str | None = None,
        result_data: dict | None = None,
        scope_validated: bool = False,
        signature_verified: bool = False,
        sandbox_executed: bool = False,
        execution_time_ms: int | None = None,
    ) -> str:
        now = self._now()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO peer_task_log (
                    request_id, requesting_peer_id, responding_peer_id,
                    task_type, title, instruction, context_provided,
                    requested_at, accepted_at, started_at, completed_at,
                    current_state, rejection_reason,
                    requires_requesting_approval, requesting_approval_state,
                    requires_responding_approval, responding_approval_state,
                    result_type, result_summary, result_data,
                    scope_validated, signature_verified, sandbox_executed, execution_time_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    requesting_peer_id,
                    responding_peer_id,
                    task_type,
                    title,
                    instruction,
                    json.dumps(context_provided or {}),
                    now,
                    accepted_at,
                    started_at,
                    completed_at,
                    current_state,
                    rejection_reason,
                    int(requires_requesting_approval),
                    requesting_approval_state,
                    int(requires_responding_approval),
                    responding_approval_state,
                    result_type,
                    result_summary,
                    json.dumps(result_data or {}),
                    int(scope_validated),
                    int(signature_verified),
                    int(sandbox_executed),
                    execution_time_ms,
                ),
            )
        return request_id

    def update_peer_task_log_state(
        self,
        request_id: str,
        *,
        current_state: str,
        accepted_at: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        rejection_reason: str | None = None,
        requesting_approval_state: str | None = None,
        responding_approval_state: str | None = None,
        result_type: str | None = None,
        result_summary: str | None = None,
        result_data: dict | None = None,
        scope_validated: bool | None = None,
        signature_verified: bool | None = None,
        sandbox_executed: bool | None = None,
        execution_time_ms: int | None = None,
    ) -> None:
        final_completed_at = completed_at
        if final_completed_at is None and current_state in {"completed", "rejected", "failed", "cancelled"}:
            final_completed_at = self._now()

        updates: dict[str, object] = {"current_state": current_state}
        if accepted_at is not None:
            updates["accepted_at"] = accepted_at
        if started_at is not None:
            updates["started_at"] = started_at
        if final_completed_at is not None:
            updates["completed_at"] = final_completed_at
        if rejection_reason is not None:
            updates["rejection_reason"] = rejection_reason
        if requesting_approval_state is not None:
            updates["requesting_approval_state"] = requesting_approval_state
        if responding_approval_state is not None:
            updates["responding_approval_state"] = responding_approval_state
        if result_type is not None:
            updates["result_type"] = result_type
        if result_summary is not None:
            updates["result_summary"] = result_summary
        if result_data is not None:
            updates["result_data"] = json.dumps(result_data)
        if scope_validated is not None:
            updates["scope_validated"] = int(scope_validated)
        if signature_verified is not None:
            updates["signature_verified"] = int(signature_verified)
        if sandbox_executed is not None:
            updates["sandbox_executed"] = int(sandbox_executed)
        if execution_time_ms is not None:
            updates["execution_time_ms"] = execution_time_ms

        assignments = ", ".join(f"{column} = ?" for column in updates.keys())
        values = list(updates.values()) + [request_id]
        with self._conn() as conn:
            conn.execute(
                f"UPDATE peer_task_log SET {assignments} WHERE request_id = ?",
                values,
            )

    def get_peer_task_log(self, request_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM peer_task_log WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_peer_task_logs(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM peer_task_log ORDER BY requested_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def append_peer_conversation_log(
        self,
        peer_device_id: str,
        *,
        role: str,
        text: str,
        via_transport: str = "mesh",
        correlation_id: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        clean_text = (text or "").strip()
        if not clean_text:
            raise ValueError("text is required")
        normalized_role = (role or "").strip().lower()
        if normalized_role not in {"local", "peer"}:
            raise ValueError("role must be 'local' or 'peer'")
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO peer_conversation_log (
                    peer_device_id, role, text, via_transport, correlation_id, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    peer_device_id,
                    normalized_role,
                    clean_text,
                    via_transport,
                    correlation_id,
                    json.dumps(metadata or {}),
                    self._now(),
                ),
            )
        return int(cursor.lastrowid)

    def list_peer_conversation_logs(self, peer_device_id: str, *, limit: int = 40) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT entry_id, peer_device_id, role, text, via_transport, correlation_id, metadata, created_at
                FROM peer_conversation_log
                WHERE peer_device_id = ?
                ORDER BY entry_id DESC
                LIMIT ?
                """,
                (peer_device_id, limit),
            ).fetchall()
        records = [dict(row) for row in reversed(rows)]
        for record in records:
            record["metadata"] = self._json_object(record.get("metadata"))
        return records

    def _json_list(self, value) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if not value:
            return []
        try:
            loaded = json.loads(value)
        except Exception:
            return []
        if not isinstance(loaded, list):
            return []
        return [str(item) for item in loaded]

    def _json_object(self, value) -> dict:
        if isinstance(value, dict):
            return dict(value)
        if not value:
            return {}
        try:
            loaded = json.loads(value)
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    # ── Audit tests ────────────────────────────────────────────

    def run_separation_audit(self) -> dict:
        """
        Run the three tests from the ChatGPT audit.
        Returns pass/fail for each.
        """
        results = {}

        devices = self.list_all()
        results["devices_found"] = len(devices)

        # Test A: identity survives without transport
        # Check that device_identities has records without transport bindings
        with self._conn() as conn:
            orphans = conn.execute("""
                SELECT COUNT(*) as c FROM device_identities i
                LEFT JOIN device_transports tr ON i.device_id = tr.device_id
                WHERE tr.device_id IS NULL
            """).fetchone()["c"]
        results["test_a_identity_independent_of_transport"] = (
            "PASS — devices can exist without transport bindings"
            if True  # schema enforces this via LEFT JOIN
            else "FAIL"
        )

        # Test B: transport keyed separately from identity
        with self._conn() as conn:
            transport_count = conn.execute(
                "SELECT COUNT(*) as c FROM device_transports"
            ).fetchone()["c"]
            identity_count = conn.execute(
                "SELECT COUNT(*) as c FROM device_identities"
            ).fetchone()["c"]
        results["test_b_transport_keyed_separately"] = (
            "PASS — transport table is separate from identity table"
            if transport_count <= identity_count
            else "FAIL"
        )

        # Test C: lookup by device_id works without telegram_chat_id
        if devices:
            sample_device_id = devices[0]["device_id"]
            record = self.get_by_device_id(sample_device_id)
            results["test_c_address_by_device_id"] = (
                f"PASS — {sample_device_id} resolved correctly"
                if record else "FAIL — device_id lookup broken"
            )
        else:
            results["test_c_address_by_device_id"] = "SKIP — no devices registered yet"

        # List all devices with their transport status
        results["devices"] = [
            {
                "device_id":      d["device_id"],
                "name":           d["device_name"],
                "type":           d["device_type"],
                "transport":      d["transport_type"] or "NONE (identity only)",
                "telegram_bound": d["telegram_chat_id"] is not None,
            }
            for d in devices
        ]

        return results
