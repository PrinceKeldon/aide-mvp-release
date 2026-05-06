"""
AIDE Owner Mesh — task lifecycle engine.

Tracks owner-mesh tasks independently from the conversational task queue.
This is the distributed coordination record for:
- local execution
- delegated execution
- approval gating
- retry / resume state
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from loguru import logger


class MeshTaskState(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    ROUTED = "routed"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    QUEUED = "queued"
    CANCELLED = "cancelled"


@dataclass
class MeshTaskRecord:
    task_id: str
    description: str
    origin_device_id: str
    assigned_device_id: str | None
    state: str
    payload: dict
    result: str = ""
    approval_device_id: str | None = None
    retries: int = 0
    created_at: str = ""
    updated_at: str = ""
    archived_at: str | None = None
    archive_bucket: str | None = None
    archive_reason: str | None = None
    backup_exported_at: str | None = None


class OwnerMeshTaskEngine:
    COMPLETED_ARCHIVE_AFTER = timedelta(hours=48)
    TERMINAL_ARCHIVE_AFTER = timedelta(days=7)

    def __init__(self, memory) -> None:
        self._memory = memory
        self._init_db()

    def _init_db(self) -> None:
        with self._memory._get_db() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS owner_mesh_tasks (
                    task_id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    origin_device_id TEXT NOT NULL,
                    assigned_device_id TEXT,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    result TEXT NOT NULL DEFAULT '',
                    approval_device_id TEXT,
                    retries INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT,
                    archive_bucket TEXT,
                    archive_reason TEXT,
                    backup_exported_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(owner_mesh_tasks)").fetchall()
            }
            if "archived_at" not in columns:
                conn.execute("ALTER TABLE owner_mesh_tasks ADD COLUMN archived_at TEXT")
            if "archive_bucket" not in columns:
                conn.execute("ALTER TABLE owner_mesh_tasks ADD COLUMN archive_bucket TEXT")
            if "archive_reason" not in columns:
                conn.execute("ALTER TABLE owner_mesh_tasks ADD COLUMN archive_reason TEXT")
            if "backup_exported_at" not in columns:
                conn.execute("ALTER TABLE owner_mesh_tasks ADD COLUMN backup_exported_at TEXT")
        logger.info("Owner mesh task engine ready")

    def create_task(
        self,
        description: str,
        origin_device_id: str,
        payload: dict | None = None,
        assigned_device_id: str | None = None,
        state: MeshTaskState = MeshTaskState.CREATED,
    ) -> str:
        task_id = f"mesh_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        record = MeshTaskRecord(
            task_id=task_id,
            description=description,
            origin_device_id=origin_device_id,
            assigned_device_id=assigned_device_id,
            state=state.value,
            payload=payload or {},
            created_at=now,
            updated_at=now,
        )
        self._save(record)
        return task_id

    def delete_task(self, task_id: str) -> bool:
        with self._memory._get_db() as conn:
            cursor = conn.execute(
                "DELETE FROM owner_mesh_tasks WHERE task_id = ?",
                (task_id,),
            )
            return cursor.rowcount > 0

    def transition(
        self,
        task_id: str,
        state: MeshTaskState,
        *,
        assigned_device_id: str | None = None,
        result: str | None = None,
        approval_device_id: str | None = None,
        retries: int | None = None,
        payload_update: dict | None = None,
        archive_immediately: bool = False,
    ) -> MeshTaskRecord | None:
        record = self.get_task(task_id)
        if not record:
            return None
        record.state = state.value
        record.updated_at = datetime.now(timezone.utc).isoformat()
        if assigned_device_id is not None:
            record.assigned_device_id = assigned_device_id
        if result is not None:
            record.result = result
        if approval_device_id is not None:
            record.approval_device_id = approval_device_id
        if retries is not None:
            record.retries = retries
        if payload_update:
            record.payload = {**record.payload, **payload_update}
        self._save(record)
        
        # Auto-archive if it's a terminal state or explicitly requested
        terminal_states = {MeshTaskState.COMPLETED, MeshTaskState.FAILED, MeshTaskState.CANCELLED}
        if state in terminal_states or archive_immediately:
            self.archive_task(task_id, reason="transition_to_terminal" if state in terminal_states else "manual")
            
        return record

    def archive_task(
        self,
        task_id: str,
        *,
        reason: str = "manual",
        archived_at: str | None = None,
    ) -> MeshTaskRecord | None:
        record = self.get_task(task_id)
        if not record:
            return None
        record.archived_at = archived_at or datetime.now(timezone.utc).isoformat()
        record.archive_bucket = (record.payload or {}).get("workflow_type") or "generic_task"
        record.archive_reason = reason
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self._save(record)
        return record

    def record_retry(self, task_id: str) -> MeshTaskRecord | None:
        record = self.get_task(task_id)
        if not record:
            return None
        return self.transition(
            task_id,
            MeshTaskState.QUEUED,
            retries=record.retries + 1,
        )
        
    def restore_from_archive(self, task_id: str) -> MeshTaskRecord | None:
        record = self.get_task(task_id)
        if not record:
            return None
        record.archived_at = None
        record.archive_bucket = None
        record.archive_reason = None
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self._save(record)
        return record

    def append_payload_list(self, task_id: str, key: str, value) -> MeshTaskRecord | None:
        record = self.get_task(task_id)
        if not record:
            return None
        items = list(record.payload.get(key, []))
        items.append(value)
        return self.transition(
            task_id,
            MeshTaskState(record.state),
            payload_update={key: items},
        )

    def get_task(self, task_id: str) -> MeshTaskRecord | None:
        with self._memory._get_db() as conn:
            row = conn.execute(
                "SELECT * FROM owner_mesh_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_active(self) -> list[MeshTaskRecord]:
        self._auto_archive_stale_tasks()
        terminal_states = {
            MeshTaskState.COMPLETED.value,
            MeshTaskState.FAILED.value,
            MeshTaskState.CANCELLED.value,
        }
        placeholders = ", ".join("?" for _ in terminal_states)
        with self._memory._get_db() as conn:
            rows = conn.execute(
                f"SELECT * FROM owner_mesh_tasks WHERE archived_at IS NULL AND state NOT IN ({placeholders}) ORDER BY created_at DESC",
                tuple(terminal_states),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_recent(self, limit: int = 25) -> list[MeshTaskRecord]:
        self._auto_archive_stale_tasks()
        with self._memory._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_mesh_tasks WHERE archived_at IS NULL ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_archive(self, limit: int = 100) -> list[MeshTaskRecord]:
        self._auto_archive_stale_tasks()
        with self._memory._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_mesh_tasks WHERE archived_at IS NOT NULL ORDER BY archived_at DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def export_archive_snapshot(self, limit: int = 1000) -> tuple[str, list[MeshTaskRecord]]:
        exported_at = datetime.now(timezone.utc).isoformat()
        records = self.list_archive(limit)
        if records:
            task_ids = [record.task_id for record in records]
            placeholders = ", ".join("?" for _ in task_ids)
            with self._memory._get_db() as conn:
                conn.execute(
                    f"UPDATE owner_mesh_tasks SET backup_exported_at = ? WHERE task_id IN ({placeholders})",
                    (exported_at, *task_ids),
                )
            records = [self.get_task(task_id) for task_id in task_ids]
            records = [record for record in records if record is not None]
        return exported_at, records

    def delete_archived_for_backup(self, backup_exported_at: str) -> int:
        with self._memory._get_db() as conn:
            cursor = conn.execute(
                """
                DELETE FROM owner_mesh_tasks
                WHERE archived_at IS NOT NULL AND backup_exported_at = ?
                """,
                (backup_exported_at,),
            )
        return cursor.rowcount or 0

    def _save(self, record: MeshTaskRecord) -> None:
        with self._memory._get_db() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO owner_mesh_tasks (
                    task_id, description, origin_device_id, assigned_device_id,
                    state, payload, result, approval_device_id, retries,
                    archived_at, archive_bucket, archive_reason, backup_exported_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.description,
                    record.origin_device_id,
                    record.assigned_device_id,
                    record.state,
                    json.dumps(record.payload),
                    record.result,
                    record.approval_device_id,
                    record.retries,
                    record.archived_at,
                    record.archive_bucket,
                    record.archive_reason,
                    record.backup_exported_at,
                    record.created_at,
                    record.updated_at,
                ),
            )

    def _row_to_record(self, row) -> MeshTaskRecord:
        return MeshTaskRecord(
            task_id=row["task_id"],
            description=row["description"],
            origin_device_id=row["origin_device_id"],
            assigned_device_id=row["assigned_device_id"],
            state=row["state"],
            payload=json.loads(row["payload"] or "{}"),
            result=row["result"] or "",
            approval_device_id=row["approval_device_id"],
            retries=row["retries"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            archived_at=row["archived_at"],
            archive_bucket=row["archive_bucket"],
            archive_reason=row["archive_reason"],
            backup_exported_at=row["backup_exported_at"],
        )

    def _auto_archive_stale_tasks(self) -> None:
        now = datetime.now(timezone.utc)
        terminal_states = {
            MeshTaskState.COMPLETED.value,
            MeshTaskState.FAILED.value,
            MeshTaskState.CANCELLED.value,
        }
        with self._memory._get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM owner_mesh_tasks WHERE archived_at IS NULL AND state IN (?, ?, ?)",
                tuple(terminal_states),
            ).fetchall()
        for row in rows:
            record = self._row_to_record(row)
            updated_at = self._parse_timestamp(record.updated_at)
            if updated_at is None:
                continue
            archive_after = (
                self.COMPLETED_ARCHIVE_AFTER
                if record.state == MeshTaskState.COMPLETED.value
                else self.TERMINAL_ARCHIVE_AFTER
            )
            if now - updated_at >= archive_after:
                reason = (
                    "auto_completed_retention"
                    if record.state == MeshTaskState.COMPLETED.value
                    else "auto_terminal_retention"
                )
                self.archive_task(
                    record.task_id,
                    reason=reason,
                    archived_at=now.isoformat(),
                )

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
