"""
AIDE -- memory manager
Two complementary stores, both local, both private.

SQLite   -- structured facts: name, preferences, recurring tasks
ChromaDB -- semantic vector search: past conversations

Persistent memory: on startup AIDE loads recent conversations
so she remembers where you left off even after a restart.
Your data never leaves this device.
"""

import os
import sqlite3
import json
import asyncio
from datetime import datetime
from loguru import logger


os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["CHROMA_TELEMETRY"] = "False"

from core.settings import settings

try:
    import chromadb
    from chromadb.utils import embedding_functions

    CHROMA_AVAILABLE = True
except ImportError:
    CHROMA_AVAILABLE = False
    logger.warning("ChromaDB not installed -- semantic recall disabled")


class MemoryManager:
    def __init__(self) -> None:
        self._init_sqlite()
        self._migrate_legacy_conversations()
        self._init_chroma()

        # Asynchronous write pipeline
        self._write_queue = asyncio.Queue()
        try:
            self._worker_task = asyncio.create_task(self._write_worker())
        except RuntimeError:
            # Not in an event loop yet
            self._worker_task = None

        from memory.obsidian import ObsidianMemorySync

        self.obsidian = ObsidianMemorySync(self)

    # ── SQLite ───────────────────────────────────────────────────

    def _init_sqlite(self) -> None:
        self._db_path = str(settings.memory_db_path)
        with self._get_db() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS facts (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS Users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS Sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    session_metadata TEXT,
                    FOREIGN KEY(user_id) REFERENCES Users(user_id)
                );

                CREATE TABLE IF NOT EXISTS Exchanges (
                    exchange_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    token_count INTEGER,
                    metadata TEXT,
                    FOREIGN KEY(session_id) REFERENCES Sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    agent_reply TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    reversible INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_approvals (
                    action_id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    tier TEXT NOT NULL,
                    reversible INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    decision INTEGER,
                    decided_at TEXT,
                    resolved_by TEXT
                );

                -- Stratified Memory Layers
                CREATE TABLE IF NOT EXISTS episodic_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    user_message TEXT,
                    agent_response TEXT,
                    importance_score REAL,
                    expires_at TEXT,
                    metadata TEXT
                );

                CREATE TABLE IF NOT EXISTS tactical_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT,
                    category TEXT,
                    content TEXT,
                    importance_score REAL,
                    status TEXT,
                    metadata TEXT,
                    updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tactical_relationships (
                    source_id INTEGER,
                    target_id INTEGER,
                    relationship_type TEXT,
                    FOREIGN KEY(source_id) REFERENCES tactical_memories(id),
                    FOREIGN KEY(target_id) REFERENCES tactical_memories(id)
                );

                CREATE TABLE IF NOT EXISTS strategic_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT,
                    title TEXT,
                    description TEXT,
                    importance_score REAL,
                    validation_count INTEGER,
                    horizon_days INTEGER,
                    status TEXT,
                    metadata TEXT,
                    updated_at TEXT,
                    superseded_by INTEGER,
                    FOREIGN KEY(superseded_by) REFERENCES strategic_memories(id)
                );

                CREATE TABLE IF NOT EXISTS strategic_insights (
                    memory_id INTEGER,
                    insight_type TEXT,
                    confidence REAL,
                    FOREIGN KEY(memory_id) REFERENCES strategic_memories(id)
                );

                CREATE TABLE IF NOT EXISTS identity_facts (
                    entity_type TEXT,
                    entity_id TEXT,
                    property TEXT,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS identity_relationships (
                    source_entity_type TEXT,
                    source_entity_id TEXT,
                    relationship_type TEXT,
                    target_entity_type TEXT,
                    target_entity_id TEXT,
                    properties TEXT
                );

                CREATE TABLE IF NOT EXISTS memory_lifecycle (
                    memory_type TEXT NOT NULL,
                    memory_id INTEGER NOT NULL,
                    action TEXT,
                    timestamp TEXT
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    description TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'EUR',
                    category TEXT,
                    source_file TEXT,
                    month_key TEXT NOT NULL,
                    hash TEXT UNIQUE NOT NULL,
                    statement_hash TEXT,
                    original_filename TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS budget_targets (
                    id TEXT PRIMARY KEY,
                    category TEXT UNIQUE NOT NULL,
                    monthly_target REAL NOT NULL DEFAULT 0,
                    notes TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS merchant_rules (
                    id TEXT PRIMARY KEY,
                    pattern TEXT NOT NULL,
                    category TEXT NOT NULL,
                    match_type TEXT NOT NULL DEFAULT 'keyword',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finance_goals (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_amount REAL NOT NULL,
                    current_amount REAL NOT NULL DEFAULT 0,
                    monthly_contribution REAL NOT NULL DEFAULT 0,
                    deadline TEXT,
                    linked_project_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finance_budget_setup (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'not_started',
                    current_step TEXT NOT NULL DEFAULT 'income',
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS finance_chat_messages (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    month_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL
                );
            """)
        logger.info(f"SQLite memory ready at {self._db_path}")

    def _migrate_legacy_conversations(self) -> None:
        """Migrate data from legacy conversations table to the new schema."""
        import uuid

        with self._get_db() as conn:
            legacy_convs = conn.execute(
                "SELECT id, timestamp, user_message, agent_reply FROM conversations"
            ).fetchall()
            if not legacy_convs:
                return

            # Check if we already migrated (e.g., by checking if Exchanges has data or if conversations is empty)
            # To be safe, we'll check if we have a 'legacy' session.
            existing_legacy = conn.execute(
                "SELECT session_id FROM Sessions WHERE session_metadata LIKE '%legacy%'"
            ).fetchone()
            if existing_legacy:
                return

            logger.info("Migrating legacy conversations to new schema...")

            # Create default user
            user_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO Users (user_id, username, created_at) VALUES (?, ?, ?)",
                (user_id, "Default User", datetime.utcnow().isoformat()),
            )

            # Create legacy session
            session_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO Sessions (session_id, user_id, start_time, session_metadata) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    user_id,
                    datetime.utcnow().isoformat(),
                    json.dumps({"type": "legacy_migration"}),
                ),
            )

            for row in legacy_convs:
                ts = row["timestamp"]
                # User exchange
                conn.execute(
                    "INSERT INTO Exchanges (exchange_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), session_id, "user", row["user_message"], ts),
                )
                # Assistant exchange
                conn.execute(
                    "INSERT INTO Exchanges (exchange_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        session_id,
                        "assistant",
                        row["agent_reply"],
                        ts,
                    ),
                )

            # We don't delete from conversations to avoid data loss, but we can mark it as migrated in a fact
            logger.info(f"Migrated {len(legacy_convs)} conversations.")

        self.store_fact("legacy_conversations_migrated", "true")

    async def _write_worker(self) -> None:
        """Background worker to process exchange writes from the queue."""
        logger.debug("Memory write worker started")
        while True:
            try:
                item = await self._write_queue.get()
                if item is None:
                    break

                session_id, role, content, timestamp, token_count, metadata = item
                import uuid

                with self._get_db() as conn:
                    conn.execute(
                        """INSERT INTO Exchanges (exchange_id, session_id, role, content, timestamp, token_count, metadata)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(uuid.uuid4()),
                            session_id,
                            role,
                            content,
                            timestamp,
                            token_count,
                            json.dumps(metadata or {}),
                        ),
                    )
                self._write_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in memory write worker: {e}")

    async def store_exchange(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: str | None = None,
        token_count: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Queue an exchange for asynchronous persistence."""
        ts = timestamp or datetime.utcnow().isoformat()
        await self._write_queue.put(
            (session_id, role, content, ts, token_count, metadata)
        )

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def store_fact(self, key: str, value: str) -> None:
        with self._get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.utcnow().isoformat()),
            )
        logger.debug(f"Fact stored: {key} = {value}")

    def store_transcription_chunk(
        self, session_id: str, timestamp: int, text: str
    ) -> None:
        """Store a real-time transcription chunk for a given session."""
        # For now, we store these in a separate table or as a JSON blob in facts
        # until the session is closed and processed.
        key = f"onit_session_{session_id}"
        existing = self.get_fact(key)
        chunks = json.loads(existing) if existing else []
        chunks.append({"t": timestamp, "x": text})
        self.store_fact(key, json.dumps(chunks))
        logger.debug(f"Transcription chunk stored for session {session_id}")

    def get_fact(self, key: str) -> str | None:
        with self._get_db() as conn:
            row = conn.execute(
                "SELECT value FROM facts WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def get_all_facts(self) -> dict[str, str]:
        with self._get_db() as conn:
            rows = conn.execute("SELECT key, value FROM facts").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def log_action(
        self,
        action: str,
        tier: str,
        outcome: str,
        reversible: bool = True,
    ) -> None:
        with self._get_db() as conn:
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, action, tier, outcome, reversible)
                   VALUES (?, ?, ?, ?, ?)""",
                (datetime.utcnow().isoformat(), action, tier, outcome, int(reversible)),
            )

    def create_session(self, user_id: str = None, metadata: dict = None) -> str:
        """Create a new session and return its ID."""
        import uuid

        session_id = str(uuid.uuid4())
        user_id = user_id or "default_user"  # Simplified for now

        # Ensure user exists
        with self._get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO Users (user_id, username, created_at) VALUES (?, ?, ?)",
                (user_id, "Default User", datetime.utcnow().isoformat()),
            )

            conn.execute(
                "INSERT INTO Sessions (session_id, user_id, start_time, session_metadata) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    user_id,
                    datetime.utcnow().isoformat(),
                    json.dumps(metadata or {}),
                ),
            )
        return session_id

    def close_session(self, session_id: str) -> None:
        """Mark a session as ended."""
        with self._get_db() as conn:
            conn.execute(
                "UPDATE Sessions SET end_time = ? WHERE session_id = ?",
                (datetime.utcnow().isoformat(), session_id),
            )

    def get_session_history(self, session_id: str, limit: int = 50) -> list[dict]:
        """Fetch the full exchange chain for a specific session."""
        with self._get_db() as conn:
            rows = conn.execute(
                """SELECT role, content, timestamp, token_count, metadata
                   FROM Exchanges 
                   WHERE session_id = ? 
                   ORDER BY timestamp ASC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_conversation(self, user_message: str, agent_reply: str) -> None:
        """Save every exchange to SQLite for persistent history. (Legacy Wrapper)"""
        # In the new system, we should use store_exchange.
        # For backward compatibility, we use a default session.
        session_id = "global_session"
        # Ensure global session exists
        with self._get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO Sessions (session_id, user_id, start_time) VALUES (?, ?, ?)",
                (session_id, "default_user", datetime.utcnow().isoformat()),
            )

        # Use put_nowait to maintain sync signature
        self._write_queue.put_nowait(
            (
                session_id,
                "user",
                user_message,
                datetime.utcnow().isoformat(),
                None,
                None,
            )
        )
        self._write_queue.put_nowait(
            (
                session_id,
                "assistant",
                agent_reply,
                datetime.utcnow().isoformat(),
                None,
                None,
            )
        )

    def load_recent_conversations(self, n: int = 10) -> list[dict]:
        """
        Load the last N conversations from SQLite.
        Used on startup to rebuild conversation context.
        """
        with self._get_db() as conn:
            # This now has to fetch from Exchanges and pair them, or just fetch the last N messages
            rows = conn.execute(
                """SELECT role, content, timestamp
                   FROM Exchanges
                   ORDER BY timestamp DESC LIMIT ?""",
                (n * 2,),
            ).fetchall()

        # The legacy return format was a list of pairs. Let's try to preserve it or evolve it.
        # For now, let's return pairs by grouping them.
        results = []
        sorted_rows = reversed(rows)

        # This is tricky because the new schema is a stream.
        # For backward compatibility with the old `load_recent_conversations` (which returned pairs):
        current_user = None
        for r in sorted_rows:
            if r["role"] == "user":
                current_user = r["content"]
            elif r["role"] == "assistant" and current_user:
                results.append(
                    {
                        "user": current_user,
                        "assistant": r["content"],
                        "timestamp": r["timestamp"],
                    }
                )
                current_user = None

        return results

    def load_recent_messages(self, n_pairs: int = 10) -> list[dict]:
        """
        Load the last N user/assistant exchanges as chat messages.
        This is the canonical conversation timeline used across model providers.
        """
        messages: list[dict] = []
        for conv in self.load_recent_conversations(n=n_pairs):
            messages.append({"role": "user", "content": conv["user"]})
            messages.append({"role": "assistant", "content": conv["assistant"]})
        return messages

    def save_pending_approval(
        self,
        *,
        action_id: str,
        tool_name: str,
        description: str,
        tier: str,
        reversible: bool,
        payload: dict,
        created_at: str,
        expires_at: str,
        status: str = "pending",
    ) -> None:
        with self._get_db() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pending_approvals
                   (action_id, tool_name, description, tier, reversible, payload,
                    created_at, expires_at, status, decision, decided_at, resolved_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)""",
                (
                    action_id,
                    tool_name,
                    description,
                    tier,
                    int(reversible),
                    json.dumps(payload or {}),
                    created_at,
                    expires_at,
                    status,
                ),
            )

    def load_pending_approvals(self) -> list[dict]:
        with self._get_db() as conn:
            rows = conn.execute(
                """SELECT action_id, tool_name, description, tier, reversible, payload,
                          created_at, expires_at, status, decision, decided_at, resolved_by
                   FROM pending_approvals
                   WHERE status = 'pending'
                   ORDER BY created_at ASC"""
            ).fetchall()
        return [
            {
                "action_id": row["action_id"],
                "tool_name": row["tool_name"],
                "description": row["description"],
                "tier": row["tier"],
                "reversible": bool(row["reversible"]),
                "payload": json.loads(row["payload"] or "{}"),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "status": row["status"],
                "decision": row["decision"],
                "decided_at": row["decided_at"],
                "resolved_by": row["resolved_by"],
            }
            for row in rows
        ]

    def get_pending_approvals(self) -> list[dict]:
        return self.load_pending_approvals()

    def mark_pending_approval_resolved(
        self,
        action_id: str,
        approved: bool,
        resolved_by: str | None = None,
    ) -> None:
        with self._get_db() as conn:
            conn.execute(
                """UPDATE pending_approvals
                   SET status = ?, decision = ?, decided_at = ?, resolved_by = ?
                   WHERE action_id = ?""",
                (
                    "approved" if approved else "denied",
                    int(approved),
                    datetime.utcnow().isoformat(),
                    resolved_by,
                    action_id,
                ),
            )

    def mark_pending_approval_expired(self, action_id: str) -> None:
        with self._get_db() as conn:
            conn.execute(
                """UPDATE pending_approvals
                   SET status = 'expired', decision = NULL, decided_at = ?, resolved_by = NULL
                   WHERE action_id = ?""",
                (datetime.utcnow().isoformat(), action_id),
            )

    async def sync_with_obsidian(self) -> None:
        """Trigger bidirectional sync with the Obsidian vault."""
        import asyncio

        # We could track the last sync timestamp in the database
        last_sync = None
        await self.obsidian.sync_to_obsidian(last_sync=last_sync)
        await self.obsidian.sync_from_obsidian()
        logger.debug("Obsidian sync complete.")

    # ── Stratified Memory Methods ─────────────────────────────────────────

    def store_tactical_memory(
        self,
        namespace: str,
        category: str,
        content: str,
        importance: float,
        status: str = "active",
        metadata: dict = None,
    ) -> int:
        with self._get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO tactical_memories (namespace, category, content, importance_score, status, metadata, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    namespace,
                    category,
                    content,
                    importance,
                    status,
                    json.dumps(metadata or {}),
                    datetime.utcnow().isoformat(),
                ),
            )
            return cursor.lastrowid

    def store_strategic_memory(
        self,
        category: str,
        title: str,
        description: str,
        importance: float,
        horizon: int = 90,
        status: str = "active",
        metadata: dict = None,
    ) -> int:
        with self._get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO strategic_memories (category, title, description, importance_score, horizon_days, status, metadata, updated_at, validation_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    category,
                    title,
                    description,
                    importance,
                    horizon,
                    status,
                    json.dumps(metadata or {}),
                    datetime.utcnow().isoformat(),
                ),
            )
            return cursor.lastrowid

    def get_strategic_memories(self, updated_after: str = None) -> list[dict]:
        with self._get_db() as conn:
            if updated_after:
                rows = conn.execute(
                    "SELECT * FROM strategic_memories WHERE updated_at > ?",
                    (updated_after,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM strategic_memories").fetchall()
            return [dict(r) for r in rows]

    def get_tactical_memories(self, updated_after: str = None) -> list[dict]:
        with self._get_db() as conn:
            if updated_after:
                rows = conn.execute(
                    "SELECT * FROM tactical_memories WHERE updated_at > ?",
                    (updated_after,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tactical_memories").fetchall()
            return [dict(r) for r in rows]

    def update_strategic_memory(self, mem_id: int, updates: dict) -> None:
        if not updates:
            return
        keys = [f"{k} = ?" for k in updates.keys()]
        values = list(updates.values())
        values.append(mem_id)
        with self._get_db() as conn:
            conn.execute(
                f"UPDATE strategic_memories SET {', '.join(keys)}, updated_at = ? WHERE id = ?",
                (*values, datetime.utcnow().isoformat(), mem_id),
            )

    def update_tactical_memory(self, mem_id: int, updates: dict) -> None:
        if not updates:
            return
        keys = [f"{k} = ?" for k in updates.keys()]
        values = list(updates.values())
        values.append(mem_id)
        with self._get_db() as conn:
            conn.execute(
                f"UPDATE tactical_memories SET {', '.join(keys)}, updated_at = ? WHERE id = ?",
                (*values, datetime.utcnow().isoformat(), mem_id),
            )

    def create_relationship(
        self, source_id: int, target_id: int, rel_type: str
    ) -> None:
        with self._get_db() as conn:
            conn.execute(
                "INSERT INTO tactical_relationships (source_id, target_id, relationship_type) VALUES (?, ?, ?)",
                (source_id, target_id, rel_type),
            )

    def search(self, query: str, n: int = 5) -> list[str]:
        """Alias for semantic_search — used by bridge/registry components."""
        return self.semantic_search(query, n=n)

    def get_recent(self, n: int = 10) -> list[dict]:
        """Alias for load_recent_conversations."""
        return self.load_recent_conversations(n=n)

    def get_user_summary(self) -> str:
        """Return a brief summary of known user facts for agent context."""
        name = self.get_fact("user_name") or ""
        location = self.get_fact("user_location") or ""
        timezone = self.get_fact("user_timezone") or ""
        language = self.get_fact("user_language") or ""
        parts = []
        if name:
            parts.append(f"Name: {name}")
        if location:
            parts.append(f"Location: {location}")
        if timezone:
            parts.append(f"Timezone: {timezone}")
        if language:
            parts.append(f"Language: {language}")
        return ", ".join(parts) if parts else ""

    def store_semantic(self, text: str, metadata: dict = None) -> None:
        """Store a text string in ChromaDB for semantic retrieval."""
        import uuid

        try:
            self._collection.add(
                documents=[text],
                metadatas=[metadata or {}],
                ids=[str(uuid.uuid4())],
            )
        except Exception as e:
            logger.debug(f"store_semantic failed: {e}")

    def add_to_semantic(self, text: str, metadata: dict = None) -> None:
        """Alias for store_semantic — used by MemoryExtractor."""
        self.store_semantic(text, metadata=metadata)

    def semantic_search(self, query: str, n: int = 5) -> list[str]:
        """Retrieve semantically similar memories from ChromaDB."""
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n,
            )
            return results["documents"][0] if results["documents"] else []
        except Exception as e:
            logger.debug(f"semantic_search failed: {e}")
            return []

    def search(self, query: str, n: int = 5) -> list[str]:
        """Alias for semantic_search."""
        return self.semantic_search(query, n=n)

    def finalize_transcription(self, session_id: str) -> str | None:
        """Consolidate transcription chunks and remove temporary session storage."""
        key = f"onit_session_{session_id}"
        existing = self.get_fact(key)
        if not existing:
            return None

        chunks = json.loads(existing)
        chunks.sort(key=lambda x: x["t"])
        full_text = " ".join([c["x"] for c in chunks])

        self.store_fact(key, "[]")
        return full_text

    # ── ChromaDB ─────────────────────────────────────────────────

    def _init_chroma(self) -> None:
        if not CHROMA_AVAILABLE:
            self._collection = None
            return
        try:
            client = chromadb.PersistentClient(path=str(settings.chroma_db_path))
            ef = embedding_functions.DefaultEmbeddingFunction()
            self._collection = client.get_or_create_collection(
                name="conversations",
                embedding_function=ef,
            )
            logger.info("ChromaDB semantic memory ready")
        except Exception as e:
            logger.warning(f"ChromaDB init failed: {e} -- semantic recall disabled")
            self._collection = None

    async def store(self, user_message: str, agent_reply: str) -> None:
        """Store a conversation exchange in both SQLite and ChromaDB."""
        # Use the new async pipeline
        await self.store_exchange("global_session", "user", user_message)
        await self.store_exchange("global_session", "assistant", agent_reply)

        # Also save to ChromaDB for semantic search
        if self._collection is None:
            return
        try:
            doc = f"User: {user_message}\nAIDE: {agent_reply}"
            doc_id = f"msg_{datetime.utcnow().timestamp()}"
            self._collection.add(
                documents=[doc],
                ids=[doc_id],
                metadatas=[{"timestamp": datetime.utcnow().isoformat()}],
            )
        except Exception as e:
            logger.warning(f"ChromaDB store failed: {e}")

    async def recall(self, query: str, n_results: int = 3) -> str:
        """
        Retrieve relevant context for a query.
        Combines: known facts + semantically similar past conversations.
        """
        facts = self.get_all_facts()
        fact_lines = [
            f"{k}: {v}"
            for k, v in facts.items()
            if not k.startswith("onboarding") and not k.startswith("trusted_peer")
        ]

        semantic_lines: list[str] = []
        if self._collection is not None:
            try:
                count = self._collection.count()
                if count > 0:
                    results = self._collection.query(
                        query_texts=[query],
                        n_results=min(n_results, count),
                    )
                    docs = results.get("documents", [[]])[0]
                    semantic_lines = [d[:300] for d in docs]
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        if not fact_lines and not semantic_lines:
            return ""

        parts = []
        if fact_lines:
            parts.append("Known facts:\n" + "\n".join(f"  - {f}" for f in fact_lines))
        if semantic_lines:
            parts.append(
                "Relevant past conversations:\n"
                + "\n".join(f"  [{i+1}] {doc}" for i, doc in enumerate(semantic_lines))
            )
        return "\n\n".join(parts)
