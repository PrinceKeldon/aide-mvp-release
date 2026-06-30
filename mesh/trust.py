"""
AIDE Mesh -- Cross-Sovereign Trust Model
Relationship types and trust scopes for the mesh.

Every peer in the trust graph has:
  - RelationshipType: OWNER_DEVICE or SOVEREIGN_PEER
  - TrustScope: per-capability flags

Default = deny. Nothing flows unless explicitly allowed.
"""
import json
import sqlite3
from enum import Enum
from datetime import datetime
from pathlib import Path
from loguru import logger


class RelationshipType(str, Enum):
    OWNER_DEVICE   = "owner_device"    # Same user, shared sovereignty
    SOVEREIGN_PEER = "sovereign_peer"  # Independent agent, external trust


class TrustScope:
    """Per-peer capability flags. Default = deny everything."""

    OWNER_DEFAULTS = {
        "can_request_tasks":        True,
        "can_receive_results":      True,
        "can_send_execution_updates": True,
        "can_request_approvals":    True,
        "can_receive_approvals":    True,
        "can_receive_brief":        True,
        "can_receive_memory":       False,  # explicit only even for owner
        "can_receive_context":      "explicit_only",
    }

    TERMINAL_DEFAULTS = {
        "can_request_tasks":        False,
        "can_receive_results":      False,
        "can_send_execution_updates": True,
        "can_request_approvals":    False,
        "can_receive_approvals":    True,   # terminals can approve
        "can_receive_brief":        False,  # no full brief on terminals
        "can_receive_memory":       False,
        "can_receive_context":      "none",
    }

    SOVEREIGN_PEER_DEFAULTS = {
        "can_request_tasks":        True,
        "can_receive_results":      True,
        "can_send_execution_updates": True,
        "can_request_approvals":    False,
        "can_receive_approvals":    False,
        "can_receive_brief":        False,  # never by default
        "can_receive_memory":       False,
        "can_receive_context":      "explicit_only",
    }

    def __init__(self, scope_dict: dict):
        self._scope = scope_dict

    def allows(self, capability: str) -> bool:
        return bool(self._scope.get(capability, False))

    def context_level(self) -> str:
        return self._scope.get("can_receive_context", "none")

    def to_dict(self) -> dict:
        return dict(self._scope)

    @classmethod
    def for_owner_device(cls) -> "TrustScope":
        return cls(dict(cls.OWNER_DEFAULTS))

    @classmethod
    def for_sovereign_terminal(cls) -> "TrustScope":
        return cls(dict(cls.TERMINAL_DEFAULTS))

    @classmethod
    def for_sovereign_peer(cls) -> "TrustScope":
        return cls(dict(cls.SOVEREIGN_PEER_DEFAULTS))


class MessageClass(str, Enum):
    TASK       = "task"
    RESULT     = "result"
    EXECUTION  = "execution"
    APPROVAL   = "approval"
    BRIEF      = "brief"
    MEMORY     = "memory"
    CONTEXT    = "context"


# Which capability flag gates each message class
MESSAGE_GATE = {
    MessageClass.TASK:      "can_request_tasks",
    MessageClass.RESULT:    "can_receive_results",
    MessageClass.EXECUTION: "can_send_execution_updates",
    MessageClass.APPROVAL:  "can_receive_approvals",
    MessageClass.BRIEF:     "can_receive_brief",
    MessageClass.MEMORY:    "can_receive_memory",
    MessageClass.CONTEXT:   "can_receive_context",
}


class TrustGraph:
    """
    SQLite trust graph with relationship types and scopes.
    Single source of truth for who can communicate and what they can see.
    """

    def __init__(self, db_path: Path = None):
        if db_path is None:
            db_path = Path.home() / ".aide" / "data" / "trust_graph.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = str(db_path)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trust_graph (
                    peer_id           TEXT PRIMARY KEY,
                    public_key        TEXT NOT NULL,
                    device_name       TEXT NOT NULL,
                    device_type       TEXT NOT NULL,
                    relationship_type TEXT NOT NULL DEFAULT 'sovereign_peer',
                    trust_scope       TEXT NOT NULL,
                    paired_at         TEXT NOT NULL,
                    last_seen         TEXT,
                    revoked           INTEGER NOT NULL DEFAULT 0
                )
            """)
        logger.info("Trust graph DB ready")

    def _conn(self):
        conn = sqlite3.connect(self._db)
        conn.row_factory = sqlite3.Row
        return conn

    def add_peer(
        self,
        peer_id: str,
        public_key: bytes,
        device_name: str,
        device_type: str,
        relationship_type: RelationshipType = RelationshipType.OWNER_DEVICE,
        trust_scope: TrustScope = None,
    ):
        if trust_scope is None:
            if relationship_type == RelationshipType.OWNER_DEVICE:
                trust_scope = TrustScope.for_owner_device()
            else:
                trust_scope = TrustScope.for_sovereign_peer()

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trust_graph
                  (peer_id, public_key, device_name, device_type,
                   relationship_type, trust_scope, paired_at, revoked)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                peer_id,
                public_key.hex() if isinstance(public_key, bytes) else public_key,
                device_name,
                device_type,
                relationship_type.value,
                json.dumps(trust_scope.to_dict()),
                datetime.utcnow().isoformat(),
            ))
        logger.info(f"Peer added: {device_name} ({relationship_type.value})")

    def get_peer(self, peer_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trust_graph WHERE peer_id = ? AND revoked = 0",
                (peer_id,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def is_trusted(self, peer_id: str) -> bool:
        peer = self.get_peer(peer_id)
        return peer is not None

    def is_message_allowed(
        self,
        peer_id: str,
        message_class: MessageClass,
    ) -> bool:
        """
        Policy check — called before sending OR accepting any message.
        Default = deny.
        """
        peer = self.get_peer(peer_id)
        if not peer:
            logger.warning(f"Message blocked: {peer_id} not in trust graph")
            return False

        scope = TrustScope(json.loads(peer["trust_scope"]))
        gate  = MESSAGE_GATE.get(message_class)

        if gate is None:
            return False

        allowed = scope.allows(gate)
        if not allowed:
            logger.warning(
                f"Message blocked: {message_class.value} not allowed "
                f"for {peer['device_name']} ({peer['relationship_type']})"
            )
        return allowed

    def get_brief_recipients(self) -> list[dict]:
        """Return only peers that are allowed to receive the morning brief."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trust_graph WHERE revoked = 0"
            ).fetchall()

        recipients = []
        for row in rows:
            scope = TrustScope(json.loads(row["trust_scope"]))
            if scope.allows("can_receive_brief"):
                recipients.append(dict(row))
        return recipients

    def get_approval_terminals(self) -> list[dict]:
        """Return peers that can receive approval requests."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trust_graph WHERE revoked = 0"
            ).fetchall()

        terminals = []
        for row in rows:
            scope = TrustScope(json.loads(row["trust_scope"]))
            if scope.allows("can_receive_approvals"):
                terminals.append(dict(row))
        return terminals

    def revoke_peer(self, peer_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE trust_graph SET revoked = 1 WHERE peer_id = ?",
                (peer_id,)
            )
        logger.info(f"Peer revoked: {peer_id}")

    def list_peers(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trust_graph WHERE revoked = 0 ORDER BY paired_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
