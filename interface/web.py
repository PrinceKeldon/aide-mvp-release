"""
AIDE -- Local web interface
A simple chat UI served from your machine plus an owner-facing Your Day view.
Works completely offline — no internet needed.
Open http://localhost:3000 in any browser on this machine.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from core.safety import PendingAction, SafetyTier
from daily_brief.calendar import get_calendar_source_status
from daily_brief.storage import (
    ensure_daily_brief,
    get_daily_brief_item,
    load_daily_brief,
    prune_invalid_draft_items,
    update_daily_brief_item,
    upsert_daily_brief_item,
)
from daily_brief.workflows import (
    build_draft_item_from_email,
    build_draft_item_from_mesh_result,
    build_prepared_item_from_mesh_task,
    generate_email_reply_draft,
)
from mesh.pairing import PairingManager, PeerInvitationManager
from mesh.peer_invitation import (
    build_peer_invitation_payload,
    parse_peer_invitation_payload,
)
from mesh.task_engine import MeshTaskState
from mesh.workflows import get_workflow_definition, normalize_workflow_type
from mesh.ui.pairing_ui import (
    build_pairing_payload,
    generate_pairing_qr,
    parse_pairing_qr,
)
from tools.email_tool import fetch_email_message_by_uid, fetch_email_thread
import qrcode
from core.finance_chat import FinanceChatService
from core.finance_engine import FinanceEngine
from core.finance_report import FinanceReportGenerator
from core.mvp_config import (
    agent_name,
    module_payload,
    onboarding_complete,
    provider_status,
    load_module_settings,
    save_module_settings,
    write_env_values,
)
from core.settings import settings
from tools.finance_ingest import FinanceIngestor

app = FastAPI()

app.mount("/static", StaticFiles(directory="web/static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def mvp_surface_gate(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    modules = load_module_settings()
    peer_mesh_enabled = bool(modules.get("peer_mesh"))
    blocked_peer_path = (
        path in {"/mesh", "/m-peer"}
        or path.startswith("/api/mesh")
        or path.startswith("/api/m-peer")
    )
    blocked_model_path = path in {"/models", "/api/models"}
    if (blocked_peer_path and not peer_mesh_enabled) or blocked_model_path:
        detail = "This surface is outside the MVP shipping build."
        if path.startswith("/api/"):
            return JSONResponse({"ok": False, "detail": detail}, status_code=410)
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>AIDE MVP</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f8fb;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}.panel{{width:min(560px,calc(100% - 32px));background:white;border:1px solid #d8e0ea;border-radius:8px;padding:32px;box-shadow:0 24px 70px rgba(21,32,51,.12)}}h1{{margin:0 0 10px;font-size:30px}}p{{color:#667085;line-height:1.5}}a{{display:inline-block;margin-top:18px;border-radius:8px;padding:11px 14px;background:#0b63ce;color:white;text-decoration:none;font-weight:800;font-size:14px}}</style></head><body><main class="panel"><h1>Outside the MVP build.</h1><p>{detail} Use AIDE, Your Day, Settings, and FinanceOS for this release.</p><a href="/your-day">Open Your Day</a></main></body></html>""",
            status_code=410,
        )
    return await call_next(request)

# Runtime references — wired from main.py
_agent = None
_safety = None
_your_day_service = None
_registry = None
_alias_registry = None
_mesh_node = None
_mesh_discovery = None
_owner_mesh = None
_mesh_identity = None
_router = None
_m_peer_dispatch_tasks: set[asyncio.Task] = set()
ACTIVE_PEER_THREAD_TIMEOUT_SECONDS = 600


def set_agent(agent) -> None:
    global _agent
    _agent = agent


def set_runtime(
    *,
    agent=None,
    safety=None,
    your_day_service=None,
    registry=None,
    alias_registry=None,
    mesh_node=None,
    mesh_discovery=None,
    owner_mesh=None,
    mesh_identity=None,
    router=None,
) -> None:
    global \
        _agent, \
        _safety, \
        _your_day_service, \
        _registry, \
        _alias_registry, \
        _mesh_node, \
        _mesh_discovery, \
        _owner_mesh, \
        _mesh_identity, \
        _router
    _agent = agent
    _safety = safety
    _your_day_service = your_day_service
    _registry = registry
    _alias_registry = alias_registry
    _mesh_node = mesh_node
    _mesh_discovery = mesh_discovery
    _owner_mesh = owner_mesh
    _mesh_identity = mesh_identity
    _router = router


class MessageRequest(BaseModel):
    message: str


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class ItemAskRequest(BaseModel):
    question: str | None = None


class DraftUpdateRequest(BaseModel):
    to: str = ""
    subject: str
    body: str


class MeshRenameRequest(BaseModel):
    name: str


class MeshCapabilityRequest(BaseModel):
    enabled: bool


class MeshPairingRequest(BaseModel):
    payload: str


class MeshTestTaskRequest(BaseModel):
    task: str | None = None


class MeshAskPeerRequest(BaseModel):
    prompt: str


class MeshManualBindRequest(BaseModel):
    host: str
    port: int


class MeshTelegramBindRequest(BaseModel):
    chat_id: str


class MeshTrustRequest(BaseModel):
    trusted: bool


class MeshPingRequest(BaseModel):
    device_id: str


class FitnessOSDispatchRequest(BaseModel):
    input: str


class FinanceChatRequest(BaseModel):
    message: str
    month_key: str | None = None


class FinanceCategoryUpdateRequest(BaseModel):
    category: str
    learn_rule: bool = True


class SetupLLMRequest(BaseModel):
    agent_name: str = "AIDE"
    default_llm: str = "groq"
    groq_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    telegram_bot_token: str = ""


class IntegrationSettingsRequest(BaseModel):
    email_address: str = ""
    email_password: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    email_safety_tier: str = "approve"
    google_calendar_credentials_path: str = "./data/google/calendar_credentials.json"
    google_calendar_token_path: str = "./data/google/calendar_token.json"
    google_calendar_id: str = "primary"
    google_calendar_label: str = "Google Calendar"
    google_calendar_timezone: str = "UTC"
    calendar_ics_path: str = ""
    calendar_ics_label: str = "Local Calendar"
    calendar_ics_timezone: str = "UTC"


class ModuleSettingsRequest(BaseModel):
    modules: dict[str, bool]


class MeshDelegateTaskRequest(BaseModel):
    task: str
    workflow_type: str = "generic_task"
    target_device_id: str | None = None
    requires_approval: bool = False
    context_notes: str | None = None
    payload: dict | None = None


class OnItSessionRequest(BaseModel):
    session_id: str
    action: Literal["start", "stop"]
    task_name: str | None = None


class OnItTranscriptChunkRequest(BaseModel):
    session_id: str
    timestamp: int
    text: str
    confidence: float


class MeshArchiveDeleteRequest(BaseModel):
    backup_exported_at: str


class MPeerTaskCreateRequest(BaseModel):
    title: str
    instruction: str
    task_type: str
    context_type: str = "explicit_facts"
    context_text: str | None = None
    require_requester_approval: bool | None = None


class MPeerTaskCancelRequest(BaseModel):
    reason: str | None = None


class MPeerInvitationAcceptRequest(BaseModel):
    payload: str
    trust_scope_id: str = "assistant_introduction"


class MPeerRenameRequest(BaseModel):
    name: str


class MPeerScopeUpdateRequest(BaseModel):
    trust_scope_id: str


class MPeerPauseRequest(BaseModel):
    reason: str | None = None


class MPeerNotesRequest(BaseModel):
    notes: str | None = None


SUPPORTED_ITEM_ACTIONS = {
    "draft_reply",
    "dismiss",
    "accept",
    "open_thread",
    "show_original",
    "snooze",
    "view",
    "send",
    "open_as_draft",
    "follow_up_task",
    "archive_source_task",
}


def _current_brief_payload() -> dict | None:
    prune_invalid_draft_items(date.today())
    return load_daily_brief(date.today())


def _brief_llm():
    if _agent is not None and hasattr(_agent, "_llm"):
        return _agent._llm
    if _agent is not None and hasattr(_agent, "chat"):
        return _agent
    from core.llm import LLMClient

    return LLMClient()


def _mesh_attention_rows(limit: int = 12) -> list[dict]:
    rows = _owner_mesh_task_rows(limit=200)
    attention = []
    for row in rows:
        state = (row.get("state") or "").lower()
        if state == MeshTaskState.WAITING_APPROVAL.value:
            attention.append(
                {**row, "attention_reason": "waiting_approval", "attention_rank": 0}
            )
        elif state in {MeshTaskState.FAILED.value, MeshTaskState.CANCELLED.value}:
            attention.append(
                {**row, "attention_reason": "retry_needed", "attention_rank": 1}
            )
        elif state in {
            MeshTaskState.CREATED.value,
            MeshTaskState.PLANNED.value,
            MeshTaskState.QUEUED.value,
            MeshTaskState.ROUTED.value,
            MeshTaskState.EXECUTING.value,
        }:
            attention.append(
                {
                    **row,
                    "attention_reason": "running",
                    "attention_rank": 2,
                    "is_stale_running": _is_attention_task_stale(
                        row.get("updated_at") or row.get("created_at")
                    ),
                }
            )
        elif state == MeshTaskState.COMPLETED.value and not row.get(
            "replayed_to_your_day"
        ):
            attention.append(
                {**row, "attention_reason": "ready_to_replay", "attention_rank": 3}
            )
    attention.sort(
        key=lambda row: (
            row.get("attention_rank", 99),
            not bool(row.get("is_stale_running")),
            -(
                _coerce_iso_timestamp(row.get("updated_at") or row.get("created_at"))
                or 0
            ),
        )
    )
    return attention[:limit]


def _coerce_iso_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except Exception:
        return None


def _is_attention_task_stale(
    value: str | None, *, stale_after_seconds: int = 1800
) -> bool:
    timestamp = _coerce_iso_timestamp(value)
    if timestamp is None:
        return False
    return (datetime.now().timestamp() - timestamp) >= stale_after_seconds


def _build_follow_up_item_from_mesh_result(item: dict) -> dict:
    content = item.get("content", {})
    mesh_task_id = content.get("mesh_task_id", "")
    workflow_display_name = content.get("workflow_display_name") or "Delegated Work"
    summary = (
        content.get("summary")
        or item.get("reason")
        or "Review delegated work and decide the next move."
    )
    executor = content.get("executor_device_name") or "Owner Mesh"
    created_at = datetime.now().isoformat()
    follow_up_id = f"mesh_follow_up_{uuid.uuid4().hex[:8]}"
    return {
        "id": follow_up_id,
        "type": "prepared_task",
        "priority": "medium",
        "title": f"Follow up: {item.get('title', workflow_display_name)}",
        "reason": f"Created from delegated work completed by {executor}.",
        "content": {
            "source": "owner_mesh_follow_up",
            "source_item_id": item.get("id"),
            "mesh_task_id": mesh_task_id,
            "workflow_display_name": workflow_display_name,
            "summary": summary,
            "body": content.get("body") or summary,
            "executor_device_name": executor,
            "generated_for": "your_day_follow_up",
            "created_from_replayed_result_at": created_at,
        },
        "actions": ["accept", "dismiss"],
        "created_at": created_at,
        "state": "ready",
    }


def _mesh_peer_rows() -> list[dict]:
    if _registry is None:
        return []
    default_device_id = (
        _alias_registry.get_default() if _alias_registry is not None else None
    )
    peers: dict[str, dict] = {}
    for row in _registry.list_all():
        device_id = row["device_id"]
        device = peers.setdefault(
            device_id,
            {
                "device_id": device_id,
                "device_name": row.get("device_name", ""),
                "device_type": row.get("device_type", ""),
                "public_key_preview": "",
                "trust_level": row.get("trust_level", "trusted"),
                "paired_at": row.get("paired_at"),
                "trust_notes": row.get("trust_notes"),
                "revoked_at": row.get("revoked_at"),
                "trust_source": row.get("trust_source"),
                "last_seen_at": row.get("last_seen_at"),
                "relationship_type": row.get("relationship_type"),
                "transports": [],
                "transport_type": None,
                "telegram_chat_id": None,
                "telegram_username": None,
                "mesh_endpoint": None,
                "capabilities": {
                    "can_execute": bool(row.get("can_execute")),
                    "can_approve": bool(row.get("can_approve")),
                    "can_receive_brief": bool(row.get("can_receive_brief")),
                    "can_receive_execution_updates": bool(
                        row.get("can_receive_execution_updates")
                    ),
                    "can_receive_memory": bool(row.get("can_receive_memory")),
                    "can_receive_context": row.get("can_receive_context")
                    or "summary_only",
                },
                "is_local": device_id == default_device_id,
                "is_trusted": False,
            },
        )
        public_key = row.get("public_key") or ""
        if public_key:
            device["public_key_preview"] = f"{public_key[:16]}...{public_key[-8:]}"
        transport = row.get("transport_type")
        if transport and transport not in device["transports"]:
            device["transports"].append(transport)
        if transport == "mesh":
            device["transport_type"] = "mesh"
        elif transport == "telegram" and device.get("transport_type") != "mesh":
            device["transport_type"] = "telegram"
        if row.get("telegram_chat_id") is not None:
            device["telegram_chat_id"] = row.get("telegram_chat_id")
        if row.get("telegram_username"):
            device["telegram_username"] = row.get("telegram_username")
        if row.get("mesh_host") and row.get("mesh_port") is not None:
            device["mesh_endpoint"] = f"{row['mesh_host']}:{row['mesh_port']}"

    ordered = []
    for device_id, peer in peers.items():
        peer["is_trusted"] = _registry.is_trusted(device_id)
        ordered.append(peer)
    ordered.sort(key=lambda peer: (peer["is_local"], peer["device_name"].lower()))
    return ordered


def _peer_conversation_rows(device_id: str, *, limit: int = 40) -> list[dict]:
    if _registry is None or not hasattr(_registry, "list_peer_conversation_logs"):
        return []
    rows = _registry.list_peer_conversation_logs(device_id, limit=limit)
    peer = _registry.get_by_device_id(device_id) or {}
    peer_name = peer.get("device_name") or device_id
    shaped = []
    for row in rows:
        role = row.get("role") or "peer"
        shaped.append(
            {
                "entry_id": row.get("entry_id"),
                "peer_device_id": device_id,
                "peer_device_name": peer_name,
                "role": role,
                "speaker": "You" if role == "local" else peer_name,
                "text": row.get("text") or "",
                "via_transport": row.get("via_transport") or "mesh",
                "correlation_id": row.get("correlation_id"),
                "metadata": row.get("metadata") or {},
                "created_at": row.get("created_at"),
            }
        )
    return shaped


def _set_active_peer_thread(device_id: str, device_name: str) -> None:
    memory = getattr(_agent, "_memory", None)
    if memory is None or not hasattr(memory, "store_fact"):
        return
    memory.store_fact("active_peer_thread_device_id", device_id)
    memory.store_fact("active_peer_thread_device_name", device_name)
    memory.store_fact("active_peer_thread_mode", "armed")
    memory.store_fact(
        "active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat()
    )


def _clear_active_peer_thread() -> None:
    memory = getattr(_agent, "_memory", None)
    if memory is None or not hasattr(memory, "store_fact"):
        return
    memory.store_fact("active_peer_thread_mode", "idle")
    memory.store_fact("active_peer_thread_device_id", "")
    memory.store_fact("active_peer_thread_device_name", "")
    memory.store_fact(
        "active_peer_thread_updated_at", datetime.now(timezone.utc).isoformat()
    )


def _active_peer_thread_payload() -> dict:
    memory = getattr(_agent, "_memory", None)
    payload = {
        "device_id": "",
        "device_name": "",
        "mode": "idle",
        "status": "idle",
        "active": False,
        "updated_at": "",
        "expires_in_seconds": None,
    }
    if memory is None:
        return payload

    device_id = (memory.get_fact("active_peer_thread_device_id") or "").strip()
    device_name = (memory.get_fact("active_peer_thread_device_name") or "").strip()
    mode = (
        memory.get_fact("active_peer_thread_mode") or "idle"
    ).strip().lower() or "idle"
    updated_at = memory.get_fact("active_peer_thread_updated_at") or ""
    payload.update(
        {
            "device_id": device_id,
            "device_name": device_name,
            "mode": mode,
            "status": mode,
            "updated_at": updated_at,
        }
    )
    if not device_id or not device_name:
        return payload
    if mode != "armed":
        return payload

    try:
        last = datetime.fromisoformat(updated_at)
    except Exception:
        payload["status"] = "stale"
        return payload

    age = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
    if age > ACTIVE_PEER_THREAD_TIMEOUT_SECONDS:
        payload["status"] = "expired"
        payload["expires_in_seconds"] = 0
        return payload

    payload["active"] = True
    payload["status"] = "armed"
    payload["expires_in_seconds"] = max(
        0, int(ACTIVE_PEER_THREAD_TIMEOUT_SECONDS - age)
    )
    return payload


def _mesh_api_payload() -> dict:
    return {
        "available": _registry is not None,
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
        "active_peer_thread": _active_peer_thread_payload(),
    }


def _m_peer_rows() -> list[dict]:
    if _registry is None or not hasattr(_registry, "list_peer_agents"):
        return []
    rows = []
    for peer in _registry.list_peer_agents():
        try:
            metadata = json.loads(peer.get("metadata") or "{}")
        except Exception:
            metadata = {}
        rows.append(
            {
                "peer_agent_id": peer.get("peer_agent_id"),
                "display_name": peer.get("display_name"),
                "owner_name": peer.get("owner_name"),
                "scope_id": peer.get("trust_scope_id"),
                "scope_name": peer.get("scope_name") or peer.get("trust_scope_id"),
                "scope_description": peer.get("scope_description") or "",
                "risk_level": peer.get("risk_level") or "low",
                "allowed_task_types": peer.get("allowed_task_types") or "[]",
                "allowed_context_types": peer.get("allowed_context_types") or "[]",
                "connection_type": peer.get("connection_type") or "offline",
                "paired_at": peer.get("paired_at"),
                "last_seen_at": peer.get("last_seen_at"),
                "trust_notes": peer.get("trust_notes") or "",
                "revoked_at": peer.get("revoked_at"),
                "revocation_reason": peer.get("revocation_reason"),
                "paused": bool(metadata.get("paused")),
                "pause_reason": metadata.get("paused_reason") or "",
                "paused_at": metadata.get("paused_at"),
                "capabilities": json.loads(peer.get("capabilities") or "[]"),
                **_m_peer_scope_copy(peer),
            }
        )
    return rows


def _owner_mesh_task_rows(limit: int = 25) -> list[dict]:
    if _owner_mesh is None:
        return []

    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        return list(getattr(_owner_mesh, "task_log", []))

    rows = []
    list_recent = getattr(task_engine, "list_recent", None)
    records = list_recent(limit) if callable(list_recent) else []

    for record in records:
        origin = (
            _registry.get_by_device_id(record.origin_device_id)
            if _registry is not None
            else None
        )
        assigned = (
            _registry.get_by_device_id(record.assigned_device_id)
            if _registry is not None and record.assigned_device_id
            else None
        )
        approval = (
            _registry.get_by_device_id(record.approval_device_id)
            if _registry is not None and record.approval_device_id
            else None
        )
        payload = record.payload or {}
        approval_state = "not_required"
        if record.state == MeshTaskState.WAITING_APPROVAL.value:
            approval_state = payload.get("approval_state") or "pending"
        elif payload.get("approval_state"):
            approval_state = payload.get("approval_state")
        can_retry = record.state in {
            MeshTaskState.FAILED.value,
            MeshTaskState.CANCELLED.value,
        }
        can_cancel = record.state in {
            MeshTaskState.CREATED.value,
            MeshTaskState.PLANNED.value,
            MeshTaskState.ROUTED.value,
            MeshTaskState.EXECUTING.value,
            MeshTaskState.QUEUED.value,
            MeshTaskState.WAITING_APPROVAL.value,
        }
        can_approve = record.state == MeshTaskState.WAITING_APPROVAL.value
        can_deny = can_approve
        rows.append(
            {
                "task_id": record.task_id,
                "description": record.description,
                "state": record.state,
                "origin_device_id": record.origin_device_id,
                "origin_device_name": (origin or {}).get(
                    "device_name", record.origin_device_id
                ),
                "assigned_device_id": record.assigned_device_id,
                "assigned_device_name": (assigned or {}).get(
                    "device_name", record.assigned_device_id
                ),
                "approval_device_id": record.approval_device_id,
                "approval_device_name": (approval or {}).get(
                    "device_name", record.approval_device_id
                ),
                "result": record.result,
                "result_preview": record.payload.get("result_preview")
                or (record.result or "").strip()[:180],
                "result_kind": record.payload.get("result_kind", "mesh_result"),
                "retries": record.retries,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "archived_at": record.archived_at,
                "archive_bucket": record.archive_bucket,
                "archive_reason": record.archive_reason,
                "payload": payload,
                "workflow_type": normalize_workflow_type(payload.get("workflow_type")),
                "workflow_display_name": payload.get("workflow_display_name")
                or get_workflow_definition(payload.get("workflow_type")).display_name,
                "requires_approval": bool(payload.get("requires_approval")),
                "approval_state": approval_state,
                "context_notes": payload.get("context_notes"),
                "replayed_to_your_day": bool(payload.get("replayed_to_your_day")),
                "your_day_item_id": payload.get("your_day_item_id"),
                "can_retry": can_retry,
                "can_cancel": can_cancel,
                "can_approve": can_approve,
                "can_deny": can_deny,
                "can_archive": record.state
                in {
                    MeshTaskState.COMPLETED.value,
                    MeshTaskState.FAILED.value,
                    MeshTaskState.CANCELLED.value,
                },
                "is_blocked": record.state == MeshTaskState.WAITING_APPROVAL.value,
            }
        )
    return rows


def _find_owner_mesh_task_row(task_id: str) -> dict | None:
    for row in _owner_mesh_task_rows(limit=200):
        if row.get("task_id") == task_id:
            return row
    return None


def _owner_mesh_task_detail(task_id: str) -> dict | None:
    if _owner_mesh is None:
        return None

    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is not None and hasattr(task_engine, "get_task"):
        record = task_engine.get_task(task_id)
        if record is None:
            return None
        origin = (
            _registry.get_by_device_id(record.origin_device_id)
            if _registry is not None
            else None
        )
        assigned = (
            _registry.get_by_device_id(record.assigned_device_id)
            if _registry is not None and record.assigned_device_id
            else None
        )
        approval = (
            _registry.get_by_device_id(record.approval_device_id)
            if _registry is not None and record.approval_device_id
            else None
        )
        payload = record.payload or {}
        workflow_type = normalize_workflow_type(payload.get("workflow_type"))
        workflow = get_workflow_definition(workflow_type)
        approval_state = payload.get("approval_state") or (
            "pending"
            if record.state == MeshTaskState.WAITING_APPROVAL.value
            else "not_required"
        )
        return {
            "task_id": record.task_id,
            "description": record.description,
            "state": record.state,
            "origin_device_id": record.origin_device_id,
            "origin_device_name": (origin or {}).get(
                "device_name", record.origin_device_id
            ),
            "assigned_device_id": record.assigned_device_id,
            "assigned_device_name": (assigned or {}).get(
                "device_name", record.assigned_device_id
            ),
            "approval_device_id": record.approval_device_id,
            "approval_device_name": (approval or {}).get(
                "device_name", record.approval_device_id
            ),
            "approval_state": approval_state,
            "workflow_type": workflow_type,
            "workflow_display_name": payload.get("workflow_display_name")
            or workflow.display_name,
            "requires_approval": bool(payload.get("requires_approval")),
            "context_notes": payload.get("context_notes"),
            "result": record.result,
            "result_preview": payload.get("result_preview")
            or (record.result or "").strip()[:180],
            "result_kind": payload.get("result_kind", "mesh_result"),
            "retries": record.retries,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "archived_at": record.archived_at,
            "archive_bucket": record.archive_bucket,
            "archive_reason": record.archive_reason,
            "replayed_to_your_day": bool(payload.get("replayed_to_your_day")),
            "your_day_item_id": payload.get("your_day_item_id"),
            "payload": payload,
        }

    task = _find_owner_mesh_task_row(task_id)
    if task is None:
        archive = _owner_mesh_archive_payload(limit=500)
        for group in archive.get("by_workflow", []):
            for archived_task in group.get("tasks", []):
                if archived_task.get("task_id") == task_id:
                    task = archived_task
                    break
            if task is not None:
                break
    return task


def _owner_mesh_archive_payload(limit: int = 200) -> dict:
    if _owner_mesh is None:
        return {"count": 0, "by_workflow": [], "by_device": []}

    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None or not hasattr(task_engine, "list_archive"):
        return {"count": 0, "by_workflow": [], "by_device": []}

    records = task_engine.list_archive(limit)
    rows = []
    for record in records:
        origin = (
            _registry.get_by_device_id(record.origin_device_id)
            if _registry is not None
            else None
        )
        assigned = (
            _registry.get_by_device_id(record.assigned_device_id)
            if _registry is not None and record.assigned_device_id
            else None
        )
        payload = record.payload or {}
        workflow_type = normalize_workflow_type(payload.get("workflow_type"))
        workflow = get_workflow_definition(workflow_type)
        rows.append(
            {
                "task_id": record.task_id,
                "description": record.description,
                "state": record.state,
                "origin_device_id": record.origin_device_id,
                "origin_device_name": (origin or {}).get(
                    "device_name", record.origin_device_id
                ),
                "assigned_device_id": record.assigned_device_id,
                "assigned_device_name": (assigned or {}).get(
                    "device_name", record.assigned_device_id or "Unassigned"
                ),
                "workflow_type": workflow_type,
                "workflow_display_name": payload.get("workflow_display_name")
                or workflow.display_name,
                "result_preview": payload.get("result_preview")
                or (record.result or "").strip()[:180],
                "archived_at": record.archived_at,
                "archive_reason": record.archive_reason or "manual",
                "replayed_to_your_day": bool(payload.get("replayed_to_your_day")),
            }
        )

    def _group(rows_to_group: list[dict], key_name: str, label_name: str) -> list[dict]:
        groups: dict[str, dict] = {}
        for row in rows_to_group:
            key = row[key_name]
            group = groups.setdefault(
                key,
                {
                    "key": key,
                    "label": row[label_name],
                    "count": 0,
                    "tasks": [],
                },
            )
            group["count"] += 1
            group["tasks"].append(row)
        return sorted(
            groups.values(), key=lambda item: (-item["count"], item["label"].lower())
        )

    return {
        "count": len(rows),
        "by_workflow": _group(rows, "workflow_type", "workflow_display_name"),
        "by_device": _group(rows, "assigned_device_id", "assigned_device_name"),
    }


def _serialize_archive_export(records, backup_exported_at: str) -> dict:
    tasks = []
    for record in records:
        payload = record.payload or {}
        tasks.append(
            {
                "task_id": record.task_id,
                "description": record.description,
                "state": record.state,
                "origin_device_id": record.origin_device_id,
                "origin_device_name": (
                    _registry.get_by_device_id(record.origin_device_id) or {}
                ).get(
                    "device_name",
                    record.origin_device_id,
                )
                if _registry is not None
                else record.origin_device_id,
                "assigned_device_id": record.assigned_device_id,
                "assigned_device_name": (
                    (_registry.get_by_device_id(record.assigned_device_id) or {}).get(
                        "device_name",
                        record.assigned_device_id,
                    )
                    if _registry is not None and record.assigned_device_id
                    else record.assigned_device_id
                ),
                "approval_device_id": record.approval_device_id,
                "workflow_type": normalize_workflow_type(payload.get("workflow_type")),
                "workflow_display_name": payload.get("workflow_display_name")
                or get_workflow_definition(payload.get("workflow_type")).display_name,
                "payload": payload,
                "result": record.result,
                "retries": record.retries,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "archived_at": record.archived_at,
                "archive_bucket": record.archive_bucket,
                "archive_reason": record.archive_reason,
                "backup_exported_at": backup_exported_at,
            }
        )
    return {
        "generated_at": backup_exported_at,
        "task_count": len(tasks),
        "tasks": tasks,
    }


def _m_peer_task_rows(limit: int = 10) -> list[dict]:
    if _registry is None or not hasattr(_registry, "list_peer_task_logs"):
        return []
    peers = {}
    if hasattr(_registry, "list_peer_agents"):
        for peer in _registry.list_peer_agents():
            peers[peer.get("peer_agent_id")] = peer.get("display_name") or peer.get(
                "peer_agent_id"
            )
    local_id = getattr(_mesh_identity, "device_id", None)
    rows = []
    for task in _registry.list_peer_task_logs()[:limit]:
        requesting_id = task.get("requesting_peer_id")
        requesting_name = peers.get(requesting_id, requesting_id)
        if local_id and requesting_id == local_id:
            requesting_name = "Local AIDE"
        responding_id = task.get("responding_peer_id")
        responding_name = peers.get(responding_id, responding_id)
        if local_id and responding_id == local_id:
            responding_name = "Local AIDE"
        rows.append(
            {
                "request_id": task.get("request_id"),
                "requesting_peer_id": requesting_id,
                "requesting_peer_name": requesting_name,
                "responding_peer_id": responding_id,
                "responding_peer_name": responding_name,
                "task_type": task.get("task_type"),
                "title": task.get("title"),
                "current_state": task.get("current_state"),
                "requested_at": task.get("requested_at"),
                "accepted_at": task.get("accepted_at"),
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "requesting_approval_state": task.get("requesting_approval_state"),
                "responding_approval_state": task.get("responding_approval_state"),
                "result_type": task.get("result_type"),
                "result_summary": task.get("result_summary"),
                "execution_time_ms": task.get("execution_time_ms"),
                "can_cancel": task.get("current_state")
                not in {"completed", "rejected", "failed", "cancelled"},
            }
        )
    return rows


def _m_peer_api_payload() -> dict:
    return {
        "available": _registry is not None and hasattr(_registry, "list_peer_agents"),
        "intro": {
            "headline": "Sovereign peer agents are not your devices.",
            "body": "Owner Mesh covers your own trusted devices. M-Peer covers another person's AIDE, with explicit scopes and revocable collaboration.",
        },
        "onboarding_steps": [
            "Share your invitation or paste theirs.",
            "Pick a plain-language trust template.",
            "Start with the smallest relationship that fits.",
            "Pause or revoke the relationship any time.",
        ],
        "safe_defaults": [
            "Explicit context only by default",
            "No automatic memory sharing",
            "Revocation ends live peer trust",
        ],
        "scope_templates": _m_peer_scope_templates(),
        "invitation": _m_peer_invitation_payload(),
        "peers": _m_peer_rows(),
        "recent_tasks": _m_peer_task_rows(),
    }


def _resolve_m_peer(peer_agent_id: str) -> dict | None:
    if _registry is None or not hasattr(_registry, "get_peer_agent"):
        return None
    return _registry.get_peer_agent(peer_agent_id)


def _m_peer_owner_name() -> str:
    if _mesh_identity is None:
        return ""
    name = getattr(_mesh_identity, "device_name", "") or ""
    if "'s " in name:
        return name.split("'s ", 1)[0]
    if name.endswith("'s"):
        return name[:-2]
    return name


def _m_peer_scope_copy(scope_record: dict) -> dict:
    scope_name = (
        scope_record.get("scope_name")
        or scope_record.get("trust_scope_id")
        or "This template"
    )
    task_types = _json_text_list(scope_record.get("allowed_task_types"))
    context_types = _json_text_list(scope_record.get("allowed_context_types"))
    receives_context = (
        scope_record.get("can_receive_context") or "explicit_only"
    ).replace("_", " ")
    risk = (scope_record.get("risk_level") or "low").lower()
    requires_approval = bool(scope_record.get("requires_local_approval")) or bool(
        scope_record.get("can_request_approvals")
    )
    return {
        "plain_summary": f"{scope_name} lets this peer collaborate in a limited, visible way.",
        "allows_summary": (
            "This peer can ask for: " + ", ".join(task_types)
            if task_types
            else "This peer has no active task permissions yet."
        ),
        "context_summary": (
            f"This peer only receives {receives_context} context using: "
            + ", ".join(context_types)
            if context_types
            else f"This peer only receives {receives_context} context."
        ),
        "memory_summary": (
            "Your wider memory does not cross this boundary automatically."
            if not scope_record.get("can_receive_memory")
            else "This template allows scoped memory sharing."
        ),
        "approval_summary": (
            "Sensitive or coordination-heavy work can require approval."
            if requires_approval
            else "Routine low-risk requests can run without an extra approval step."
        ),
        "change_later_summary": (
            "You can rename, pause, re-template, or revoke this relationship later."
            if risk in {"low", "medium", "high"}
            else "You can edit this relationship later."
        ),
    }


def _m_peer_scope_templates() -> list[dict]:
    if _registry is None or not hasattr(_registry, "list_scope_templates"):
        return []
    return [
        {**template, **_m_peer_scope_copy(template)}
        for template in _registry.list_scope_templates()
    ]


def _json_text_list(raw_value) -> list[str]:
    try:
        parsed = json.loads(raw_value or "[]")
        if isinstance(parsed, list):
            return [str(item).replace("_", " ") for item in parsed]
    except Exception:
        pass
    return []


def _qr_data_uri(raw_payload: str) -> str:
    qr = qrcode.QRCode(version=None, box_size=8, border=3)
    qr.add_data(raw_payload)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )


def _m_peer_invitation_payload() -> dict:
    if _mesh_identity is None:
        return {"available": False}
    raw_payload = build_peer_invitation_payload(
        _mesh_identity,
        display_name=f"{_m_peer_owner_name() or _mesh_identity.device_name}'s AIDE",
        owner_name=_m_peer_owner_name(),
        primary_device_id=_mesh_identity.device_id,
        capabilities=["peer_tasks", "updates", "results"],
        agent_version="m-peer-phase3",
    )
    return {
        "available": True,
        "peer_agent_id": _mesh_identity.device_id,
        "display_name": f"{_m_peer_owner_name() or _mesh_identity.device_name}'s AIDE",
        "owner_name": _m_peer_owner_name(),
        "payload": raw_payload,
        "qr_data_uri": _qr_data_uri(raw_payload),
    }


def _local_peer_requester_id() -> str:
    return getattr(_mesh_identity, "device_id", "local_aide")


def _peer_target_device_id(peer_agent_id: str) -> str | None:
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        return None
    target = peer.get("primary_device_id") or ""
    if not target:
        return None
    if hasattr(_registry, "device_exists") and not _registry.device_exists(target):
        return None
    return target


def _build_m_peer_context_payload(context_type: str, raw_text: str | None) -> dict:
    text = (raw_text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    key = "facts" if context_type == "explicit_facts" else "notes"
    return {key: [text] if key == "facts" else text}


def _scope_requires_requester_approval(
    scope_record: dict, explicit_override: bool | None
) -> bool:
    if explicit_override is not None:
        return explicit_override
    return False


async def _send_outbound_m_peer_task(
    *,
    request_id: str,
    peer: dict,
    payload: dict,
    requester_approval_state: str,
) -> dict:
    if _router is None or _mesh_identity is None:
        raise HTTPException(status_code=503, detail="M-Peer routing is not ready yet.")
    target_device_id = _peer_target_device_id(peer["peer_agent_id"])
    if not target_device_id:
        raise HTTPException(
            status_code=400, detail="Peer does not have a routable primary device yet."
        )

    response = await _router.send_with_response(
        to_device_id=target_device_id,
        message_type="PEER_TASK_REQUEST",
        payload=payload,
        from_device_id=_mesh_identity.device_id,
    )
    if not response:
        _registry.update_peer_task_log_state(
            request_id,
            current_state="failed",
            requesting_approval_state=requester_approval_state,
            result_summary="Peer task delivery failed.",
        )
        return {"status": "failed", "error": "Peer task delivery failed."}

    status = response.get("status", "")
    common_updates = {"requesting_approval_state": requester_approval_state}
    if status == "awaiting_local_approval":
        _registry.update_peer_task_log_state(
            request_id,
            current_state="pending_remote_approval",
            responding_approval_state="pending",
            result_summary="Waiting for the peer owner's approval.",
            **common_updates,
        )
    elif status == "completed":
        _registry.update_peer_task_log_state(
            request_id,
            current_state="completed",
            responding_approval_state="approved",
            result_type=response.get("result_type", "text_reply"),
            result_summary=response.get("result_summary", ""),
            result_data=response.get("result_data") or {},
            **common_updates,
        )
    elif status == "rejected":
        _registry.update_peer_task_log_state(
            request_id,
            current_state="rejected",
            responding_approval_state="denied",
            rejection_reason=response.get("error", "Peer rejected the task."),
            result_summary=response.get("error", "Peer rejected the task."),
            **common_updates,
        )
    else:
        _registry.update_peer_task_log_state(
            request_id,
            current_state=status or "accepted",
            responding_approval_state="not_required",
            result_summary=response.get("result_summary"),
            result_data=response.get("result_data") or {},
            **common_updates,
        )
    return response


def _schedule_m_peer_dispatch(coro) -> None:
    task = asyncio.create_task(coro)
    _m_peer_dispatch_tasks.add(task)

    def _cleanup(done_task):
        _m_peer_dispatch_tasks.discard(done_task)

    task.add_done_callback(_cleanup)


def _mesh_discovery_rows() -> list[dict]:
    if _mesh_discovery is None:
        return []
    rows = []
    for peer in _mesh_discovery.get_peers():
        device_id = peer.get("agent_id", "")
        known = bool(_registry and _registry.device_exists(device_id))
        trusted = bool(_registry and _registry.is_trusted(device_id))
        record = _registry.get_by_device_id(device_id) if known and _registry else None
        rows.append(
            {
                "device_id": device_id,
                "address": peer.get("address", ""),
                "port": peer.get("port"),
                "known": known,
                "trusted": trusted,
                "device_name": (record or {}).get("device_name", ""),
            }
        )
    rows.sort(
        key=lambda row: (not row["trusted"], row["device_name"] or row["device_id"])
    )
    return rows


def _mesh_pairing_payload() -> dict:
    if _mesh_identity is None:
        return {"available": False}
    raw_payload = build_pairing_payload(_mesh_identity)
    qr_image = generate_pairing_qr(_mesh_identity)
    buffer = BytesIO()
    qr_image.save(buffer, format="PNG")
    qr_data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
        "ascii"
    )
    return {
        "available": True,
        "device_id": _mesh_identity.device_id,
        "device_name": _mesh_identity.device_name,
        "device_type": _mesh_identity.device_type.value,
        "payload": raw_payload,
        "qr_data_uri": qr_data_uri,
    }


def _sync_mesh_node_trust(device_id: str) -> None:
    if _registry is None or _mesh_node is None:
        return
    if not _registry.is_trusted(device_id):
        _mesh_node.revoke_peer(device_id)
        return
    record = _registry.get_by_device_id(device_id) or {}
    public_key = record.get("public_key")
    if not public_key:
        return
    try:
        _mesh_node.trust_peer(device_id, bytes.fromhex(public_key))
    except Exception as exc:
        logger.warning(f"Could not sync mesh trust for {device_id}: {exc}")


def _find_tool(name: str):
    if _agent is None or not hasattr(_agent, "_tools"):
        return None
    return _agent._tools.get(name)


def _draft_send_payload(item: dict) -> dict:
    content = item.get("content", {})
    return {
        "to": (content.get("to") or "").strip(),
        "subject": (content.get("subject") or "").strip(),
        "body": (content.get("body") or "").strip(),
        "account": (content.get("account") or "").strip(),
    }


def _item_email_locator(item: dict) -> tuple[str, str]:
    content = item.get("content", {})
    return (
        str(content.get("uid", "")).strip(),
        str(content.get("account", "")).strip(),
    )


def _thread_preview(message: dict) -> dict:
    return {
        "uid": message.get("uid", ""),
        "from": message.get("from", ""),
        "subject": message.get("subject", ""),
        "date": message.get("date", ""),
        "snippet": message.get("snippet", "")[:280],
    }


def _resolve_source_email_item(item: dict) -> tuple[str | None, dict | None]:
    if item.get("type") == "email_digest":
        return item.get("id"), item
    source_item_id = item.get("content", {}).get("source_item_id")
    if not source_item_id:
        return None, None
    _, source_item = get_daily_brief_item(source_item_id, target_date=date.today())
    return source_item_id, source_item


async def _ask_aide_about_item(item: dict, question: str | None) -> str:
    prompt_question = (
        question or "What should I know about this item, and what should I do next?"
    ).strip()
    item_context = json.dumps(item, indent=2)
    prompt = (
        "You are AIDE helping the owner act on a specific Your Day item.\n"
        "Answer clearly and concretely in 2-4 plain sentences.\n"
        "Use only the provided item context.\n"
        "If details are missing, say what is missing without inventing facts.\n\n"
        f"Item context:\n{item_context}\n\n"
        f"Owner question: {prompt_question}"
    )
    llm = _brief_llm()
    result = await llm.chat([{"role": "user", "content": prompt}])
    if isinstance(result, dict):
        content = result.get("content") or result.get("choices", [{}])
        if isinstance(content, list):
            block = content[0]
            return (
                block.get("text") or block.get("message", {}).get("content", "")
            ).strip()
        return str(content).strip()
    return str(result).strip()


def _your_day_response(*, include_controls: bool = True) -> dict:
    brief = _current_brief_payload()
    delegated_items = []
    if brief is not None:
        delegated_items = [
            item
            for item in brief.get("items", [])
            if item.get("content", {}).get("source") == "owner_mesh"
        ]
    return {
        "date": date.today().isoformat(),
        "available": brief is not None,
        "brief": brief,
        "delegated_work": delegated_items,
        "mesh_attention": _mesh_attention_rows(),
        "calendar_status": get_calendar_source_status(date.today()),
        "capabilities": {
            "can_refresh": include_controls and _your_day_service is not None,
            "can_approve": include_controls and _safety is not None,
        },
    }


def _model_diagnostics_payload() -> dict:
    router = (
        _router
        if _router is not None and hasattr(_router, "recent_diagnostics")
        else None
    )
    if (
        router is None
        and _agent is not None
        and hasattr(_agent, "_llm")
        and hasattr(_agent._llm, "_router")
    ):
        router = _agent._llm._router
    if router is None:
        return {"available": False, "detail": "Router not ready yet."}
    payload = router.recent_diagnostics()
    payload["available"] = True
    return payload


@app.get("/", response_class=HTMLResponse)
async def index():
    if not onboarding_complete():
        try:
            return HTMLResponse(
                content=Path("interface/static/onboarding.html").read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            pass
    return HTMLResponse(content=CHAT_HTML)


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page():
    try:
        return HTMLResponse(
            content=Path("interface/static/onboarding.html").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return HTMLResponse(content="<h1>AIDE setup page not found.</h1>", status_code=404)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    try:
        return HTMLResponse(
            content=Path("interface/static/settings.html").read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return HTMLResponse(content="<h1>AIDE settings page not found.</h1>", status_code=404)


@app.get("/your-day", response_class=HTMLResponse)
async def your_day_page():
    return HTMLResponse(content=YOUR_DAY_HTML)


@app.get("/mesh", response_class=HTMLResponse)
async def mesh_page():
    return HTMLResponse(content=MESH_HTML)


@app.get("/m-peer", response_class=HTMLResponse)
async def m_peer_page():
    return HTMLResponse(content=M_PEER_HTML)


@app.get("/fit-genie", response_class=HTMLResponse)
async def fit_genie_page():
    return HTMLResponse(
        content="""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Fit Genie Coming Soon</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f8fb;color:#162033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}.panel{width:min(560px,calc(100% - 32px));background:white;border:1px solid #d8e0ea;border-radius:8px;padding:32px;box-shadow:0 24px 70px rgba(21,32,51,.12)}h1{margin:0 0 10px;font-size:30px}.muted{color:#667085;line-height:1.5}.actions{margin-top:24px;display:flex;gap:10px;flex-wrap:wrap}a{border-radius:8px;padding:11px 14px;text-decoration:none;font-weight:800;font-size:14px}.primary{background:#0b63ce;color:white}.secondary{background:#eef4fb;color:#18314f}</style></head><body><main class="panel"><h1>Fit Genie is coming soon.</h1><p class="muted">Health and fitness coaching is excluded from the MVP shipping build. AIDE will keep this module disabled until it is ready for non-technical users.</p><div class="actions"><a class="primary" href="/settings">Open Settings</a><a class="secondary" href="/your-day">Back to Your Day</a></div></main></body></html>"""
    )


@app.get("/finance", response_class=HTMLResponse)
@app.get("/finance/{month_key}", response_class=HTMLResponse)
async def finance_page(month_key: str | None = None):
    try:
        with open("interface/static/finance.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Finance page not found.</h1>", status_code=404)


@app.get("/models", response_class=HTMLResponse)

async def models_page():
    return HTMLResponse(content=MODELS_HTML)


@app.get("/api/your-day")
async def get_your_day():
    return _your_day_response()


@app.get("/api/setup/status")
async def get_setup_status():
    return {
        "ok": True,
        "onboarding_complete": onboarding_complete(),
        **provider_status(),
    }


@app.post("/api/setup/llm")
async def save_setup_llm(req: SetupLLMRequest):
    allowed = {"groq", "gemini", "openai", "ollama"}
    default_llm = req.default_llm if req.default_llm in allowed else "groq"
    updates = {"DEFAULT_LLM": default_llm}
    if req.groq_api_key.strip():
        updates["GROQ_API_KEY"] = req.groq_api_key.strip()
    if req.gemini_api_key.strip():
        updates["GEMINI_API_KEY"] = req.gemini_api_key.strip()
    if req.openai_api_key.strip():
        updates["OPENAI_API_KEY"] = req.openai_api_key.strip()
    if req.telegram_bot_token.strip():
        updates["TELEGRAM_BOT_TOKEN"] = req.telegram_bot_token.strip()
    write_env_values(updates)
    _save_agent_name(req.agent_name)
    for env_key, attr in {
        "DEFAULT_LLM": "default_llm",
        "GROQ_API_KEY": "groq_api_key",
        "GEMINI_API_KEY": "gemini_api_key",
        "OPENAI_API_KEY": "openai_api_key",
        "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    }.items():
        if env_key in updates:
            setattr(settings, attr, updates[env_key])
    return {"ok": True, **provider_status()}


@app.get("/api/settings/integrations")
async def get_integration_settings():
    status = provider_status()
    return {
        "ok": True,
        "integrations": status.get("integrations", {}),
        "values": status.get("integration_values", {}),
    }


@app.put("/api/settings/integrations")
async def update_integration_settings(req: IntegrationSettingsRequest):
    tier = req.email_safety_tier if req.email_safety_tier in {"approve", "notify", "autonomous"} else "approve"
    updates = {
        "EMAIL_ADDRESS": req.email_address.strip(),
        "IMAP_HOST": req.imap_host.strip() or "imap.gmail.com",
        "IMAP_PORT": str(req.imap_port or 993),
        "SMTP_HOST": req.smtp_host.strip() or "smtp.gmail.com",
        "SMTP_PORT": str(req.smtp_port or 587),
        "EMAIL_SAFETY_TIER": tier,
        "GOOGLE_CALENDAR_CREDENTIALS_PATH": req.google_calendar_credentials_path.strip()
        or "./data/google/calendar_credentials.json",
        "GOOGLE_CALENDAR_TOKEN_PATH": req.google_calendar_token_path.strip()
        or "./data/google/calendar_token.json",
        "GOOGLE_CALENDAR_ID": req.google_calendar_id.strip() or "primary",
        "GOOGLE_CALENDAR_LABEL": req.google_calendar_label.strip() or "Google Calendar",
        "GOOGLE_CALENDAR_TIMEZONE": req.google_calendar_timezone.strip() or "UTC",
        "CALENDAR_ACCOUNT_GOOGLE_PRIMARY_PROVIDER": "google",
        "CALENDAR_ACCOUNT_GOOGLE_PRIMARY_GOOGLE_CALENDAR_ID": req.google_calendar_id.strip() or "primary",
        "CALENDAR_ACCOUNT_GOOGLE_PRIMARY_LABEL": req.google_calendar_label.strip() or "Google Calendar",
        "CALENDAR_ACCOUNT_GOOGLE_PRIMARY_TIMEZONE": req.google_calendar_timezone.strip() or "UTC",
        "CALENDAR_ICS_PATH": req.calendar_ics_path.strip(),
        "CALENDAR_ICS_LABEL": req.calendar_ics_label.strip() or "Local Calendar",
        "CALENDAR_ICS_TIMEZONE": req.calendar_ics_timezone.strip() or "UTC",
        "CALENDAR_ACCOUNT_LOCAL_ICS_PATH": req.calendar_ics_path.strip(),
        "CALENDAR_ACCOUNT_LOCAL_LABEL": req.calendar_ics_label.strip() or "Local Calendar",
        "CALENDAR_ACCOUNT_LOCAL_TIMEZONE": req.calendar_ics_timezone.strip() or "UTC",
    }
    if req.email_password.strip():
        updates["EMAIL_PASSWORD"] = req.email_password.strip()
    write_env_values(updates)
    for env_key, attr in {
        "EMAIL_ADDRESS": "email_address",
        "EMAIL_PASSWORD": "email_password",
        "IMAP_HOST": "imap_host",
        "IMAP_PORT": "imap_port",
        "SMTP_HOST": "smtp_host",
        "SMTP_PORT": "smtp_port",
        "EMAIL_SAFETY_TIER": "email_safety_tier",
        "GOOGLE_CALENDAR_CREDENTIALS_PATH": "google_calendar_credentials_path",
        "GOOGLE_CALENDAR_TOKEN_PATH": "google_calendar_token_path",
        "GOOGLE_CALENDAR_ID": "google_calendar_id",
        "GOOGLE_CALENDAR_LABEL": "google_calendar_label",
        "GOOGLE_CALENDAR_TIMEZONE": "google_calendar_timezone",
        "CALENDAR_ICS_PATH": "calendar_ics_path",
        "CALENDAR_ICS_LABEL": "calendar_ics_label",
        "CALENDAR_ICS_TIMEZONE": "calendar_ics_timezone",
    }.items():
        if env_key in updates:
            value = updates[env_key]
            if attr.endswith("_port"):
                value = int(value)
            setattr(settings, attr, value)
    status = provider_status()
    return {
        "ok": True,
        "integrations": status.get("integrations", {}),
        "values": status.get("integration_values", {}),
    }


@app.post("/api/setup/complete")
async def complete_setup():
    from onboarding.storage import OnboardingData

    data = OnboardingData.load()
    data.onboarding_completed = True
    data.completed_at = datetime.now()
    data.save()
    return {"ok": True, "onboarding_complete": True}


def _save_agent_name(value: str) -> None:
    from onboarding.storage import OnboardingData

    name = (value or "").strip() or "AIDE"
    legacy_name = "".join(["v", "e", "r", "a"])
    if name.lower() in {"aide", legacy_name}:
        name = "AIDE"
    data = OnboardingData.load()
    data.operator_name = name
    data.operator_name_customized = name != "AIDE"
    data.save()


@app.get("/api/settings/modules")
async def get_settings_modules():
    return {"ok": True, "modules": module_payload()}


@app.put("/api/settings/modules")
async def update_settings_modules(req: ModuleSettingsRequest):
    save_module_settings(req.modules)
    return {"ok": True, "modules": module_payload()}


@app.get("/api/fitness-os/state")
async def get_fitness_os_state():
    raise HTTPException(status_code=410, detail="Fit Genie is coming soon.")


@app.post("/api/fitness-os/dispatch")
async def dispatch_fitness_os(req: FitnessOSDispatchRequest):
    raise HTTPException(status_code=410, detail="Fit Genie is coming soon.")


@app.get("/api/fitness-os/recommendations")
async def get_fitness_os_recommendations():
    raise HTTPException(status_code=410, detail="Fit Genie is coming soon.")


@app.get("/api/finance/months")
async def list_finance_months():
    import sqlite3

    try:
        with sqlite3.connect(_finance_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT month_key, COUNT(*) AS count
                FROM transactions
                GROUP BY month_key
                ORDER BY month_key DESC
                """
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    return {"ok": True, "months": [dict(row) for row in rows]}


def _finance_db_path() -> str:
    memory = getattr(_agent, "_memory", None)
    return getattr(memory, "_db_path", str(settings.memory_db_path))


@app.get("/api/finance/categories")
async def get_finance_categories():
    return {"ok": True, "categories": FinanceEngine(_finance_db_path()).categories()}


@app.get("/api/finance/review")
async def get_finance_review_queue(month_key: str | None = None):
    engine = FinanceEngine(_finance_db_path())
    return {
        "ok": True,
        "categories": engine.categories(),
        "transactions": engine.review_queue(month_key=month_key),
    }


@app.post("/api/finance/transactions/{transaction_id}/category")
async def update_finance_transaction_category(
    transaction_id: str,
    request: FinanceCategoryUpdateRequest,
):
    try:
        result = FinanceEngine(_finance_db_path()).update_transaction_category(
            transaction_id,
            request.category,
            learn_rule=request.learn_rule,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/finance/analysis/summary")
async def get_finance_analysis_summary(
    period: str = "month",
    month_key: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    try:
        summary = FinanceEngine(_finance_db_path()).summary_for_period(
            period,
            month_key=month_key,
            start=start,
            end=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "summary": summary}


@app.get("/api/finance/analysis/report")
async def get_finance_analysis_report(
    period: str = "all",
    month_key: str | None = None,
    start: str | None = None,
    end: str | None = None,
):
    engine = FinanceEngine(_finance_db_path())
    try:
        comparison = engine.comparison_for_period(
            period,
            month_key=month_key,
            start=start,
            end=end,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reporter = FinanceReportGenerator(_finance_db_path())
    return {
        "ok": True,
        "comparison": comparison,
        "insights": engine.dashboard_insights_for_period(
            comparison,
            month_key=month_key,
        ),
        "telegram": reporter.telegram_summary(comparison),
        "markdown": reporter.markdown_report(comparison),
    }


@app.get("/api/finance/{month_key}")
async def get_finance_month(month_key: str):
    report = FinanceReportGenerator(_finance_db_path()).monthly_report(
        month_key,
        save=False,
    )
    return {"ok": True, **report}


@app.post("/api/finance/upload")
async def upload_finance_statement(
    file: UploadFile = File(...),
    bank_name: str = "generic",
    hard_refresh: bool = False,
):
    upload_dir = Path.home() / ".aide" / "uploads" / "finance"
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination = upload_dir / (
        f"{datetime.now().strftime('%Y%m%d%H%M%S')}-{file.filename or 'statement.pdf'}"
    )
    destination.write_bytes(await file.read())
    result = await FinanceIngestor(_finance_db_path()).ingest_pdf(
        destination,
        bank_name,
        hard_refresh=hard_refresh,
        original_filename=file.filename or destination.name,
        rewrite_reports=True,
    )
    return {"ok": True, **result}


@app.get("/api/finance/chat/history")
async def get_finance_chat_history(month_key: str | None = None):
    history = FinanceChatService(_finance_db_path()).history(month_key=month_key)
    return {"ok": True, "messages": history}


@app.post("/api/finance/chat")
async def finance_chat(request: FinanceChatRequest):
    try:
        result = await FinanceChatService(_finance_db_path()).ask(
            request.message,
            month_key=request.month_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.get("/api/mesh/peers")
async def get_mesh_peers():
    return _mesh_api_payload()


@app.get("/api/mesh/devices")
async def get_mesh_devices():
    if _registry is None:
        raise HTTPException(status_code=503, detail="Registry not ready.")

    device_map = {}
    for row in _registry.list_all():
        device_id = row["device_id"]
        if device_id not in device_map:
            device_map[device_id] = {
                "device_id": device_id,
                "device_name": row.get("device_name", ""),
                "mesh_host": row.get("mesh_host"),
                "mesh_port": row.get("mesh_port"),
                "telegram_chat_id": row.get("telegram_chat_id"),
                "is_trusted": _registry.is_trusted(device_id),
                "device_type": row.get("device_type", ""),
            }
        else:
            # Merge transports if the device appears multiple times
            if row.get("mesh_host"):
                device_map[device_id]["mesh_host"] = row["mesh_host"]
                device_map[device_id]["mesh_port"] = row["mesh_port"]
            if row.get("telegram_chat_id"):
                device_map[device_id]["telegram_chat_id"] = row["telegram_chat_id"]

    return list(device_map.values())


@app.post("/api/mesh/devices/{device_id}/bind-mesh")
async def bind_mesh_device(device_id: str, req: MeshManualBindRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Registry not ready.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Device not found.")
    host = req.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="Peer IP or host cannot be empty.")
    if req.port <= 0 or req.port > 65535:
        raise HTTPException(
            status_code=400, detail="Peer port must be between 1 and 65535."
        )
    _registry.bind_mesh(device_id, host, req.port)
    _sync_mesh_node_trust(device_id)
    if (
        _mesh_node is not None
        and hasattr(_mesh_node, "ensure_outbound_session")
        and _registry.is_trusted(device_id)
    ):
        await _mesh_node.ensure_outbound_session(
            {
                "agent_id": device_id,
                "address": host,
                "port": req.port,
            }
        )
    return {"ok": True}


@app.post("/api/mesh/devices/{device_id}/bind-telegram")
async def bind_telegram_device(device_id: str, req: MeshTelegramBindRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Registry not ready.")

    chat_id_str = req.chat_id.strip()
    if not chat_id_str:
        raise HTTPException(status_code=400, detail="Chat ID cannot be empty.")

    try:
        chat_id_int = int(chat_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Chat ID must be a valid integer.")

    # Note: This is a manual override. Normally /pair handles this.
    _registry.bind_telegram(device_id, chat_id_int, "Manual Bind")
    return {"ok": True}


@app.post("/api/mesh/devices/{device_id}/trust")
async def toggle_device_trust(device_id: str, req: MeshTrustRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Registry not ready.")

    device = _registry.get_by_device_id(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found.")

    if req.trusted:
        # 1. Update the persistent registry
        _registry.restore_trust(device_id, trust_source="web_ui")

        # 2. Update the active mesh node's trust list
        if _mesh_node:
            pub_key = device.get("public_key")
            if pub_key:
                _mesh_node.trust_peer(device_id, bytes.fromhex(pub_key))
            else:
                logger.warning(
                    f"Could not trust {device_id} in MeshNode: no public key"
                )
        return {"ok": True}

    else:
        # 1. Update the persistent registry
        _registry.revoke_trust(device_id, note="Revoked via web UI")

        # 2. Update the active mesh node's trust list
        if _mesh_node:
            _mesh_node.revoke_peer(device_id)
            return {"ok": True}

        raise HTTPException(status_code=503, detail="Mesh node not ready.")


@app.post("/api/mesh/devices/{device_id}/ping")
async def ping_device(device_id: str, req: MeshPingRequest):
    if _router is None:
        raise HTTPException(status_code=503, detail="Router not ready.")
    # We send a PING intent and wait for a response
    result = await _router.send_with_response(
        to_device_id=device_id,
        message_type="PING",
        payload={},
        from_device_id=_mesh_identity.device_id if _mesh_identity else "local",
    )
    return {"status": "success" if result else "failed", "response": result}


@app.post("/api/mesh/active-peer-thread/clear")
async def clear_active_mesh_peer_thread():
    memory = getattr(_agent, "_memory", None)
    if memory is None:
        raise HTTPException(
            status_code=503, detail="Peer thread memory is not ready yet."
        )
    _clear_active_peer_thread()
    return {
        "ok": True,
        "active_peer_thread": _active_peer_thread_payload(),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.get("/api/m-peer")
async def get_m_peer():
    return _m_peer_api_payload()


@app.get("/api/m-peer/invitation")
async def get_m_peer_invitation():
    payload = _m_peer_invitation_payload()
    if not payload["available"]:
        raise HTTPException(
            status_code=503, detail="Local AIDE identity is not ready yet."
        )
    return payload


@app.post("/api/m-peer/invitations/accept")
async def accept_m_peer_invitation(req: MPeerInvitationAcceptRequest):
    if _registry is None or _mesh_identity is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    payload = req.payload.strip()
    if not payload:
        raise HTTPException(
            status_code=400, detail="Invitation payload cannot be empty."
        )
    if not hasattr(_registry, "get_trust_scope") or not _registry.get_trust_scope(
        req.trust_scope_id
    ):
        raise HTTPException(status_code=400, detail="Unknown trust template.")

    try:
        parsed = parse_peer_invitation_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    peer_agent_id = parsed["peer_agent_id"]
    if peer_agent_id == _mesh_identity.device_id:
        raise HTTPException(
            status_code=400,
            detail="You cannot connect your own AIDE as a sovereign peer.",
        )

    manager = PeerInvitationManager(_mesh_identity, device_registry=_registry)
    ok = manager.complete_invitation(
        payload,
        trust_scope_id=req.trust_scope_id,
        paired_by=_mesh_identity.device_id,
    )
    if not ok:
        raise HTTPException(
            status_code=400, detail="Invitation payload could not be accepted."
        )
    return {
        "ok": True,
        "peer": _resolve_m_peer(peer_agent_id),
        "peers": _m_peer_rows(),
        "tasks": _m_peer_task_rows(),
    }


@app.put("/api/m-peer/peers/{peer_agent_id}/name")
async def rename_m_peer(peer_agent_id: str, req: MPeerRenameRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    _registry.rename_peer_agent(peer_agent_id, new_name)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.put("/api/m-peer/peers/{peer_agent_id}/scope")
async def update_m_peer_scope(peer_agent_id: str, req: MPeerScopeUpdateRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.get_trust_scope(req.trust_scope_id):
        raise HTTPException(status_code=400, detail="Unknown trust template.")
    _registry.update_peer_agent_scope(peer_agent_id, req.trust_scope_id)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.post("/api/m-peer/peers/{peer_agent_id}/pause")
async def pause_m_peer(peer_agent_id: str, req: MPeerPauseRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.pause_peer_agent(
        peer_agent_id,
        reason=(req.reason or "Paused from the M-Peer dashboard.").strip(),
    )
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.post("/api/m-peer/peers/{peer_agent_id}/resume")
async def resume_m_peer(peer_agent_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.resume_peer_agent(peer_agent_id)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.post("/api/m-peer/peers/{peer_agent_id}/revoke")
async def revoke_m_peer(peer_agent_id: str, req: MPeerPauseRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.revoke_peer_agent(peer_agent_id)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


# ── Voice Chat Integration ──────────────────────────────────────
@app.post("/api/voice/trigger")
async def voice_chat_trigger(req: OnItSessionRequest):
    logger.info(f"Voice chat trigger received: {req.action}")
    scheduler = getattr(_agent, "_scheduler", None)
    if not scheduler:
        logger.error("Voice trigger failed: Scheduler not found on agent")
        raise HTTPException(status_code=503, detail="Scheduler not ready.")

    try:
        if req.action == "start":
            await scheduler._voice.trigger_session("start", task_name="Voice Chat")
            logger.info("Native voice session started successfully")
        elif req.action == "stop":
            await scheduler._voice.trigger_session("stop")
            logger.info("Native voice session stopped successfully")
    except Exception as e:
        logger.exception(f"Error in native voice trigger: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


@app.get("/api/voice/transcript")
async def get_last_voice_transcript():
    """Retrieve the last processed voice transcript for the chat UI."""
    # We can store the last transcript in a temporary state or memory
    last_text = _agent._memory.get_fact("last_voice_chat_input")
    return {"text": last_text or ""}


# Update the agent's voice handler to also store the text for the chat UI
# We need to override the scheduler's handler or add a hook.
# Let's modify core/scheduler.py to store the last transcript in memory.


@app.post("/api/onit/transcript/chunk")
async def onit_transcript_chunk(req: OnItTranscriptChunkRequest):
    memory = getattr(_agent, "_memory", None)
    if memory is None:
        raise HTTPException(status_code=503, detail="Memory manager not ready.")

    memory.store_transcription_chunk(req.session_id, req.timestamp, req.text)
    return {"ok": True}


@app.post("/api/m-peer/peers/{peer_agent_id}/restore")
async def restore_m_peer(peer_agent_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.restore_peer_agent(peer_agent_id)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.put("/api/m-peer/peers/{peer_agent_id}/notes")
async def update_m_peer_notes(peer_agent_id: str, req: MPeerNotesRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    notes = (req.notes or "").strip() or None
    _registry.update_peer_agent_notes(peer_agent_id, notes)
    return {"ok": True, "peer": _resolve_m_peer(peer_agent_id), "peers": _m_peer_rows()}


@app.post("/api/m-peer/peers/{peer_agent_id}/tasks")
async def create_m_peer_task(peer_agent_id: str, req: MPeerTaskCreateRequest):
    if _registry is None or _mesh_identity is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    peer = _resolve_m_peer(peer_agent_id)
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if peer.get("revoked_at"):
        raise HTTPException(status_code=400, detail="Peer has been revoked.")
    try:
        metadata = json.loads(peer.get("metadata") or "{}")
    except Exception:
        metadata = {}
    if metadata.get("paused"):
        raise HTTPException(
            status_code=400, detail="Peer collaboration is currently paused."
        )

    scope = (
        _registry.get_trust_scope(peer.get("trust_scope_id"))
        if hasattr(_registry, "get_trust_scope")
        else None
    )
    if not scope:
        raise HTTPException(status_code=400, detail="Peer trust scope is missing.")

    request_id = f"peer_req_{uuid.uuid4().hex[:10]}"
    context_payload = _build_m_peer_context_payload(req.context_type, req.context_text)
    requester_needs_approval = _scope_requires_requester_approval(
        scope, req.require_requester_approval
    )
    _registry.create_peer_task_log(
        request_id=request_id,
        requesting_peer_id=_local_peer_requester_id(),
        responding_peer_id=peer_agent_id,
        task_type=req.task_type,
        current_state="pending_requester_approval"
        if requester_needs_approval
        else "routed",
        title=req.title.strip(),
        instruction=req.instruction.strip(),
        context_provided={"context_type": req.context_type, "data": context_payload},
        requires_requesting_approval=requester_needs_approval,
        requesting_approval_state="pending"
        if requester_needs_approval
        else "not_required",
        requires_responding_approval=bool(scope.get("requires_local_approval")),
        responding_approval_state="unknown"
        if requester_needs_approval
        else "not_required",
        scope_validated=True,
    )

    payload = {
        "request_id": request_id,
        "task_type": req.task_type,
        "title": req.title.strip(),
        "instruction": req.instruction.strip(),
        "context_type": req.context_type,
        "context": context_payload,
    }

    if requester_needs_approval and _safety is not None:
        description = f"Send peer task '{req.title.strip()}' to {peer.get('display_name') or peer_agent_id}"
        action = PendingAction(
            action_id=f"outbound_{request_id}",
            tool_name="peer_task_dispatch",
            description=description,
            tier=SafetyTier.APPROVE,
            reversible=True,
            payload={"request_id": request_id, "peer_agent_id": peer_agent_id},
        )

        async def runner():
            async def executor() -> str:
                _registry.update_peer_task_log_state(
                    request_id,
                    current_state="routed",
                    requesting_approval_state="approved",
                    result_summary="Peer task approved for dispatch.",
                )
                response = await _send_outbound_m_peer_task(
                    request_id=request_id,
                    peer=peer,
                    payload=payload,
                    requester_approval_state="approved",
                )
                return response.get("result_summary") or response.get(
                    "status", "dispatched"
                )

            result = await _safety.process(action, executor)
            if result in {
                "Action cancelled.",
                "Action timed out waiting for your approval.",
            }:
                _registry.update_peer_task_log_state(
                    request_id,
                    current_state="cancelled",
                    requesting_approval_state="denied",
                    rejection_reason=result,
                    result_summary=result,
                )

        _schedule_m_peer_dispatch(runner())
        return {
            "ok": True,
            "request_id": request_id,
            "status": "awaiting_requester_approval",
            "tasks": _m_peer_task_rows(),
        }

    response = await _send_outbound_m_peer_task(
        request_id=request_id,
        peer=peer,
        payload=payload,
        requester_approval_state="not_required",
    )
    return {
        "ok": True,
        "request_id": request_id,
        "status": response.get("status", "routed"),
        "response": response,
        "tasks": _m_peer_task_rows(),
    }


@app.post("/api/m-peer/tasks/{request_id}/cancel")
async def cancel_m_peer_task(request_id: str, req: MPeerTaskCancelRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="M-Peer runtime is not ready yet.")
    task = (
        _registry.get_peer_task_log(request_id)
        if hasattr(_registry, "get_peer_task_log")
        else None
    )
    if not task:
        raise HTTPException(status_code=404, detail="Peer task not found.")
    if task.get("requesting_peer_id") != _local_peer_requester_id():
        raise HTTPException(
            status_code=400,
            detail="Only locally requested peer tasks can be cancelled here.",
        )
    if task.get("current_state") in {"completed", "rejected", "failed", "cancelled"}:
        raise HTTPException(status_code=400, detail="Peer task is already finished.")

    reason = (req.reason or "Cancelled from the M-Peer dashboard.").strip()
    responding_peer_id = task.get("responding_peer_id")
    target_device_id = (
        _peer_target_device_id(responding_peer_id) if responding_peer_id else None
    )
    if _router is not None and _mesh_identity is not None and target_device_id:
        await _router.send(
            to_device_id=target_device_id,
            message_type="PEER_TASK_CANCEL",
            payload={"request_id": request_id, "reason": reason},
            from_device_id=_mesh_identity.device_id,
        )
    _registry.update_peer_task_log_state(
        request_id,
        current_state="cancelled",
        requesting_approval_state=task.get("requesting_approval_state")
        or "not_required",
        rejection_reason=reason,
        result_summary=reason,
    )
    return {"ok": True, "request_id": request_id, "tasks": _m_peer_task_rows()}


@app.get("/api/models")
async def get_model_diagnostics():
    return _model_diagnostics_payload()


@app.get("/api/mesh/pairing")
async def get_mesh_pairing():
    payload = _mesh_pairing_payload()
    if not payload["available"]:
        raise HTTPException(status_code=503, detail="Mesh identity not ready yet.")
    return payload


@app.post("/api/mesh/pairing")
async def complete_mesh_pairing(req: MeshPairingRequest):
    if _mesh_identity is None or _registry is None:
        raise HTTPException(status_code=503, detail="Mesh pairing is not ready yet.")
    try:
        peer_data = parse_pairing_qr(req.payload.strip())
    except Exception:
        peer_data = None
    manager = PairingManager(
        _mesh_identity,
        device_registry=_registry,
        alias_registry=_alias_registry,
    )
    ok = manager.complete_pairing(req.payload.strip())
    if not ok:
        raise HTTPException(
            status_code=400, detail="Pairing payload could not be verified."
        )
    if peer_data is not None:
        _sync_mesh_node_trust(peer_data["id"])
    return {
        "ok": True,
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/revoke")
async def revoke_mesh_peer(device_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.revoke_trust(device_id, note="Trust revoked from owner mesh web console.")
    _sync_mesh_node_trust(device_id)
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/restore")
async def restore_mesh_peer(device_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.restore_trust(
        device_id,
        trust_level="trusted",
        note="Trust restored from owner mesh web console.",
        trust_source="web_console",
    )
    _sync_mesh_node_trust(device_id)
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
    }


@app.put("/api/mesh/peers/{device_id}/name")
async def rename_mesh_peer(device_id: str, req: MeshRenameRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    _registry.rename_device(device_id, new_name)
    if _alias_registry is not None:
        try:
            _alias_registry.rename(device_id, new_name)
        except Exception:
            _alias_registry.register(
                device_id=device_id,
                canonical_name=new_name,
                aliases=[new_name.lower()],
                is_default=False,
            )
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
    }


@app.put("/api/mesh/peers/{device_id}/capabilities/{capability}")
async def update_mesh_peer_capability(
    device_id: str, capability: str, req: MeshCapabilityRequest
):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    if capability not in {"can_execute", "can_receive_brief", "can_approve"}:
        raise HTTPException(
            status_code=400, detail="Capability not exposed on the mesh admin page."
        )
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.update_capabilities(device_id, **{capability: 1 if req.enabled else 0})
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/ping")
async def ping_mesh_peer(device_id: str):
    if _registry is None or _mesh_identity is None or _router is None:
        raise HTTPException(status_code=503, detail="Mesh runtime not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.is_trusted(device_id):
        raise HTTPException(status_code=400, detail="Peer is not trusted.")
    if record.get("transport_type") != "mesh" and not record.get("mesh_host"):
        raise HTTPException(status_code=400, detail="Peer is not currently mesh-bound.")
    result = await _router.send_with_response(
        to_device_id=device_id,
        message_type="PING",
        payload={"reason": "owner_mesh_dashboard_healthcheck"},
        from_device_id=_mesh_identity.device_id,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Peer did not respond to ping.")
    return {
        "ok": True,
        "result": result,
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/ask")
async def ask_mesh_peer(device_id: str, req: MeshAskPeerRequest):
    if _registry is None or _mesh_identity is None or _router is None:
        raise HTTPException(status_code=503, detail="Mesh runtime not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.is_trusted(device_id):
        raise HTTPException(status_code=400, detail="Peer is not trusted.")
    if not _registry.get_mesh_endpoint(device_id):
        raise HTTPException(status_code=400, detail="Peer is not currently mesh-bound.")
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    peer_name = record.get("device_name") or device_id
    _set_active_peer_thread(device_id, peer_name)
    correlation_id = uuid.uuid4().hex
    if hasattr(_registry, "append_peer_conversation_log"):
        try:
            _registry.append_peer_conversation_log(
                device_id,
                role="local",
                text=prompt,
                via_transport="mesh",
                correlation_id=correlation_id,
                metadata={"source": "mesh_dashboard"},
            )
        except Exception:
            logger.exception("Could not log outbound mesh dashboard question")

    result = await _router.send_with_response(
        to_device_id=device_id,
        message_type="ASK_PEER",
        payload={"prompt": prompt, "correlation_id": correlation_id},
        from_device_id=_mesh_identity.device_id,
    )
    if not result:
        raise HTTPException(status_code=400, detail="Peer did not answer.")
    if result.get("status") == "error":
        raise HTTPException(
            status_code=400, detail=result.get("error", "Peer returned an error.")
        )
    if result.get("status") != "completed":
        raise HTTPException(
            status_code=400, detail="Peer did not complete the request."
        )
    response_text = result.get("response", "")
    if hasattr(_registry, "append_peer_conversation_log"):
        try:
            _registry.append_peer_conversation_log(
                device_id,
                role="peer",
                text=response_text,
                via_transport="mesh",
                correlation_id=correlation_id,
                metadata={"source": "mesh_dashboard"},
            )
        except Exception:
            logger.exception("Could not log inbound mesh dashboard answer")

    return {
        "ok": True,
        "response": response_text,
        "result": result,
        "conversation": _peer_conversation_rows(device_id),
        "active_peer_thread": _active_peer_thread_payload(),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.get("/api/mesh/peers/{device_id}/conversation")
async def get_mesh_peer_conversation(device_id: str, limit: int = 40):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.is_trusted(device_id):
        raise HTTPException(status_code=400, detail="Peer is not trusted.")
    return {
        "ok": True,
        "peer": {
            "device_id": device_id,
            "device_name": record.get("device_name") or device_id,
        },
        "conversation": _peer_conversation_rows(device_id, limit=limit),
    }


@app.post("/api/mesh/peers/{device_id}/bind-mesh")
async def bind_mesh_peer(device_id: str, req: MeshManualBindRequest):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.is_trusted(device_id):
        raise HTTPException(status_code=400, detail="Peer is not trusted.")
    host = req.host.strip()
    if not host:
        raise HTTPException(status_code=400, detail="Peer IP or host cannot be empty.")
    if req.port <= 0 or req.port > 65535:
        raise HTTPException(
            status_code=400, detail="Peer port must be between 1 and 65535."
        )
    _registry.bind_mesh(device_id, host, req.port)
    _sync_mesh_node_trust(device_id)
    if _mesh_node is not None and hasattr(_mesh_node, "ensure_outbound_session"):
        await _mesh_node.ensure_outbound_session(
            {
                "agent_id": device_id,
                "address": host,
                "port": req.port,
            }
        )
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/unbind-mesh")
async def unbind_mesh_peer(device_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    _registry.unbind_mesh(device_id)
    if _mesh_node is not None and hasattr(_mesh_node, "clear_outbound_session"):
        await _mesh_node.clear_outbound_session(device_id)
    return {
        "ok": True,
        "peer": _registry.get_trust_record(device_id),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.post("/api/mesh/peers/{device_id}/test-task")
async def test_task_mesh_peer(device_id: str, req: MeshTestTaskRequest):
    if _owner_mesh is None or _registry is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if not _registry.is_trusted(device_id):
        raise HTTPException(status_code=400, detail="Peer is not trusted.")
    if not record.get("can_execute"):
        raise HTTPException(
            status_code=400, detail="Peer does not have executor capability."
        )
    result = await _owner_mesh.delegate_task(
        (req.task or "Return a one-line mesh health acknowledgement.").strip(),
        preferred_device_id=device_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=400, detail=result.get("error", "Mesh test task failed.")
        )
    return {
        "ok": True,
        "result": result,
        "tasks": _owner_mesh_task_rows(),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.get("/api/mesh/tasks")
async def list_mesh_tasks():
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    return {"ok": True, "tasks": _owner_mesh_task_rows()}


@app.get("/api/mesh/tasks/detail/{task_id}")
async def get_mesh_task(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task = _owner_mesh_task_detail(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"ok": True, "task": task}


@app.get("/api/mesh/tasks/archive")
async def list_archived_mesh_tasks():
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    return {"ok": True, "archive": _owner_mesh_archive_payload()}


@app.post("/api/mesh/tasks/archive/backup")
async def backup_archived_mesh_tasks():
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None or not hasattr(task_engine, "export_archive_snapshot"):
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    backup_exported_at, records = task_engine.export_archive_snapshot()
    filename = f"owner-mesh-archive-{backup_exported_at.replace(':', '-').replace('+', '_plus_')}.json"
    return {
        "ok": True,
        "backup_exported_at": backup_exported_at,
        "filename": filename,
        "export": _serialize_archive_export(records, backup_exported_at),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/archive/delete")
async def delete_archived_mesh_tasks(req: MeshArchiveDeleteRequest):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None or not hasattr(task_engine, "delete_archived_for_backup"):
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    backup_exported_at = (req.backup_exported_at or "").strip()
    if not backup_exported_at:
        raise HTTPException(
            status_code=400, detail="Backup export token is required before delete."
        )
    deleted_count = task_engine.delete_archived_for_backup(backup_exported_at)
    if deleted_count <= 0:
        raise HTTPException(
            status_code=400,
            detail="No archived tasks matched that backup export. Export the archive again before deleting.",
        )
    return {
        "ok": True,
        "deleted_count": deleted_count,
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks")
async def create_mesh_task(req: MeshDelegateTaskRequest):
    if _owner_mesh is None or _registry is None or _mesh_identity is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )

    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="Task cannot be empty.")

    target_device_id = req.target_device_id or None
    if target_device_id:
        record = _registry.get_by_device_id(target_device_id)
        if not record:
            raise HTTPException(status_code=404, detail="Target device not found.")
        if not _registry.is_trusted(target_device_id):
            raise HTTPException(status_code=400, detail="Target device is not trusted.")
        if not record.get("can_execute"):
            raise HTTPException(
                status_code=400,
                detail="Target device does not have executor capability.",
            )

    workflow_type = normalize_workflow_type(req.workflow_type)
    workflow = get_workflow_definition(workflow_type)
    payload = {
        **(req.payload or {}),
        "workflow_type": workflow.workflow_type,
        "workflow_display_name": workflow.display_name,
        "requires_approval": bool(req.requires_approval),
        "context_notes": (req.context_notes or "").strip() or None,
    }

    if req.requires_approval:
        task_engine = getattr(_owner_mesh, "_task_engine", None)
        if task_engine is None or _mesh_identity is None:
            raise HTTPException(
                status_code=503, detail="Owner mesh task engine not ready yet."
            )
        task_id = task_engine.create_task(
            description=task,
            origin_device_id=_mesh_identity.device_id,
            assigned_device_id=target_device_id,
            payload={
                **payload,
                "task": task,
                "target_device_id": target_device_id,
                "approval_state": "pending",
            },
            state=MeshTaskState.WAITING_APPROVAL,
        )
        task_rows = _owner_mesh_task_rows()
        task_row = next((row for row in task_rows if row["task_id"] == task_id), None)
        return {
            "ok": True,
            "result": {"status": "awaiting_approval", "task_id": task_id},
            "task": task_row,
            "tasks": task_rows,
            "archive": _owner_mesh_archive_payload(),
            "peers": _mesh_peer_rows(),
            "discovered": _mesh_discovery_rows(),
        }

    result = await _owner_mesh.delegate_task(
        task,
        payload=payload,
        preferred_device_id=target_device_id,
        origin_device_id=_mesh_identity.device_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=400, detail=result.get("error", "Mesh delegation failed.")
        )

    task_id = result.get("task_id")
    task_rows = _owner_mesh_task_rows()
    task_row = next((row for row in task_rows if row["task_id"] == task_id), None)
    return {
        "ok": True,
        "result": result,
        "task": task_row,
        "tasks": task_rows,
        "archive": _owner_mesh_archive_payload(),
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.delete("/api/mesh/tasks/{task_id}")
async def delete_mesh_task(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(status_code=503, detail="Owner mesh not ready.")
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        raise HTTPException(status_code=503, detail="Task engine not ready.")
    if not task_engine.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {
        "ok": True,
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }

@app.post("/api/mesh/tasks/{task_id}/approve")
async def approve_mesh_task(task_id: str):
    if _owner_mesh is None or _mesh_identity is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    record = task_engine.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.state != MeshTaskState.WAITING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="Task is not waiting for approval.")

    payload = record.payload or {}
    result = await _owner_mesh.delegate_task(
        record.description,
        payload={
            **payload,
            "approval_state": "approved",
        },
        preferred_device_id=payload.get("target_device_id"),
        origin_device_id=record.origin_device_id or _mesh_identity.device_id,
        existing_task_id=task_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=400, detail=result.get("error", "Mesh delegation failed.")
        )
    return {
        "ok": True,
        "result": result,
        "task": _find_owner_mesh_task_row(task_id),
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/{task_id}/deny")
async def deny_mesh_task(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    record = task_engine.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.state != MeshTaskState.WAITING_APPROVAL.value:
        raise HTTPException(status_code=400, detail="Task is not waiting for approval.")
    task_engine.transition(
        task_id,
        MeshTaskState.CANCELLED,
        result="Denied before execution.",
        payload_update={"approval_state": "denied"},
        archive_immediately=True,
    )
    return {
        "ok": True,
        "task": _find_owner_mesh_task_row(task_id),
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/{task_id}/retry")
async def retry_mesh_task(task_id: str):
    if _owner_mesh is None or _mesh_identity is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    record = task_engine.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.state not in {MeshTaskState.FAILED.value, MeshTaskState.CANCELLED.value}:
        raise HTTPException(
            status_code=400, detail="Only failed or cancelled tasks can be retried."
        )
    payload = record.payload or {}
    if payload.get("requires_approval"):
        task_engine.transition(
            task_id,
            MeshTaskState.WAITING_APPROVAL,
            result="",
            payload_update={"approval_state": "pending"},
        )
        return {
            "ok": True,
            "result": {"status": "awaiting_approval", "task_id": task_id},
            "task": _find_owner_mesh_task_row(task_id),
            "tasks": _owner_mesh_task_rows(),
            "archive": _owner_mesh_archive_payload(),
        }
    result = await _owner_mesh.delegate_task(
        record.description,
        payload={
            **payload,
            "approval_state": payload.get("approval_state"),
        },
        preferred_device_id=payload.get("target_device_id")
        or record.assigned_device_id,
        origin_device_id=record.origin_device_id or _mesh_identity.device_id,
        existing_task_id=task_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(
            status_code=400, detail=result.get("error", "Mesh retry failed.")
        )
    return {
        "ok": True,
        "result": result,
        "task": _find_owner_mesh_task_row(task_id),
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/{task_id}/cancel")
async def cancel_mesh_task(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    record = task_engine.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.state in {
        MeshTaskState.COMPLETED.value,
        MeshTaskState.FAILED.value,
        MeshTaskState.CANCELLED.value,
    }:
        raise HTTPException(status_code=400, detail="Task is already terminal.")
    task_engine.transition(
        task_id,
        MeshTaskState.CANCELLED,
        result="Cancelled from owner mesh dashboard.",
        payload_update={"approval_state": "cancelled"},
    )
    return {
        "ok": True,
        "task": _find_owner_mesh_task_row(task_id),
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/{task_id}/archive")
async def archive_mesh_task(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )
    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is None or not hasattr(task_engine, "archive_task"):
        raise HTTPException(
            status_code=503, detail="Owner mesh task engine not ready yet."
        )
    record = task_engine.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    if record.state not in {
        MeshTaskState.COMPLETED.value,
        MeshTaskState.FAILED.value,
        MeshTaskState.CANCELLED.value,
    }:
        raise HTTPException(
            status_code=400, detail="Only terminal tasks can be archived."
        )
    task_engine.archive_task(task_id, reason="manual")
    return {
        "ok": True,
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
    }


@app.post("/api/mesh/tasks/{task_id}/replay-to-your-day")
async def replay_mesh_task_to_your_day(task_id: str):
    if _owner_mesh is None:
        raise HTTPException(
            status_code=503, detail="Owner mesh orchestrator not ready yet."
        )

    task = _find_owner_mesh_task_row(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if task.get("state") != "completed":
        raise HTTPException(
            status_code=400,
            detail="Only completed tasks can be replayed into Your Day.",
        )

    ensure_daily_brief(date.today())
    brief_item = build_prepared_item_from_mesh_task(task)
    if not upsert_daily_brief_item(brief_item.to_dict(), target_date=date.today()):
        raise HTTPException(
            status_code=400, detail="Could not replay task into Your Day."
        )

    task_engine = getattr(_owner_mesh, "_task_engine", None)
    if task_engine is not None and hasattr(task_engine, "transition"):
        record = task_engine.get_task(task_id)
        if record is not None:
            task_engine.transition(
                task_id,
                MeshTaskState(record.state),
                payload_update={
                    "replayed_to_your_day": True,
                    "your_day_item_id": brief_item.id,
                },
            )
    else:
        task_log = getattr(_owner_mesh, "task_log", None)
        if isinstance(task_log, list):
            for entry in task_log:
                if entry.get("task_id") == task_id:
                    entry["replayed_to_your_day"] = True
                    entry["your_day_item_id"] = brief_item.id
                    break

    return {
        "ok": True,
        "task_id": task_id,
        "your_day_item_id": brief_item.id,
        "tasks": _owner_mesh_task_rows(),
        "archive": _owner_mesh_archive_payload(),
        "brief": _current_brief_payload(),
    }


@app.post("/api/mesh/peers/{device_id}/forget")
async def forget_mesh_peer(device_id: str):
    if _registry is None:
        raise HTTPException(status_code=503, detail="Mesh registry not ready yet.")
    if _mesh_identity is not None and device_id == _mesh_identity.device_id:
        raise HTTPException(
            status_code=400,
            detail="The local device cannot be forgotten from the dashboard.",
        )
    record = _registry.get_by_device_id(device_id)
    if not record:
        raise HTTPException(status_code=404, detail="Peer not found.")
    if _mesh_node is not None:
        _mesh_node.revoke_peer(device_id)
    if _alias_registry is not None:
        _alias_registry.unregister(device_id)
    _registry.remove_device(device_id)
    return {
        "ok": True,
        "peers": _mesh_peer_rows(),
        "discovered": _mesh_discovery_rows(),
    }


@app.post("/api/your-day/refresh")
async def refresh_your_day():
    if _your_day_service is None:
        raise HTTPException(status_code=503, detail="Your Day service not ready yet.")
    brief = await _your_day_service.generate_and_deliver(target_date=date.today())
    return {
        "ok": True,
        "brief": brief.to_dict(),
    }


@app.post("/api/your-day/approvals/{action_id}")
async def decide_your_day_approval(action_id: str, req: ApprovalDecisionRequest):
    if _safety is None:
        raise HTTPException(status_code=503, detail="Approval controls not ready yet.")

    resolved = _safety.resolve_approval(
        action_id,
        req.approved,
        resolved_by="web_owner",
    )
    if not resolved:
        raise HTTPException(
            status_code=404, detail="Approval not found or already expired."
        )

    # Give the approval-state sync task a chance to update the saved brief.
    await asyncio.sleep(0)
    return {
        "ok": True,
        "action_id": action_id,
        "status": "approved" if req.approved else "denied",
        "brief": _current_brief_payload(),
    }


@app.post("/api/your-day/items/{item_id}/actions/{action}")
async def your_day_item_action(item_id: str, action: str):
    if action not in SUPPORTED_ITEM_ACTIONS:
        raise HTTPException(status_code=400, detail="Unsupported Your Day action.")

    brief, item = get_daily_brief_item(item_id, target_date=date.today())
    if not brief or not item:
        raise HTTPException(status_code=404, detail="Item not found in today's brief.")

    if action == "dismiss":
        updated = update_daily_brief_item(
            item_id,
            state="dismissed",
            actions=[],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action == "accept":
        updated = update_daily_brief_item(
            item_id,
            state="accepted",
            actions=[],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action == "snooze":
        updated = update_daily_brief_item(
            item_id,
            state="snoozed",
            content_updates={"snoozed_until": f"{date.today().isoformat()}T18:00:00"},
            actions=[],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action in {"open_thread", "show_original"}:
        source_item_id, source_item = _resolve_source_email_item(item)
        if not source_item:
            raise HTTPException(
                status_code=400,
                detail="Email source item not available for this action.",
            )
        uid, account = _item_email_locator(source_item)
        if not uid:
            raise HTTPException(
                status_code=400, detail="Email item is missing a message UID."
            )

        content_updates = {}
        if action == "show_original":
            original = fetch_email_message_by_uid(uid, account=account)
            if not original:
                raise HTTPException(
                    status_code=400,
                    detail="Could not load the original email right now.",
                )
            content_updates = {
                "show_original": True,
                "original_email": {
                    "from": original.get("from", ""),
                    "subject": original.get("subject", ""),
                    "date": original.get("date", ""),
                    "body": original.get("body", ""),
                },
            }
        else:
            thread = fetch_email_thread(uid, account=account)
            messages = thread.get("messages", [])
            content_updates = {
                "thread_open": True,
                "thread_messages": [_thread_preview(message) for message in messages],
            }
            if thread.get("original"):
                content_updates["original_email"] = {
                    "from": thread["original"].get("from", ""),
                    "subject": thread["original"].get("subject", ""),
                    "date": thread["original"].get("date", ""),
                    "body": thread["original"].get("body", ""),
                }

        update_target_id = source_item_id or item_id
        updated = update_daily_brief_item(
            update_target_id,
            state="in_progress",
            content_updates=content_updates,
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action == "view":
        updated = update_daily_brief_item(
            item_id,
            state="in_progress",
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action == "follow_up_task":
        if item.get("content", {}).get("source") != "owner_mesh":
            raise HTTPException(
                status_code=400,
                detail="Follow-up tasks can only be created from delegated mesh results.",
            )
        follow_up_item = _build_follow_up_item_from_mesh_result(item)
        upsert_daily_brief_item(follow_up_item, target_date=date.today())
        updated = update_daily_brief_item(
            item_id,
            content_updates={"follow_up_item_id": follow_up_item["id"]},
            actions=["view", "dismiss", "archive_source_task"],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "created_item_id": follow_up_item["id"],
            "brief": _current_brief_payload(),
        }

    if action == "open_as_draft":
        if item.get("content", {}).get("source") != "owner_mesh":
            raise HTTPException(
                status_code=400,
                detail="Draft conversion is only available for delegated mesh results.",
            )
        if item.get("content", {}).get("workflow_type") != "follow_up_draft":
            raise HTTPException(
                status_code=400,
                detail="Only follow-up draft results can open into draft editing.",
            )
        draft_item = build_draft_item_from_mesh_result(item)
        upsert_daily_brief_item(draft_item.to_dict(), target_date=date.today())
        updated = update_daily_brief_item(
            item_id,
            content_updates={"draft_item_id": draft_item.id},
            actions=["view", "dismiss", "archive_source_task"],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "created_item_id": draft_item.id,
            "brief": _current_brief_payload(),
        }

    if action == "archive_source_task":
        if item.get("content", {}).get("source") != "owner_mesh":
            raise HTTPException(
                status_code=400,
                detail="Archive source is only available for delegated mesh results.",
            )
        task_id = str(item.get("content", {}).get("mesh_task_id", "")).strip()
        if not task_id:
            raise HTTPException(
                status_code=400,
                detail="This item is missing its source owner-mesh task.",
            )
        task_engine = getattr(_owner_mesh, "_task_engine", None)
        if task_engine is None or not hasattr(task_engine, "archive_task"):
            raise HTTPException(
                status_code=503, detail="Owner Mesh archive controls are not ready yet."
            )
        archived = task_engine.archive_task(task_id, reason="your_day_cleanup")
        if archived is None:
            raise HTTPException(
                status_code=404, detail="The source owner-mesh task could not be found."
            )
        updated = update_daily_brief_item(
            item_id,
            state="dismissed",
            actions=[],
            content_updates={
                "source_task_archived_at": archived.archived_at,
                "source_task_archive_reason": archived.archive_reason,
            },
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "brief": _current_brief_payload(),
        }

    if action == "draft_reply":
        if item.get("type") != "email_digest":
            raise HTTPException(
                status_code=400, detail="Draft reply is only available for email items."
            )

        draft_payload = await generate_email_reply_draft(item, _brief_llm())
        draft_item = build_draft_item_from_email(item, draft_payload)
        upsert_daily_brief_item(draft_item.to_dict(), target_date=date.today())
        updated = update_daily_brief_item(
            item_id,
            state="drafted",
            content_updates={"draft_item_id": draft_item.id},
            actions=["open_thread", "show_original", "snooze", "dismiss"],
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "created_item_id": draft_item.id,
            "brief": _current_brief_payload(),
        }

    if action == "send":
        if item.get("type") != "draft_message":
            raise HTTPException(
                status_code=400, detail="Send is only available for draft items."
            )
        payload = _draft_send_payload(item)
        uid = str(item.get("content", {}).get("uid", "")).strip()
        reply_tool = _find_tool("reply_email")
        send_tool = _find_tool("send_email")
        if uid and reply_tool is not None:
            result = await reply_tool.execute(
                json.dumps(
                    {
                        "uid": uid,
                        "body": payload["body"],
                        "account": payload["account"],
                    }
                )
            )
        else:
            if send_tool is None:
                raise HTTPException(status_code=503, detail="Email tool not ready yet.")
            if not payload["to"] or not payload["subject"] or not payload["body"]:
                raise HTTPException(
                    status_code=400,
                    detail="Draft is missing recipient, subject, or body.",
                )
            result = await send_tool.execute(json.dumps(payload))
        if not str(result).strip().startswith("✅"):
            raise HTTPException(status_code=400, detail=str(result))
        updated = update_daily_brief_item(
            item_id,
            state="sent",
            actions=[],
            content_updates={
                "sent_at": date.today().isoformat(),
                "result": result,
                "generated_for": "owner_web_send",
            },
            target_date=date.today(),
        )
        return {
            "ok": True,
            "action": action,
            "item": updated,
            "result": result,
            "brief": _current_brief_payload(),
        }

    raise HTTPException(status_code=400, detail="Unsupported Your Day action.")


@app.post("/api/your-day/items/{item_id}/ask")
async def ask_aide_about_your_day_item(item_id: str, req: ItemAskRequest):
    brief, item = get_daily_brief_item(item_id, target_date=date.today())
    if not brief or not item:
        raise HTTPException(status_code=404, detail="Item not found in today's brief.")
    answer = await _ask_aide_about_item(item, req.question)
    update_daily_brief_item(
        item_id,
        state="in_progress",
        content_updates={"last_aide_answer": answer},
        target_date=date.today(),
    )
    return {"ok": True, "item_id": item_id, "answer": answer}


@app.put("/api/your-day/items/{item_id}/draft")
async def update_your_day_draft(item_id: str, req: DraftUpdateRequest):
    brief, item = get_daily_brief_item(item_id, target_date=date.today())
    if not brief or not item:
        raise HTTPException(status_code=404, detail="Item not found in today's brief.")
    if item.get("type") != "draft_message":
        raise HTTPException(
            status_code=400, detail="Only draft items can be edited here."
        )
    to_value = req.to.strip()
    body_value = req.body.strip()
    actions = ["dismiss"]
    if to_value and body_value:
        actions = ["send", "snooze", "dismiss"]
    updated = update_daily_brief_item(
        item_id,
        state="ready",
        content_updates={
            "to": to_value,
            "subject": req.subject.strip(),
            "body": body_value,
        },
        actions=actions,
        target_date=date.today(),
    )
    return {"ok": True, "item": updated, "brief": _current_brief_payload()}


@app.post("/chat")
async def chat(req: MessageRequest):
    if not _agent:
        return {"reply": "Agent not ready yet."}
    try:
        reply = await _agent.run(req.message)
        return {"reply": reply}
    except Exception as e:
        logger.error(f"Web chat error: {e}")
        return {"reply": f"Error: {e}"}


@app.post("/chat/stream")
async def chat_stream(req: MessageRequest):
    async def generate():
        if not _agent:
            yield "data: Agent not ready yet.\n\n"
            return
        try:
            async for chunk in _agent.run_stream(req.message):
                if chunk:
                    chunk = chunk.replace("\r", "")
                    for line in chunk.split("\n"):
                        yield f"data: {line}\n"
                    yield "\n"
        except Exception as e:
            logger.error(f"Web chat stream error: {e}")
            yield f"data: Error: {e}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/status")
async def status():
    from core.llm import LLMClient

    llm = LLMClient()
    ollama_up = await llm.is_ollama_running()
    return {
        "ollama": ollama_up,
        "agent": _agent is not None,
        "agent_name": agent_name(),
        "your_day": _current_brief_payload() is not None,
    }


CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AIDE</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f5f5f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    background: #1a1a1a;
    color: white;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: #1D9E75;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  header h1 { font-size: 18px; font-weight: 500; }
  header span { font-size: 13px; color: #888; margin-left: auto; }
  .header-link {
    margin-left: 12px;
    color: #d9d9d9;
    text-decoration: none;
    border: 1px solid #3b3b3b;
    padding: 8px 12px;
    border-radius: 999px;
    font-size: 12px;
  }
  .header-link:hover { border-color: #1D9E75; color: white; }
  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .msg {
    max-width: 75%;
    padding: 12px 16px;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.user {
    background: #1a1a1a;
    color: white;
    align-self: flex-end;
    border-bottom-right-radius: 4px;
  }
  .msg.aide {
    background: white;
    color: #1a1a1a;
    align-self: flex-start;
    border-bottom-left-radius: 4px;
    border: 0.5px solid #e0e0d8;
  }
  .msg.thinking {
    background: white;
    color: #888;
    align-self: flex-start;
    border: 0.5px solid #e0e0d8;
    border-bottom-left-radius: 4px;
    font-style: italic;
  }
  #input-area {
    padding: 16px 24px;
    background: white;
    border-top: 0.5px solid #e0e0d8;
    display: flex;
    gap: 12px;
    align-items: flex-end;
  }
  #input {
    flex: 1;
    border: 0.5px solid #d0d0c8;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 14px;
    font-family: inherit;
    resize: none;
    min-height: 44px;
    max-height: 120px;
    outline: none;
    background: #fafaf8;
  }
  #input:focus { border-color: #1D9E75; }
  #send {
    background: #1a1a1a;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 14px;
    cursor: pointer;
    height: 44px;
    white-space: nowrap;
  }
  #send:hover { background: #333; }
  #send:disabled { background: #999; cursor: not-allowed; }
  .offline-banner {
    background: #fff3cd;
    color: #856404;
    padding: 8px 24px;
    font-size: 13px;
    text-align: center;
    display: none;
  }
</style>
</head>
<body>

<header>
  <div class="dot" id="status-dot"></div>
  <h1 id="agent-title">AIDE</h1>
  <span id="status-text">connecting...</span>
  <a class="header-link" href="/your-day">Your Day</a>
  <a class="header-link" href="/settings">Settings</a>
  <a class="header-link" href="/onboarding">Setup</a>
</header>
<div class="offline-banner" id="offline-banner">
  No internet — AIDE is running locally on your device
</div>
<div id="messages">
  <div class="msg aide" id="welcome-message">Hi. I'm AIDE, running entirely on your device. How can I help?</div>
</div>
<div id="input-area">
  <button id="voice-btn" onclick="toggleVoiceChat()" style="background:rgba(24,33,27,0.06); border:1px solid #d0d0c8; border-radius:8px; padding:0 12px; font-size:13px; cursor:pointer; font-family:inherit; transition:all 140ms ease;">Speech</button>
  <textarea id="input" placeholder="Message AIDE..." rows="1"></textarea>
   <button id="send" onclick="sendMessage()" style="background: #1a1a1a; color: white; border: none; border-radius: 8px; padding: 10px 20px; font-size: 14px; cursor: pointer; height: 44px; white-space: nowrap;">Send</button>
 </div>
 
 
 <script>
   let messagesEl, inputEl, sendBtn;
 
   function getEls() {
     return {
       messagesEl: document.getElementById('messages'),
       inputEl: document.getElementById('input'),
       sendBtn: document.getElementById('send')
     };
   }
 
   async function checkStatus() {
     try {
       const r = await fetch('/status');
       const d = await r.json();
       const dot = document.getElementById('status-dot');
       const txt = document.getElementById('status-text');
       if (dot) dot.style.background = d.ollama ? '#1D9E75' : '#BA7517';
       if (txt) txt.textContent = d.ollama ? 'online' : 'offline — local only';
       const agentName = d.agent_name || 'AIDE';
       document.title = agentName;
       const title = document.getElementById('agent-title');
       if (title) title.textContent = agentName;
       const input = document.getElementById('input');
       if (input) input.placeholder = `Message ${agentName}...`;
       const welcome = document.getElementById('welcome-message');
       if (welcome && !sessionStorage.getItem('chat_history')) {
         welcome.textContent = `Hi. I'm ${agentName}, running entirely on your device. How can I help?`;
       }
     } catch(e) {
       const txt = document.getElementById('status-text');
       if (txt) txt.textContent = 'disconnected';
     }
   }
 
   function addMessage(text, type, save = true) {
     const mEl = document.getElementById('messages');
     if (!mEl) return;
     const div = document.createElement('div');
     div.className = 'msg ' + type;
     div.textContent = text;
     mEl.appendChild(div);
     mEl.scrollTop = mEl.scrollHeight;
     if (save) {
       const history = JSON.parse(sessionStorage.getItem('chat_history') || '[]');
       history.push({text, type});
       sessionStorage.setItem('chat_history', JSON.stringify(history));
     }
     return div;
   }
 
   async function sendMessage() {
     const iEl = document.getElementById('input');
     const sBtn = document.getElementById('send');
     if (!iEl || !sBtn) return;
     
     const text = iEl.value.trim();
     if (!text) return;
     
     addMessage(text, 'user');
     iEl.value = '';
     iEl.style.height = 'auto';
     sBtn.disabled = true;
     
     const thinking = addMessage('Thinking...', 'thinking');
     
     try {
       const r = await fetch('/chat/stream', {
         method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({message: text}),
       });
     
       if (!r.ok || !r.body) throw new Error('Streaming unavailable');
     
       const reader = r.body.getReader();
       const decoder = new TextDecoder();
       thinking.remove();
       const replyEl = addMessage('', 'aide');
       let buffer = '';
     
       while (true) {
         const {value, done} = await reader.read();
         if (done) break;
         buffer += decoder.decode(value, {stream: true});
         const events = buffer.split('\\n\\n');
         buffer = events.pop() || '';
         for (const event of events) {
           const lines = event.split('\\n');
           for (const line of lines) {
             if (!line.startsWith('data: ')) continue;
             replyEl.textContent += line.slice(6);
           }
           const mEl = document.getElementById('messages');
           if (mEl) mEl.scrollTop = mEl.scrollHeight;
         }
       }
       buffer += decoder.decode();
       if (buffer.trim()) {
         const lines = buffer.split('\\n');
         for (const line of lines) {
           if (!line.startsWith('data: ')) continue;
           replyEl.textContent += line.slice(6);
         }
       }
       if (!replyEl.textContent.trim()) replyEl.textContent = 'No reply received.';
     } catch(e) {
       console.error('Stream error:', e);
       try {
         const r = await fetch('/chat', {
           method: 'POST',
           headers: {'Content-Type': 'application/json'},
           body: JSON.stringify({message: text}),
         });
         const d = await r.json();
         thinking.remove();
         addMessage(d.reply, 'aide');
       } catch(_) {
         thinking.remove();
        addMessage('Connection error — is AIDE running?', 'aide');
       }
     }
     sBtn.disabled = false;
     iEl.focus();
   }
 
   let isVoiceActive = false;
  async function toggleVoiceChat() {
    const btn = document.getElementById('voice-btn');
    if (!isVoiceActive) {
      try {
        const res = await fetch('/api/voice/trigger', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'start', session_id: 'voice-chat'})
        });
        if (res.ok) {
          isVoiceActive = true;
          btn.textContent = '🛑 Stop';
          btn.style.background = '#fdd';
        }
      } catch (e) { console.error(e); }
    } else {
      try {
        const res = await fetch('/api/voice/trigger', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({action: 'stop', session_id: 'voice-chat'})
        });
        if (res.ok) {
          isVoiceActive = false;
          btn.textContent = 'Speech';
          btn.style.background = 'rgba(24,33,27,0.06)';
          setTimeout(async () => {
            try {
              const r = await fetch('/api/voice/transcript');
              const d = await r.json();
              if (d.text) {
                const iEl = document.getElementById('input');
                if (iEl) iEl.value = d.text;
                sendMessage();
              }
            } catch (e) { console.error(e); }
          }, 1500);
        }
      } catch (e) { console.error(e); }
    }
  }
 
  function initChat() {
    console.log('Initializing chat interface...');
    const iEl = document.getElementById('input');
    if (!iEl) {
      console.error('Input element not found during initChat');
      return;
    }

 
     iEl.addEventListener('keydown', e => {
       if (e.key === 'Enter' && !e.shiftKey) {
         e.preventDefault();
         sendMessage();
       }
     });
 
     iEl.addEventListener('input', () => {
       iEl.style.height = 'auto';
       iEl.style.height = Math.min(iEl.scrollHeight, 120) + 'px';
     });
     
     const history = JSON.parse(sessionStorage.getItem('chat_history') || '[]');
     if (history.length > 0) {
       const mEl = document.getElementById('messages');
       if (mEl) {
         mEl.innerHTML = '';
         history.forEach(m => addMessage(m.text, m.type, false));
       }
     }
     
     checkStatus();
     setInterval(checkStatus, 30000);
     console.log('Chat interface initialized.');
   }
 
   if (document.readyState === 'loading') {
     document.addEventListener('DOMContentLoaded', initChat);
   } else {
     initChat();
   }
</script>
</body>
</html>"""


MODELS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Model Diagnostics</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: #f5f5f0;
    color: #1a1a1a;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
  header {
    background: #1a1a1a;
    color: white;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  header h1 { font-size: 18px; font-weight: 500; margin: 0; }
  .header-link {
    color: #d9d9d9;
    text-decoration: none;
    border: 1px solid #3b3b3b;
    padding: 8px 12px;
    border-radius: 999px;
    font-size: 12px;
  }
  .header-link:first-of-type { margin-left: auto; }
  .header-link:hover { border-color: #1D9E75; color: white; }
  main {
    max-width: 1100px;
    margin: 0 auto;
    padding: 24px;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 18px;
  }
  .card {
    background: white;
    border: 1px solid #e0e0d8;
    border-radius: 16px;
    padding: 16px;
  }
  .label {
    color: #666;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .metric {
    font-size: 26px;
    font-weight: 700;
    word-break: break-word;
  }
  .subtle {
    color: #666;
    font-size: 13px;
    margin-top: 8px;
    line-height: 1.5;
  }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  .empty {
    display: none;
    background: white;
    border: 1px dashed #d0d0c8;
    border-radius: 16px;
    padding: 24px;
    color: #666;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    background: white;
    border: 1px solid #e0e0d8;
    border-radius: 16px;
    overflow: hidden;
    display: none;
  }
  th, td {
    padding: 12px 14px;
    border-bottom: 1px solid #ecece2;
    text-align: left;
    vertical-align: top;
    font-size: 13px;
  }
  th {
    background: #fafaf8;
    color: #666;
    font-weight: 600;
  }
  tr:last-child td { border-bottom: none; }
  .pill {
    display: inline-block;
    padding: 4px 8px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
  }
  .ok { background: #e7f7ef; color: #1f7a4d; }
  .fail { background: #fdeaea; color: #a13838; }
</style>
</head>
<body>
<header>
  <h1>Model Diagnostics</h1>
  <a class="header-link" href="/">Chat</a>
  <a class="header-link" href="/your-day">Your Day</a>
  <a class="header-link" href="/mesh">Mesh</a>
</header>
<main>
  <div class="grid">
    <section class="card">
      <div class="label">Primary</div>
      <div class="metric mono" id="primary-model">-</div>
      <div class="subtle">Configured primary model candidate.</div>
    </section>
    <section class="card">
      <div class="label">Fallback</div>
      <div class="metric mono" id="fallback-model">-</div>
      <div class="subtle">Used when the primary model fails.</div>
    </section>
    <section class="card">
      <div class="label">Average Latency</div>
      <div class="metric" id="average-latency">-</div>
      <div class="subtle">Across recent successful runs.</div>
    </section>
    <section class="card">
      <div class="label">Endpoint</div>
      <div class="metric mono" id="endpoint" style="font-size: 15px;">-</div>
      <div class="subtle" id="endpoint-mode">-</div>
    </section>
  </div>
  <div class="empty" id="empty-state">No model runs recorded yet. Send a message or refresh Your Day to populate diagnostics.</div>
  <table id="runs-table">
    <thead>
      <tr>
        <th>When</th>
        <th>Task</th>
        <th>Mode</th>
        <th>Provider</th>
        <th>Model</th>
        <th>Latency</th>
        <th>Status</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody id="runs-body"></tbody>
  </table>
</main>
<script>
function fmtMs(value) {
  if (value === null || value === undefined || value === '') return '-';
  return `${Number(value).toFixed(1)} ms`;
}

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function refreshModels() {
  const response = await fetch('/api/models');
  const payload = await response.json();

  document.getElementById('primary-model').textContent = payload.configured?.ollama_model || '-';
  document.getElementById('fallback-model').textContent = payload.configured?.ollama_fallback_model || '-';
  document.getElementById('average-latency').textContent = fmtMs(payload.summary?.average_latency_ms);
  document.getElementById('endpoint').textContent = payload.configured?.ollama_base_url || '-';
  document.getElementById('endpoint-mode').textContent = payload.configured?.ollama_cloud ? 'Cloud Ollama endpoint' : 'Local Ollama endpoint';

  const rows = payload.runs || [];
  const emptyState = document.getElementById('empty-state');
  const table = document.getElementById('runs-table');
  const body = document.getElementById('runs-body');
  body.innerHTML = '';

  if (!rows.length) {
    emptyState.style.display = 'block';
    table.style.display = 'none';
    return;
  }

  emptyState.style.display = 'block';
  emptyState.style.display = 'none';
  table.style.display = 'table';

  for (const run of rows) {
    const notes = [];
    if (run.metrics?.eval_count) notes.push(`eval ${run.metrics.eval_count}`);
    if (run.metrics?.prompt_eval_count) notes.push(`prompt ${run.metrics.prompt_eval_count}`);
    if (run.error) notes.push(run.error);
    if (run.offline) notes.push('offline');
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${esc(run.timestamp)}</td>
      <td>${esc(run.task_type)}</td>
      <td>${esc(run.mode)}</td>
      <td>${esc(run.provider)}</td>
      <td class="mono">${esc(run.model)}</td>
      <td>${fmtMs(run.duration_ms)}</td>
      <td><span class="pill ${run.success ? 'ok' : 'fail'}">${run.success ? 'success' : 'failed'}</span></td>
      <td>${esc(notes.join(' · '))}</td>
    `;
    body.appendChild(row);
  }
}

refreshModels();
setInterval(refreshModels, 5000);
</script>
</body>
</html>"""


YOUR_DAY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your Day</title>
<style>
  :root {
    --paper: #f3efe4;
    --ink: #18211b;
    --muted: #59655d;
    --panel: rgba(255, 252, 245, 0.88);
    --line: rgba(24, 33, 27, 0.12);
    --accent: #2f7d5e;
    --accent-strong: #19553d;
    --warn: #b56a1b;
    --danger: #8f4634;
    --shadow: 0 20px 60px rgba(24, 33, 27, 0.12);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(circle at top left, rgba(47,125,94,0.18), transparent 28%),
      radial-gradient(circle at top right, rgba(181,106,27,0.12), transparent 24%),
      linear-gradient(180deg, #f8f5eb 0%, var(--paper) 100%);
    color: var(--ink);
    font-family: Georgia, 'Times New Roman', serif;
  }
  .shell {
    max-width: 1280px;
    margin: 0 auto;
    padding: 36px 24px 64px;
  }
  .hero {
    display: grid;
    grid-template-columns: 1.4fr .9fr;
    gap: 20px;
    align-items: stretch;
  }
  .hero-card, .summary-card, .section-card, .empty-card {
    background: var(--panel);
    backdrop-filter: blur(16px);
    border: 1px solid var(--line);
    border-radius: 28px;
    box-shadow: var(--shadow);
  }
  .hero-card {
    padding: 28px;
    position: relative;
    overflow: hidden;
  }
  .hero-card::after {
    content: "";
    position: absolute;
    right: -60px;
    top: -50px;
    width: 200px;
    height: 200px;
    border-radius: 50%;
    background: rgba(47,125,94,0.10);
  }
  .eyebrow {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    border-radius: 999px;
    border: 1px solid rgba(47,125,94,0.18);
    background: rgba(47,125,94,0.08);
    color: var(--accent-strong);
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  h1 {
    margin: 16px 0 10px;
    font-size: clamp(42px, 6vw, 72px);
    line-height: 0.95;
    letter-spacing: -0.04em;
  }
  .headline {
    font-size: 18px;
    line-height: 1.6;
    color: var(--muted);
    max-width: 62ch;
  }
  .hero-actions {
    margin-top: 24px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .nav-pair {
    display: inline-flex;
    gap: 10px;
    flex-wrap: nowrap;
  }
  .hero-subnav-note {
    margin-top: 12px;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }
  .hero-subnav-note a {
    color: var(--accent-strong);
    text-decoration: none;
    border-bottom: 1px solid rgba(25,85,61,0.18);
  }
  button, .nav-link {
    border: none;
    border-radius: 999px;
    padding: 11px 16px;
    cursor: pointer;
    font-size: 14px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    transition: transform 140ms ease, opacity 140ms ease, background 140ms ease;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  button:hover, .nav-link:hover { transform: translateY(-1px); }
  button:disabled { cursor: wait; opacity: 0.6; transform: none; }
  .primary {
    background: var(--ink);
    color: white;
  }
  .secondary, .nav-link {
    background: rgba(24,33,27,0.06);
    color: var(--ink);
    border: 1px solid var(--line);
  }
  .summary-card {
    padding: 24px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-height: 100%;
  }
  .status-panel {
    margin-top: 20px;
    display: grid;
    gap: 12px;
  }
  .status-panel h2 {
    margin: 0 0 8px;
    font-size: 22px;
    letter-spacing: -0.03em;
  }
  .source-row {
    display: grid;
    gap: 6px;
    padding: 16px 18px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    border-radius: 20px;
  }
  .source-row strong {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .source-row.ok strong {
    color: var(--accent-strong);
  }
  .source-row.warn strong {
    color: var(--danger);
  }
  .source-row p {
    margin: 0;
    color: var(--ink);
    line-height: 1.5;
    font-size: 15px;
  }
  .source-row .mini {
    color: var(--muted);
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .finance-upload-grid {
    display: grid;
    grid-template-columns: minmax(220px, 1.2fr) minmax(150px, 0.6fr) auto;
    gap: 10px;
    align-items: end;
  }
  .finance-upload-field {
    display: grid;
    gap: 6px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    color: var(--muted);
    font-size: 12px;
  }
  .finance-upload-field input,
  .finance-upload-field select {
    min-height: 42px;
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 9px 11px;
    background: rgba(255,255,255,0.94);
    color: var(--ink);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  .finance-upload-status {
    margin-top: 12px;
    padding: 12px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(24,33,27,0.04);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  .finance-upload-status.ok {
    background: rgba(47,125,94,0.08);
    color: var(--accent-strong);
    border-color: rgba(47,125,94,0.22);
  }
  .finance-upload-status.error {
    background: rgba(143,70,52,0.08);
    color: var(--danger);
    border-color: rgba(143,70,52,0.2);
  }
  .finance-month-list {
    margin-top: 12px;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .finance-month-chip {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 7px 10px;
    background: rgba(255,255,255,0.76);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
  }
  .conflict-banner {
    margin-top: 20px;
    padding: 18px 20px;
    border-radius: 22px;
    background: rgba(143,70,52,0.10);
    border: 1px solid rgba(143,70,52,0.16);
    color: var(--danger);
    box-shadow: var(--shadow);
  }
  .conflict-banner strong {
    display: block;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .conflict-banner p {
    margin: 0;
    line-height: 1.6;
    font-size: 18px;
  }
  .mesh-activity-strip {
    margin-top: 20px;
    display: grid;
    gap: 16px;
    padding: 22px 24px;
    background: var(--panel);
    backdrop-filter: blur(16px);
    border: 1px solid var(--line);
    border-radius: 28px;
    box-shadow: var(--shadow);
  }
  .mesh-activity-head {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .mesh-activity-head h2 {
    margin: 0;
    font-size: 24px;
    letter-spacing: -0.03em;
  }
  .mesh-activity-head p {
    margin: 0;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
  }
  .mesh-activity-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }
  .mesh-activity-card {
    padding: 16px 18px;
    border-radius: 20px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    display: grid;
    gap: 8px;
  }
  .mesh-activity-card strong {
    display: block;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 28px;
    line-height: 1;
  }
  .mesh-activity-card span {
    color: var(--ink);
    font-size: 14px;
  }
  .mesh-activity-card .mini {
    color: var(--muted);
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    line-height: 1.5;
  }
  .mesh-activity-actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .attention-card {
    margin-top: 20px;
    padding: 22px;
  }
  .attention-item {
    padding: 16px 18px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    border-radius: 20px;
    display: grid;
    gap: 12px;
  }
  .attention-item.running {
    border-color: rgba(47,125,94,0.22);
  }
  .attention-item.waiting_approval {
    border-color: rgba(181,106,27,0.22);
  }
  .attention-item.retry_needed {
    border-color: rgba(143,70,52,0.22);
  }
  .attention-item.ready_to_replay {
    border-color: rgba(24,33,27,0.16);
  }
  .attention-note {
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
  }
  .task-kicker {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .task-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.08);
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .task-chip.primary {
    background: rgba(47,125,94,0.10);
    color: var(--accent-strong);
  }
  .task-chip.warn {
    background: rgba(181,106,27,0.14);
    color: var(--warn);
  }
  .task-chip.muted {
    background: rgba(24,33,27,0.06);
    color: var(--muted);
  }
  .task-meta {
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    color: var(--muted);
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    line-height: 1.5;
  }
  .task-meta strong {
    color: var(--ink);
  }
  .task-preview {
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(24,33,27,0.05);
    border: 1px solid var(--line);
    color: var(--ink);
    line-height: 1.6;
    font-size: 14px;
  }
  .task-preview strong {
    display: block;
    margin-bottom: 6px;
  }
  .archive-overview {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }
  .archive-overview-card {
    padding: 14px 16px;
    border-radius: 18px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    display: grid;
    gap: 6px;
  }
  .archive-overview-card strong {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 22px;
    line-height: 1;
  }
  .archive-overview-card span {
    color: var(--muted);
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .source-task-modal {
    position: fixed;
    inset: 0;
    background: rgba(12, 18, 15, 0.38);
    display: none;
    align-items: center;
    justify-content: center;
    padding: 20px;
    z-index: 50;
  }
  .source-task-modal.open {
    display: flex;
  }
  .source-task-modal-card {
    width: min(820px, 100%);
    max-height: calc(100vh - 40px);
    overflow: auto;
    background: rgba(250,246,239,0.98);
    border: 1px solid var(--line);
    border-radius: 28px;
    box-shadow: var(--shadow);
    padding: 22px;
    display: grid;
    gap: 16px;
  }
  .source-task-modal-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: flex-start;
  }
  .source-task-modal-head h3 {
    margin: 8px 0 0;
    font-size: 28px;
    letter-spacing: -0.03em;
  }
  .source-task-modal-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .source-task-modal-field {
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    display: grid;
    gap: 6px;
  }
  .source-task-modal-field strong {
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .source-task-modal-text,
  .source-task-modal-code {
    padding: 14px;
    border-radius: 16px;
    background: rgba(24,33,27,0.05);
    border: 1px solid var(--line);
    color: var(--ink);
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .source-task-modal-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
  }
  .source-task-modal-close {
    background: rgba(24,33,27,0.06);
    color: var(--ink);
    border: 1px solid var(--line);
  }
  .status-line {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    color: var(--muted);
  }
  .meta {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin-top: 18px;
  }
  .count {
    padding: 14px;
    border-radius: 18px;
    background: rgba(255,255,255,0.7);
    border: 1px solid var(--line);
  }
  .count strong {
    display: block;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 24px;
    margin-bottom: 4px;
  }
  .count span {
    color: var(--muted);
    font-size: 13px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .layout {
    display: grid;
    grid-template-columns: 1.05fr 1fr;
    gap: 20px;
    margin-top: 22px;
  }
  .stack {
    display: grid;
    gap: 20px;
    align-content: start;
  }
  .section-card {
    padding: 22px;
  }
  .section-head {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    align-items: baseline;
    margin-bottom: 16px;
  }
  .section-head h2 {
    margin: 0;
    font-size: 26px;
    letter-spacing: -0.03em;
  }
  .section-head p {
    margin: 0;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
  }
  .item-list {
    display: grid;
    gap: 14px;
  }
  .item {
    padding: 16px 18px;
    background: rgba(255,255,255,0.76);
    border: 1px solid var(--line);
    border-radius: 20px;
  }
  .item-top {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    align-items: flex-start;
  }
  .item h3 {
    margin: 0 0 6px;
    font-size: 18px;
  }
  .item .reason {
    margin: 0;
    color: var(--muted);
    line-height: 1.5;
    font-size: 14px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .badge {
    padding: 6px 10px;
    border-radius: 999px;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    background: rgba(24,33,27,0.08);
    color: var(--muted);
    white-space: nowrap;
  }
  .badge.high { background: rgba(181,106,27,0.14); color: var(--warn); }
  .badge.approval { background: rgba(143,70,52,0.12); color: var(--danger); }
  .state-badge {
    margin-top: 10px;
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(47,125,94,0.10);
    color: var(--accent-strong);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .kv {
    margin-top: 12px;
    display: grid;
    gap: 8px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  .kv div { color: var(--muted); }
  .kv strong { color: var(--ink); }
  .approval-actions {
    display: flex;
    gap: 10px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .item-actions {
    display: flex;
    gap: 10px;
    margin-top: 14px;
    flex-wrap: wrap;
  }
  .action-btn {
    background: rgba(24,33,27,0.06);
    color: var(--ink);
    border: 1px solid var(--line);
  }
  .send-btn {
    background: var(--accent);
    color: white;
  }
  .ask-panel {
    margin-top: 14px;
    padding: 12px;
    border-radius: 16px;
    background: rgba(24,33,27,0.04);
    border: 1px solid var(--line);
    display: grid;
    gap: 10px;
  }
  .ask-panel textarea, .draft-editor textarea, .draft-editor input {
    width: 100%;
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 10px 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    background: rgba(255,255,255,0.92);
    color: var(--ink);
  }
  .ask-answer {
    padding: 12px;
    border-radius: 14px;
    background: rgba(47,125,94,0.08);
    color: var(--ink);
    line-height: 1.6;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    white-space: pre-wrap;
  }
  .draft-editor {
    margin-top: 14px;
    display: grid;
    gap: 10px;
  }
  .context-panel {
    margin-top: 14px;
    padding: 14px;
    border-radius: 16px;
    background: rgba(24,33,27,0.05);
    border: 1px solid var(--line);
    display: grid;
    gap: 10px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .context-panel h4 {
    margin: 0;
    font-size: 13px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .context-panel pre {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--ink);
    font-size: 14px;
    line-height: 1.6;
  }
  .thread-list {
    display: grid;
    gap: 10px;
  }
  .thread-message {
    padding: 12px;
    border-radius: 14px;
    background: rgba(255,255,255,0.9);
    border: 1px solid var(--line);
  }
  .thread-message .mini {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 6px;
  }
  .approve-btn {
    background: var(--accent);
    color: white;
  }
  .deny-btn {
    background: rgba(143,70,52,0.12);
    color: var(--danger);
    border: 1px solid rgba(143,70,52,0.18);
  }
  .empty-card {
    padding: 28px;
    margin-top: 22px;
    text-align: center;
  }
  .empty-card h2 {
    margin: 0 0 10px;
    font-size: 30px;
  }
  .empty-card p {
    margin: 0;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    line-height: 1.7;
  }
  .status-toast {
    position: fixed;
    right: 24px;
    bottom: 24px;
    padding: 14px 18px;
    border-radius: 16px;
    background: rgba(24,33,27,0.92);
    color: white;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    opacity: 0;
    transform: translateY(10px);
    transition: opacity 180ms ease, transform 180ms ease;
    pointer-events: none;
  }
  .status-toast.show {
    opacity: 1;
    transform: translateY(0);
  }
  .muted-note {
    color: var(--muted);
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  @media (max-width: 960px) {
    .hero, .layout {
      grid-template-columns: 1fr;
    }
    .finance-upload-grid {
      grid-template-columns: 1fr;
    }
    .mesh-activity-grid {
      grid-template-columns: 1fr 1fr;
    }
    .archive-overview {
      grid-template-columns: 1fr 1fr;
    }
    .source-task-modal-grid {
      grid-template-columns: 1fr;
    }
    .shell {
      padding: 20px 16px 44px;
    }
  }
  @media (max-width: 640px) {
    .mesh-activity-grid {
      grid-template-columns: 1fr;
    }
    .archive-overview {
      grid-template-columns: 1fr;
    }
  }
</style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="hero-card">
        <div class="eyebrow">Owner View</div>
        <h1>Your Day</h1>
        <p class="headline" id="headline">Loading today’s brief...</p>
        <div class="hero-actions">
          <button class="primary" id="refresh-btn" onclick="refreshBrief()">Refresh Brief</button>
          <a class="nav-link" href="/">Back to Chat</a>
            <span class="nav-pair">
              <a class="nav-link" href="/finance">FinanceOS</a>
            </span>
          </div>


        <div class="hero-subnav-note">Owner Mesh, M-Peer, and Fit Genie are outside the MVP shipping surface.</div>
      </div>
      <div class="summary-card">
        <div>
          <div class="status-line" id="brief-date">Preparing status...</div>
          <div class="meta" id="summary-counts"></div>
        </div>
        <div class="muted-note" id="brief-generated-at">Looking for the latest saved brief.</div>
      </div>
    </section>

    <section class="conflict-banner" id="conflict-banner" style="display:none;">
      <strong>Schedule Conflicts</strong>
      <p id="conflict-banner-text">Conflicts detected.</p>
    </section>

    <section class="mesh-activity-strip" id="mesh-activity-strip" style="display:none;">
      <div class="mesh-activity-head">
        <div>
          <h2>Mesh Activity</h2>
          <p>Delegated work already replayed into today’s brief, reduced to what needs review first.</p>
        </div>
        <div class="mesh-activity-actions">
          <button class="secondary" onclick="focusDelegatedWork()">Jump to Delegated Work</button>
        </div>
      </div>
      <div class="mesh-activity-grid" id="mesh-activity-grid"></div>
    </section>

    <section class="section-card attention-card" id="mesh-attention-card" style="display:none;">
      <div class="section-head">
        <div>
          <h2>Mesh Needs Attention</h2>
          <p>Owner-mesh work that is blocked, still running, failed, or ready to replay.</p>
        </div>
        <div class="mesh-activity-actions">
          <a class="nav-link" href="/mesh">Open Owner Mesh</a>
        </div>
      </div>
      <div class="item-list" id="mesh-attention-list"></div>
    </section>

    <section class="section-card status-panel">
      <div class="section-head">
        <h2>Calendar Sources</h2>
        <p>Quick visibility into what AIDE can read right now.</p>
      </div>
      <div id="calendar-status-list"></div>
    </section>

    <section class="section-card status-panel" id="finance-ingest-panel">
      <div class="section-head">
        <div>
          <h2>FinanceOS</h2>
          <p>Statement intake and latest monthly reports.</p>
        </div>
        <a class="nav-link" href="/finance">Open FinanceOS</a>
      </div>
      <div class="finance-upload-grid">
        <label class="finance-upload-field">
          Statement PDF
          <input id="finance-statement-file" type="file" accept="application/pdf">
        </label>
        <label class="finance-upload-field">
          Bank
          <select id="finance-bank-name">
            <option value="generic">Generic PDF</option>
            <option value="deutsche_bank">Deutsche Bank</option>
            <option value="n26">N26</option>
          </select>
        </label>
        <button class="primary" id="finance-upload-btn" onclick="uploadFinanceStatement()">Process Statement</button>
      </div>
      <div class="finance-upload-status" id="finance-upload-status">No statement processed in this session.</div>
      <div class="finance-month-list" id="finance-month-list"></div>
    </section>

    <div id="empty-state" class="empty-card" style="display:none;">
      <h2>No brief yet</h2>
      <p>Your Day hasn’t been generated for today. Generate it here and it will also sync to approved owner devices.</p>
    </div>

    <section class="layout" id="brief-layout" style="display:none;">
      <div class="stack">
        <div class="section-card">
          <div class="section-head">
            <h2>Approvals</h2>
            <p>Act from the web and keep other owner devices aligned.</p>
          </div>
          <div class="item-list" id="approvals-list"></div>
        </div>
        <div class="section-card">
          <div class="section-head">
            <h2>Inbox Digest</h2>
            <p>Unread emails from the last 48 hours with sender, subject, and summary.</p>
          </div>
          <div class="item-list" id="emails-list"></div>
        </div>
        <div class="section-card">
          <div class="section-head">
            <h2>Draft Replies</h2>
            <p>Responses AIDE prepared for messages that look actionable today.</p>
          </div>
          <div class="item-list" id="drafts-list"></div>
        </div>
      </div>

      <div class="stack">
        <div class="section-card">
          <div class="section-head">
            <h2>Schedule</h2>
            <p>Today’s agenda across the calendars AIDE can see.</p>
          </div>
          <div class="item-list" id="schedule-list"></div>
        </div>
        <div class="section-card" id="delegated-work-section">
          <div class="section-head">
            <h2>Delegated Work</h2>
            <p>Completed owner-mesh results replayed into today’s cockpit.</p>
          </div>
          <div class="item-list" id="delegated-list"></div>
        </div>
        <div class="section-card">
          <div class="section-head">
            <h2>Suggestions</h2>
            <p>Conflicts, focus windows, and schedule shaping suggestions.</p>
          </div>
          <div class="item-list" id="suggestions-list"></div>
        </div>
        <div class="section-card">
          <div class="section-head">
            <h2>Prepared Tasks</h2>
            <p>Useful tasks AIDE thinks are worth tackling today.</p>
          </div>
          <div class="item-list" id="tasks-list"></div>
        </div>
      </div>
    </section>
  </div>

  <div class="status-toast" id="toast"></div>
  <div id="source-task-detail-modal" class="source-task-modal" onclick="dismissSourceTaskDetail(event)">
    <div class="source-task-modal-card" role="dialog" aria-modal="true" aria-labelledby="source-task-detail-title">
      <div class="source-task-modal-head">
        <div>
          <div class="task-kicker" id="source-task-detail-kicker"></div>
          <h3 id="source-task-detail-title">Source Task Detail</h3>
        </div>
        <button class="source-task-modal-close" onclick="closeSourceTaskDetail()">Close</button>
      </div>
      <div id="source-task-detail-grid" class="source-task-modal-grid"></div>
      <div class="section-card" style="padding:18px;">
        <div class="section-head">
          <h2 style="font-size:18px;">Context Notes</h2>
        </div>
        <div id="source-task-detail-notes" class="source-task-modal-text">No context notes.</div>
      </div>
      <div class="section-card" style="padding:18px;">
        <div class="section-head">
          <h2 style="font-size:18px;">Result</h2>
        </div>
        <div id="source-task-detail-result" class="source-task-modal-text">No result recorded yet.</div>
      </div>
      <div class="section-card" style="padding:18px;">
        <div class="section-head">
          <h2 style="font-size:18px;">Task Payload</h2>
        </div>
        <div id="source-task-detail-payload" class="source-task-modal-code">{}</div>
      </div>
    </div>
  </div>

<script>
  const inactiveStates = new Set(['dismissed', 'accepted', 'completed', 'sent', 'denied', 'snoozed']);
  const typeLabels = {
    approval_request: 'Approval',
    email_digest: 'Email',
    draft_message: 'Draft',
    calendar_agenda: 'Schedule',
    schedule_suggestion: 'Suggestion',
    prepared_task: 'Task',
  };

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function showToast(message, kind='info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.background = kind === 'error' ? 'rgba(143,70,52,0.94)' : 'rgba(24,33,27,0.92)';
    toast.classList.add('show');
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2600);
  }

  function setFinanceStatus(message, kind='info') {
    const status = document.getElementById('finance-upload-status');
    if (!status) return;
    status.textContent = message;
    status.className = `finance-upload-status ${kind === 'error' ? 'error' : kind === 'ok' ? 'ok' : ''}`.trim();
  }

  function renderFinanceMonths(months = []) {
    const list = document.getElementById('finance-month-list');
    if (!list) return;
    if (!months.length) {
      list.innerHTML = '<span class="finance-month-chip">No finance reports yet</span>';
      return;
    }
    list.innerHTML = months.slice(0, 4).map(month => `
      <a class="finance-month-chip" href="/finance/${encodeURIComponent(month.month_key)}">
        ${escapeHtml(month.month_key)} · ${escapeHtml(month.count || 0)} transactions
      </a>
    `).join('');
  }

  async function loadFinanceMonths() {
    try {
      const response = await fetch('/api/finance/months');
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.detail || 'Finance months unavailable');
      renderFinanceMonths(payload.months || []);
    } catch (error) {
      renderFinanceMonths([]);
    }
  }

  async function uploadFinanceStatement() {
    const input = document.getElementById('finance-statement-file');
    const bank = document.getElementById('finance-bank-name')?.value || 'generic';
    const button = document.getElementById('finance-upload-btn');
    const file = input?.files?.[0];
    if (!file) {
      setFinanceStatus('Choose a PDF statement first.', 'error');
      return;
    }
    const body = new FormData();
    body.append('file', file);
    const original = button?.textContent || 'Process Statement';
    if (button) {
      button.disabled = true;
      button.textContent = 'Processing...';
    }
    setFinanceStatus('Processing statement locally...');
    try {
      const response = await fetch(`/api/finance/upload?bank_name=${encodeURIComponent(bank)}`, {
        method: 'POST',
        body,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.ok) {
        throw new Error(payload.detail || 'Statement processing failed');
      }
      setFinanceStatus(
        `Processed ${payload.inserted_count || 0} new transactions for ${payload.month_key}. ${payload.uncategorised_count || 0} need category review.`,
        'ok'
      );
      if (input) input.value = '';
      await loadFinanceMonths();
      showToast('FinanceOS statement processed.');
    } catch (error) {
      setFinanceStatus(error.message || 'Statement processing failed.', 'error');
      showToast(error.message || 'FinanceOS upload failed.', 'error');
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = original;
      }
    }
  }

  function renderCounts(counts = {}) {
    const entries = [
      ['approval_requests', 'Pending approvals'],
      ['unread_emails', 'Unread emails'],
      ['calendar_events', 'Calendar events'],
      ['schedule_suggestions', 'Suggestions'],
    ];
    const el = document.getElementById('summary-counts');
    el.innerHTML = entries.map(([key, label]) => `
      <div class="count">
        <strong>${counts[key] || 0}</strong>
        <span>${label}</span>
      </div>
    `).join('');
  }

  function focusDelegatedWork() {
    const section = document.getElementById('delegated-work-section');
    if (!section) return;
    section.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  function formatTaskStateLabel(state) {
    const value = String(state || '').trim();
    if (!value) return 'Unknown';
    return value
      .split('_')
      .map(part => part ? `${part[0].toUpperCase()}${part.slice(1)}` : part)
      .join(' ');
  }

  function formatApprovalStateLabel(state) {
    const value = String(state || '').trim();
    if (!value) return 'Pending';
    return value
      .split('_')
      .map(part => part ? `${part[0].toUpperCase()}${part.slice(1)}` : part)
      .join(' ');
  }

  function isSameCalendarDay(isoValue, date = new Date()) {
    if (!isoValue) return false;
    const parsed = new Date(isoValue);
    if (Number.isNaN(parsed.getTime())) return false;
    return parsed.toDateString() === date.toDateString();
  }

  function formatLatestArchiveLabel(tasks = []) {
    const latest = [...tasks]
      .map(task => task.archived_at)
      .filter(Boolean)
      .sort()
      .pop();
    return latest || 'None yet';
  }

  function openOwnerMeshForTask(taskId) {
    const suffix = taskId ? `#task-${encodeURIComponent(taskId)}` : '';
    window.location.href = `/mesh${suffix}`;
  }

  function renderMeshActivity(items = []) {
    const strip = document.getElementById('mesh-activity-strip');
    const grid = document.getElementById('mesh-activity-grid');
    if (!items.length) {
      strip.style.display = 'none';
      grid.innerHTML = '';
      return;
    }

    const needsApproval = items.filter(item => item.content?.requires_approval).length;
    const researchReady = items.filter(item => item.content?.workflow_type === 'research_brief').length;
    const draftReady = items.filter(item => ['follow_up_draft', 'email_triage'].includes(item.content?.workflow_type)).length;
    const replayedToday = items.filter(item => item.content?.replayed_at).length;
    const executors = Array.from(new Set(items.map(item => item.content?.executor_device_name).filter(Boolean)));

    const cards = [
      {
        count: items.length,
        label: 'Ready to review',
        detail: executors.length
          ? `Handled by ${executors.slice(0, 2).join(', ')}${executors.length > 2 ? ` +${executors.length - 2}` : ''}.`
          : 'Completed through Owner Mesh.',
      },
      {
        count: needsApproval,
        label: 'Need approval',
        detail: needsApproval ? 'Approval-sensitive work is waiting in Delegated Work.' : 'No delegated work needs approval right now.',
      },
      {
        count: researchReady,
        label: 'Research briefs',
        detail: researchReady ? 'Review research outputs before they get buried lower in the brief.' : 'No research briefs replayed yet.',
      },
      {
        count: draftReady,
        label: 'Draft-oriented tasks',
        detail: draftReady
          ? 'Draft and follow-up work is ready for final review.'
          : replayedToday
            ? 'Today’s delegated work is not draft-oriented.'
            : 'Nothing replayed from mesh yet today.',
      },
    ];

    grid.innerHTML = cards.map(card => `
      <div class="mesh-activity-card">
        <div>
          <strong>${card.count}</strong>
          <span>${escapeHtml(card.label)}</span>
        </div>
        <div class="mini">${escapeHtml(card.detail)}</div>
      </div>
    `).join('');
    strip.style.display = 'grid';
  }

  function renderMeshAttention(tasks = []) {
    const card = document.getElementById('mesh-attention-card');
    const list = document.getElementById('mesh-attention-list');
    if (!tasks.length) {
      card.style.display = 'none';
      list.innerHTML = '';
      return;
    }

    const reasonCopy = {
      waiting_approval: 'Owner approval is required before this delegated work can move again.',
      retry_needed: 'This delegated task stopped in a terminal state and is a candidate for retry.',
      running: 'Execution is still in progress on Owner Mesh.',
      ready_to_replay: 'The task completed but has not been replayed into Your Day yet.',
    };
    const headlineCopy = {
      waiting_approval: 'Waiting Approval',
      retry_needed: 'Needs Retry',
      running: 'Running',
      ready_to_replay: 'Ready to Replay',
    };

    list.innerHTML = tasks.map(task => `
      <article class="attention-item ${escapeHtml(task.attention_reason || '')}">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(task.description || 'Owner Mesh task')}</h3>
            <p class="reason">${escapeHtml(reasonCopy[task.attention_reason] || 'Owner Mesh attention required.')}</p>
            <div class="task-kicker" style="margin-top:10px;">
              <span class="task-chip warn">${escapeHtml(headlineCopy[task.attention_reason] || 'Needs Attention')}</span>
              <span class="task-chip primary">${escapeHtml(task.workflow_display_name || task.workflow_type || 'Generic Task')}</span>
              <span class="task-chip">${escapeHtml(task.assigned_device_name || task.assigned_device_id || 'Unassigned')}</span>
              <span class="task-chip muted">${escapeHtml(formatTaskStateLabel(task.state))}</span>
              ${task.is_stale_running ? '<span class="task-chip warn">Stale</span>' : ''}
            </div>
          </div>
        </div>
        <div class="task-meta">
          <span><strong>Origin:</strong> ${escapeHtml(task.origin_device_name || task.origin_device_id || 'unknown')}</span>
          ${task.approval_device_name ? `<span><strong>Approval device:</strong> ${escapeHtml(task.approval_device_name)}</span>` : ''}
          <span><strong>Updated:</strong> ${escapeHtml(task.updated_at || task.created_at || 'unknown')}</span>
        </div>
        ${task.result_preview ? `<div class="task-preview"><strong>Latest result:</strong> ${escapeHtml(task.result_preview)}</div>` : ''}
        ${task.context_notes ? `<div class="attention-note">${escapeHtml(task.context_notes)}</div>` : ''}
        <div class="item-actions">
          ${task.can_approve ? `<button class="approve-btn" onclick="approveMeshAttentionTask('${escapeHtml(task.task_id)}')">Approve & Run</button>` : ''}
          ${task.can_deny ? `<button class="deny-btn" onclick="denyMeshAttentionTask('${escapeHtml(task.task_id)}')">Deny</button>` : ''}
          ${task.can_retry ? `<button class="action-btn" onclick="retryMeshAttentionTask('${escapeHtml(task.task_id)}')">Retry</button>` : ''}
          ${task.state === 'completed' && !task.replayed_to_your_day ? `<button class="action-btn" onclick="replayMeshAttentionTask('${escapeHtml(task.task_id)}')">Replay to Your Day</button>` : ''}
          <button class="action-btn danger" onclick="deleteMeshAttentionTask('${escapeHtml(task.task_id)}')">Delete</button>
          <button class="action-btn" onclick="openOwnerMeshForTask('${escapeHtml(task.task_id)}')">Open in Owner Mesh</button>
        </div>
      </article>
    `).join('');
    card.style.display = 'block';
  }

  function closeSourceTaskDetail() {
    document.getElementById('source-task-detail-modal').classList.remove('open');
  }

  function dismissSourceTaskDetail(event) {
    if (event.target.id === 'source-task-detail-modal') {
      closeSourceTaskDetail();
    }
  }

  function renderSourceTaskDetail(task) {
    const kicker = document.getElementById('source-task-detail-kicker');
    const grid = document.getElementById('source-task-detail-grid');
    const notes = document.getElementById('source-task-detail-notes');
    const result = document.getElementById('source-task-detail-result');
    const payload = document.getElementById('source-task-detail-payload');
    const title = document.getElementById('source-task-detail-title');

    title.textContent = task.description || 'Source Task Detail';
    kicker.innerHTML = `
      <span class="task-chip primary">${escapeHtml(task.workflow_display_name || task.workflow_type || 'Generic Task')}</span>
      <span class="task-chip muted">${escapeHtml(formatTaskStateLabel(task.state))}</span>
      ${task.requires_approval ? `<span class="task-chip warn">${escapeHtml(formatApprovalStateLabel(task.approval_state || 'pending'))}</span>` : ''}
      ${task.archived_at ? '<span class="task-chip muted">Archived</span>' : ''}
    `;
    grid.innerHTML = [
      ['Task ID', task.task_id],
      ['Origin', task.origin_device_name || task.origin_device_id || 'unknown'],
      ['Executor', task.assigned_device_name || task.assigned_device_id || 'not assigned'],
      ['Approval Device', task.approval_device_name || task.approval_device_id || 'not required'],
      ['Created', task.created_at || 'unknown'],
      ['Updated', task.updated_at || 'unknown'],
      ['Retries', String(task.retries ?? 0)],
      ['Your Day', task.replayed_to_your_day ? `Replayed${task.your_day_item_id ? ` · ${task.your_day_item_id}` : ''}` : 'Not replayed'],
      ['Archived At', task.archived_at || 'Not archived'],
      ['Archive Reason', task.archive_reason || 'n/a'],
    ].map(([label, value]) => `
      <div class="source-task-modal-field">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(value)}</span>
      </div>
    `).join('');
    notes.textContent = task.context_notes || 'No context notes.';
    result.textContent = task.result || task.result_preview || 'No result recorded yet.';
    payload.textContent = JSON.stringify(task.payload || {}, null, 2);
  }

  async function openSourceTaskDetail(taskId) {
    const response = await fetch(`/api/mesh/tasks/detail/${encodeURIComponent(taskId)}`);
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not load source task details.', 'error');
      return;
    }
    renderSourceTaskDetail(payload.task || {});
    document.getElementById('source-task-detail-modal').classList.add('open');
  }

  function renderItem(item, actionsEnabled) {
    const content = item.content || {};
    const priorityClass = item.type === 'approval_request' ? 'approval' : (item.priority || '').toLowerCase();
    const rows = [];

    if (content.action_description) rows.push(`<div><strong>Action:</strong> ${escapeHtml(content.action_description)}</div>`);
    if (content.action_type) rows.push(`<div><strong>Tool:</strong> ${escapeHtml(content.action_type)}</div>`);
    if (content.sender || content.from) rows.push(`<div><strong>From:</strong> ${escapeHtml(content.sender || content.from)}</div>`);
    if (content.account) rows.push(`<div><strong>Account:</strong> ${escapeHtml(content.account)}</div>`);
    if (content.subject) rows.push(`<div><strong>Subject:</strong> ${escapeHtml(content.subject)}</div>`);
    if (content.summary) rows.push(`<div><strong>Summary:</strong> ${escapeHtml(content.summary)}</div>`);
    if (content.why_it_matters) rows.push(`<div><strong>Why now:</strong> ${escapeHtml(content.why_it_matters)}</div>`);
    if (content.schedule) rows.push(`<div><strong>Schedule:</strong> ${escapeHtml(content.schedule)}</div>`);
    if (content.conflict) rows.push(`<div><strong>Conflict:</strong> ${escapeHtml(content.conflict)}</div>`);
    if (content.suggestion) rows.push(`<div><strong>Suggestion:</strong> ${escapeHtml(content.suggestion)}</div>`);
    if (content.draft) rows.push(`<div><strong>Draft:</strong> ${escapeHtml(content.draft)}</div>`);
    if (item.type !== 'email_digest' && content.body) rows.push(`<div><strong>Body:</strong> ${escapeHtml(content.body)}</div>`);
    if (content.consequence) rows.push(`<div><strong>Consequence:</strong> ${escapeHtml(content.consequence)}</div>`);
    if (content.expires_at) rows.push(`<div><strong>Expires:</strong> ${escapeHtml(content.expires_at)}</div>`);
    if (content.generated_for) rows.push(`<div><strong>Prepared for:</strong> ${escapeHtml(content.generated_for)}</div>`);
    if (content.snoozed_until) rows.push(`<div><strong>Snoozed until:</strong> ${escapeHtml(content.snoozed_until)}</div>`);
    if (content.workflow_display_name) rows.push(`<div><strong>Workflow:</strong> ${escapeHtml(content.workflow_display_name)}</div>`);
    if (content.executor_device_name) rows.push(`<div><strong>Executor:</strong> ${escapeHtml(content.executor_device_name)}</div>`);
    if (content.origin_device_name) rows.push(`<div><strong>Requested by:</strong> ${escapeHtml(content.origin_device_name)}</div>`);
    if (content.replayed_at) rows.push(`<div><strong>Replayed:</strong> ${escapeHtml(content.replayed_at)}</div>`);
    if (content.source === 'owner_mesh') rows.push(`<div><strong>Source:</strong> Owner Mesh</div>`);

    const approvalButtons = item.type === 'approval_request' && content.approval_id ? `
      <div class="approval-actions">
        <button class="approve-btn" ${actionsEnabled ? '' : 'disabled'} onclick="resolveApproval('${escapeHtml(content.approval_id)}', true)">Approve</button>
        <button class="deny-btn" ${actionsEnabled ? '' : 'disabled'} onclick="resolveApproval('${escapeHtml(content.approval_id)}', false)">Deny</button>
      </div>
    ` : '';

    const genericActions = (item.actions || [])
      .filter(action => ['draft_reply', 'dismiss', 'accept', 'open_thread', 'show_original', 'snooze', 'open_as_draft', 'follow_up_task', 'archive_source_task'].includes(action))
      .map(action => {
        let label = action;
        if (action === 'draft_reply') label = 'Draft reply';
        if (action === 'dismiss') label = 'Dismiss';
        if (action === 'accept') label = 'Accept';
        if (action === 'open_thread') label = 'Open thread';
        if (action === 'show_original') label = 'Show original';
        if (action === 'snooze') label = 'Snooze';
        if (action === 'open_as_draft') label = 'Open as draft';
        if (action === 'archive_source_task') label = 'Archive source task';
        if (action === 'follow_up_task') {
          const workflowType = item.content?.workflow_type || '';
          if (workflowType === 'research_brief') label = 'Create action task';
          else if (workflowType === 'email_triage') label = 'Create triage task';
          else if (workflowType === 'daily_preparation') label = 'Create execution task';
          else label = 'Create follow-up';
        }
        return `<button class="action-btn" onclick="runItemAction('${escapeHtml(item.id)}', '${action}')">${label}</button>`;
      })
      .join('');

    const askButton = `<button class="action-btn" onclick="toggleAskPanel('${escapeHtml(item.id)}')">Ask AIDE</button>`;
    const sourceTaskButton = content.source === 'owner_mesh' && content.mesh_task_id
      ? `<button class="action-btn" onclick="openSourceTaskDetail('${escapeHtml(content.mesh_task_id)}')">View source task</button>`
      : '';
    const sendButton = item.type === 'draft_message' && (item.actions || []).includes('send')
      ? `<button class="send-btn" onclick="runItemAction('${escapeHtml(item.id)}', 'send')">Approve & send</button>`
      : '';
    const genericButtons = (genericActions || askButton || sendButton) ? `
      <div class="item-actions">
        ${genericActions}
        ${sendButton}
        ${askButton}
        ${sourceTaskButton}
      </div>
    ` : '';
    const stateBadge = item.state && item.state !== 'new'
      ? `<div class="state-badge">${escapeHtml(item.state)}</div>`
      : '';
    const answerPanel = content.last_aide_answer
      ? `<div class="ask-answer">${escapeHtml(content.last_aide_answer)}</div>`
      : '';
    const askPanel = `
      <div class="ask-panel" id="ask-panel-${escapeHtml(item.id)}" style="display:none;">
        <textarea id="ask-input-${escapeHtml(item.id)}" rows="2" placeholder="Ask AIDE about this item..."></textarea>
        <div class="item-actions">
          <button class="action-btn" onclick="askAideAboutItem('${escapeHtml(item.id)}')">Ask</button>
        </div>
      </div>
    `;
    const draftEditor = item.type === 'draft_message' ? `
      <div class="draft-editor">
        <input id="draft-to-${escapeHtml(item.id)}" value="${escapeHtml(content.to || '')}" placeholder="Recipient email">
        <input id="draft-subject-${escapeHtml(item.id)}" value="${escapeHtml(content.subject || '')}" placeholder="Subject">
        <textarea id="draft-body-${escapeHtml(item.id)}" rows="6" placeholder="Draft body">${escapeHtml(content.body || '')}</textarea>
        <div class="item-actions">
          <button class="action-btn" onclick="saveDraft('${escapeHtml(item.id)}')">Save Draft</button>
        </div>
      </div>
    ` : '';
    const originalPanel = content.original_email ? `
      <div class="context-panel">
        <h4>Original Email</h4>
        <div class="mini">${escapeHtml(content.original_email.from || '')} · ${escapeHtml(content.original_email.date || '')}</div>
        <pre>${escapeHtml(content.original_email.body || '')}</pre>
      </div>
    ` : '';
    const threadPanel = (content.thread_messages || []).length ? `
      <div class="context-panel">
        <h4>Thread</h4>
        <div class="thread-list">
          ${(content.thread_messages || []).map(message => `
            <div class="thread-message">
              <div class="mini">${escapeHtml(message.from || '')} · ${escapeHtml(message.date || '')}</div>
              <div><strong>${escapeHtml(message.subject || '')}</strong></div>
              <pre>${escapeHtml(message.snippet || '')}</pre>
            </div>
          `).join('')}
        </div>
      </div>
    ` : '';
    const quotedPanel = item.type === 'draft_message' && content.quoted_context ? `
      <div class="context-panel">
        <h4>Quoted Context</h4>
        <div class="mini">${escapeHtml(content.quoted_sender || '')} · ${escapeHtml(content.quoted_subject || '')}</div>
        <pre>${escapeHtml(content.quoted_context || '')}</pre>
      </div>
    ` : '';

    return `
      <article class="item">
        <div class="item-top">
          <div>
            <h3>${escapeHtml(item.title || typeLabels[item.type] || 'Item')}</h3>
            <p class="reason">${escapeHtml(item.reason || '')}</p>
            ${stateBadge}
          </div>
          <span class="badge ${priorityClass}">${escapeHtml(typeLabels[item.type] || item.type || 'Item')}</span>
        </div>
        <div class="kv">${rows.join('')}</div>
        ${approvalButtons}
        ${genericButtons}
        ${draftEditor}
        ${quotedPanel}
        ${originalPanel}
        ${threadPanel}
        ${askPanel}
        ${answerPanel}
      </article>
    `;
  }

  function renderSection(targetId, items, emptyLabel, actionsEnabled=false) {
    const el = document.getElementById(targetId);
    if (!items.length) {
      el.innerHTML = `<div class="item"><p class="reason">${escapeHtml(emptyLabel)}</p></div>`;
      return;
    }
    el.innerHTML = items.map(item => renderItem(item, actionsEnabled)).join('');
  }

  function splitItems(items) {
    const visible = items.filter(item => !inactiveStates.has((item.state || 'new').toLowerCase()));
    const delegated = visible.filter(item => item.content?.source === 'owner_mesh');
    const nonDelegated = visible.filter(item => item.content?.source !== 'owner_mesh');
    return {
      approvals: nonDelegated.filter(item => item.type === 'approval_request'),
      emails: nonDelegated.filter(item => item.type === 'email_digest'),
      drafts: nonDelegated.filter(item => item.type === 'draft_message'),
      schedule: nonDelegated.filter(item => item.type === 'calendar_agenda'),
      delegated,
      suggestions: nonDelegated.filter(item => item.type === 'schedule_suggestion'),
      tasks: nonDelegated.filter(item => item.type === 'prepared_task'),
    };
  }

  function renderBrief(data) {
    const refreshBtn = document.getElementById('refresh-btn');
    refreshBtn.disabled = !data.capabilities?.can_refresh;
    const conflictBanner = document.getElementById('conflict-banner');
    const conflictBannerText = document.getElementById('conflict-banner-text');
    const calendarStatusList = document.getElementById('calendar-status-list');

    document.getElementById('brief-date').textContent = `Today · ${data.date}`;
    const sources = data.calendar_status || [];
    calendarStatusList.innerHTML = sources.length ? sources.map(source => `
      <div class="source-row ${source.ok ? 'ok' : 'warn'}">
        <strong>${escapeHtml(source.label)} · ${escapeHtml(source.provider)}</strong>
        <p>${escapeHtml(source.message || '')}</p>
        <div class="mini">
          Today: ${escapeHtml(source.today_event_count || 0)}
          ${source.next_event ? ` · Next: ${escapeHtml(source.next_event.title)} (${escapeHtml(source.next_event.start_at)})` : ''}
        </div>
      </div>
    `).join('') : '<div class="source-row"><p>No calendar sources configured.</p></div>';

    if (!data.available || !data.brief) {
      document.getElementById('headline').textContent = 'No saved brief yet. Generate one to review inbox, schedule, and approvals here.';
      document.getElementById('brief-generated-at').textContent = 'Nothing has been generated for today yet.';
      renderCounts({});
      renderMeshActivity([]);
      renderMeshAttention([]);
      conflictBanner.style.display = 'none';
      document.getElementById('empty-state').style.display = 'block';
      document.getElementById('brief-layout').style.display = 'none';
      return;
    }

    const brief = data.brief;
    document.getElementById('headline').textContent = brief.summary?.headline || 'Your day is ready.';
    document.getElementById('brief-generated-at').textContent = `Generated at ${brief.generated_at || 'unknown time'}`;
    renderCounts(brief.summary?.counts || {});
    const conflictCount = brief.summary?.counts?.calendar_conflicts || 0;
    if (conflictCount > 0) {
      conflictBanner.style.display = 'block';
      conflictBannerText.textContent = `Conflicts detected: ${conflictCount}. Check Suggestions to review the overlap details.`;
    } else {
      conflictBanner.style.display = 'none';
    }

    const groups = splitItems(brief.items || []);
    renderMeshActivity(groups.delegated);
    renderMeshAttention(data.mesh_attention || []);
    renderSection('approvals-list', groups.approvals, 'No approvals are waiting right now.', data.capabilities?.can_approve);
    renderSection('emails-list', groups.emails, 'No unread email digest items for the current brief.');
    renderSection('drafts-list', groups.drafts, 'No draft replies were prepared for today.');
    renderSection('schedule-list', groups.schedule, 'No calendar agenda items are in today’s brief.');
    renderSection('delegated-list', groups.delegated, 'No delegated results have been replayed into Your Day yet.');
    renderSection('suggestions-list', groups.suggestions, 'No schedule suggestions right now.');
    renderSection('tasks-list', groups.tasks, 'No prepared tasks in the brief.');

    document.getElementById('empty-state').style.display = 'none';
    document.getElementById('brief-layout').style.display = 'grid';
  }

  async function loadBrief() {
    loadFinanceMonths();
    const response = await fetch('/api/your-day');
    const data = await response.json();
    renderBrief(data);
  }

  async function refreshBrief() {
    const button = document.getElementById('refresh-btn');
    button.disabled = true;
    const original = button.textContent;
    button.textContent = 'Refreshing...';
    try {
      const response = await fetch('/api/your-day/refresh', {method: 'POST'});
      if (!response.ok) throw new Error('Refresh failed');
      showToast('Your Day refreshed and shared.');
      await loadBrief();
    } catch (error) {
      showToast('Could not refresh Your Day right now.', 'error');
    } finally {
      button.textContent = original;
      button.disabled = false;
    }
  }

  async function resolveApproval(actionId, approved) {
    try {
      const response = await fetch(`/api/your-day/approvals/${encodeURIComponent(actionId)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({approved}),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || 'Approval action failed');
      }
      showToast(approved ? 'Approval recorded.' : 'Action denied.');
      await loadBrief();
    } catch (error) {
      showToast(error.message || 'Approval action failed.', 'error');
    }
  }

  async function runItemAction(itemId, action) {
    try {
      const response = await fetch(`/api/your-day/items/${encodeURIComponent(itemId)}/actions/${encodeURIComponent(action)}`, {
        method: 'POST',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Item action failed');
      }
      const labels = {
        draft_reply: 'Draft reply created.',
        dismiss: 'Item dismissed.',
        accept: 'Suggestion accepted.',
        send: 'Draft approved and sent.',
        open_as_draft: 'Draft opened for editing.',
        follow_up_task: 'Follow-up task created.',
        archive_source_task: 'Source task archived.',
      };
      await loadBrief();
      showToast(labels[action] || 'Item updated.');
    } catch (error) {
      showToast(error.message || 'Item action failed.', 'error');
    }
  }

  async function approveMeshAttentionTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/approve`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not approve task right now.', 'error');
      return;
    }
    showToast('Task approved and re-routed.');
    renderMeshAttention();
  }

  async function denyMeshAttentionTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/deny`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not deny task right now.', 'error');
      return;
    }
    showToast('Task denied and archived.');
    renderMeshAttention();
  }

  async function deleteMeshAttentionTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}`, {method: 'DELETE'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not delete task.', 'error');
      return;
    }
    showToast('Task deleted.');
    renderMeshAttention(payload.tasks || []);
  }

  async function retryMeshAttentionTask(taskId) {



    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/retry`, {method: 'POST'});
    if (!response.ok) {
      showToast('Could not retry task right now.', 'error');
      return;
    }
    showToast('Task retried.');
    await loadBrief();
  }

  async function replayMeshAttentionTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/replay-to-your-day`, {method: 'POST'});
    if (!response.ok) {
      showToast('Could not replay task into Your Day.', 'error');
      return;
    }
    showToast('Task replayed into Your Day.');
    await loadBrief();
  }

  function toggleAskPanel(itemId) {
    const el = document.getElementById(`ask-panel-${itemId}`);
    if (!el) return;
    el.style.display = el.style.display === 'none' ? 'grid' : 'none';
  }

  async function askAideAboutItem(itemId) {
    const input = document.getElementById(`ask-input-${itemId}`);
    const question = (input?.value || '').trim();
    try {
      const response = await fetch(`/api/your-day/items/${encodeURIComponent(itemId)}/ask`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Ask AIDE failed');
      }
      await loadBrief();
      showToast('AIDE responded.');
    } catch (error) {
      showToast(error.message || 'Ask AIDE failed.', 'error');
    }
  }

  async function saveDraft(itemId) {
    const to = document.getElementById(`draft-to-${itemId}`)?.value || '';
    const subject = document.getElementById(`draft-subject-${itemId}`)?.value || '';
    const body = document.getElementById(`draft-body-${itemId}`)?.value || '';
    try {
      const response = await fetch(`/api/your-day/items/${encodeURIComponent(itemId)}/draft`, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({to, subject, body}),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || 'Save draft failed');
      }
      await loadBrief();
      showToast('Draft saved.');
    } catch (error) {
      showToast(error.message || 'Save draft failed.', 'error');
    }
  }

  loadBrief();
</script>
</body>
</html>"""


ONIT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OnIt Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f5f5f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  header {
    background: #1a1a1a;
    color: white;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .dot {
    width: 10px; height: 10px;
    border-radius: 50%;
    background: #1D9E75;
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  header h1 { font-size: 18px; font-weight: 500; }
  .header-link {
    margin-left: 12px;
    color: #d9d9d9;
    text-decoration: none;
    border: 1px solid #3b3b3b;
    padding: 8px 12px;
    border-radius: 999px;
    font-size: 12px;
  }
  .header-link:hover { border-color: #1D9E75; color: white; }
  main {
    flex: 1;
    padding: 24px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    overflow-y: auto;
  }
  .card {
    background: white;
    border: 1px solid #e0e0d8;
    border-radius: 16px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .card h2 { font-size: 16px; color: #666; text-transform: uppercase; letter-spacing: 0.05em; }
  #session-status {
    font-size: 24px;
    font-weight: 700;
    padding: 16px;
    border-radius: 12px;
    text-align: center;
    background: #fafaf8;
    border: 1px solid #e0e0d8;
  }
  .active { background: #e7f7ef !important; color: #1f7a4d !important; border-color: #1D9E75 !important; }
  #live-transcript {
    flex: 1;
    padding: 16px;
    background: #fafaf8;
    border: 1px solid #e0e0d8;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.6;
    overflow-y: auto;
    white-space: pre-wrap;
    min-height: 300px;
  }
  .controls {
    display: flex;
    gap: 12px;
    justify-content: center;
    margin-top: 12px;
  }
  button {
    padding: 10px 20px;
    border-radius: 8px;
    border: 1px solid #d0d0c8;
    background: white;
    cursor: pointer;
    font-size: 14px;
  }
  button.primary { background: #1a1a1a; color: white; border: none; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>
<header>
  <div class="dot"></div>
  <h1>OnIt Voice Dashboard</h1>
  <a class="header-link" href="/">Chat</a>
  <a class="header-link" href="/your-day">Your Day</a>
</header>
<main>
  <div class="card">
    <h2>Session Control</h2>
    <div id="session-status">Idle</div>
    <div class="controls">
      <button id="start-btn" class="primary" onclick="triggerSession('start')">Start Session</button>
      <button id="stop-btn" onclick="triggerSession('stop')" disabled>Stop Session</button>
    </div>
    <div style="margin-top: 20px; font-size: 13px; color: #666;">
      Note: This triggers the OnIt extension. Ensure the extension is installed and active in your browser.
    </div>
  </div>
  <div class="card">
    <h2>Live Transcription</h2>
    <div id="live-transcript">Waiting for session to start...</div>
  </div>
</main>
<script>
  async function triggerSession(action) {
    const res = await fetch('/api/onit/session/trigger', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        session_id: 'native-session-' + Date.now(),
        action: action,
        task_name: action === 'start' ? prompt('What is the task name?') : null
      })
    });
    if (res.ok) {
      updateUI(action);
    }
  }

  function updateUI(action) {
    const status = document.getElementById('session-status');
    const startBtn = document.getElementById('start-btn');
    const stopBtn = document.getElementById('stop-btn');
    if (action === 'start') {
      status.textContent = 'Recording...';
      status.classList.add('active');
      startBtn.disabled = true;
      stopBtn.disabled = false;
      document.getElementById('live-transcript').textContent = 'Listening...';
    } else {
      status.textContent = 'Idle';
      status.classList.remove('active');
      startBtn.disabled = false;
      stopBtn.disabled = true;
    }
  }

  // Poll for updates if needed or use WebSocket/SSE
  setInterval(async () => {
    // In a real impl, we'd fetch the current active session transcript
    // For now, this is a structural placeholder
  }, 2000);
</script>
</body>
</html>"""

M_PEER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>M-Peer</title>
<style>
  :root {
    --bg: #f4efe6;
    --panel: rgba(255, 252, 245, 0.92);
    --line: rgba(24, 33, 27, 0.12);
    --ink: #18211b;
    --muted: #5b655d;
    --accent: #2f7d5e;
    --danger: #8f4634;
    --warn: #b56a1b;
    --shadow: 0 18px 48px rgba(24, 33, 27, 0.12);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(circle at top left, rgba(47,125,94,0.16), transparent 28%),
      linear-gradient(180deg, #f8f4ea 0%, var(--bg) 100%);
    color: var(--ink);
    font-family: Georgia, 'Times New Roman', serif;
  }
  .shell { max-width: 1180px; margin: 0 auto; padding: 36px 24px 64px; }
  .hero, .panel, .peer-card, .template-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 28px;
    box-shadow: var(--shadow);
  }
  .hero, .panel, .peer-card, .template-card { padding: 24px; }
  .eyebrow {
    display: inline-flex;
    padding: 7px 12px;
    border-radius: 999px;
    border: 1px solid rgba(47,125,94,0.18);
    background: rgba(47,125,94,0.08);
    color: #19553d;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  h1 { margin: 16px 0 8px; font-size: clamp(40px, 5vw, 68px); line-height: .96; letter-spacing: -0.04em; }
  .headline { margin: 0; color: var(--muted); line-height: 1.6; max-width: 72ch; font-size: 17px; }
  .hero-actions { margin-top: 20px; display: flex; gap: 10px; flex-wrap: wrap; }
  .promise {
    margin-top: 16px;
    padding: 16px 18px;
    border-radius: 18px;
    background: rgba(47,125,94,0.08);
    border: 1px solid rgba(47,125,94,0.14);
    color: #19553d;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
  }
  a.nav-link, button {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 11px 16px;
    background: rgba(24,33,27,0.06);
    color: var(--ink);
    text-decoration: none;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  .layout { display: grid; gap: 18px; margin-top: 22px; }
  .intro-grid { display: grid; gap: 18px; grid-template-columns: 1.1fr 0.9fr; }
  .template-grid, .peer-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
  .task-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .peer-card h3, .template-card h3, .panel h2 { margin: 0 0 8px; letter-spacing: -0.03em; }
  .panel h2 { font-size: 24px; }
  .muted { color: var(--muted); font-family: 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; }
  ul { margin: 0; padding-left: 20px; }
  li { margin: 8px 0; font-family: 'Helvetica Neue', Arial, sans-serif; color: var(--muted); }
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.08);
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    white-space: nowrap;
  }
  .risk-low { background: rgba(47,125,94,0.14); color: #19553d; }
  .risk-medium { background: rgba(181,106,27,0.14); color: var(--warn); }
  .risk-high { background: rgba(143,70,52,0.12); color: var(--danger); }
  .warning { background: rgba(181,106,27,0.14); color: var(--warn); }
  .revoked { background: rgba(143,70,52,0.12); color: var(--danger); }
  .meta { display: grid; gap: 8px; margin-top: 14px; font-family: 'Helvetica Neue', Arial, sans-serif; color: var(--muted); font-size: 14px; }
  .caps { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
  .form-grid { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  label { display: grid; gap: 6px; font-family: 'Helvetica Neue', Arial, sans-serif; color: var(--muted); font-size: 14px; }
  input, textarea, select, button {
    width: 100%;
    border-radius: 14px;
    border: 1px solid rgba(24,33,27,0.12);
    padding: 12px 14px;
    font: inherit;
    background: rgba(255,255,255,0.85);
    color: var(--ink);
  }
  textarea { min-height: 110px; resize: vertical; }
  button {
    cursor: pointer;
    background: var(--accent);
    color: white;
    border: none;
    font-weight: 600;
  }
  button.secondary {
    background: rgba(24,33,27,0.08);
    color: var(--ink);
  }
  .task-actions { display: flex; gap: 10px; margin-top: 14px; }
  .task-actions button { width: auto; }
  .step-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    padding: 6px 12px;
    border-radius: 999px;
    background: rgba(24,33,27,0.06);
    border: 1px solid rgba(24,33,27,0.08);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .status-note {
    margin-top: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    color: var(--muted);
  }
  .empty {
    padding: 28px;
    text-align: center;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  @media (max-width: 940px) {
    .shell { padding: 20px 16px 44px; }
    .intro-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Layer II · M-Peer</div>
      <h1>Sovereign Peer Agents</h1>
      <p class="headline">M-Peer is how your AIDE can collaborate with another person's AIDE without either of you giving up control. These peers are not your devices. They are separate agents with explicit scopes, revocable trust, and no automatic memory sharing.</p>
      <div class="promise"><strong>Plain-language promise:</strong> My AIDE can ask your AIDE for help, but neither of us gives up control.</div>
      <div class="hero-actions">
        <a class="nav-link" href="/mesh">Owner Mesh</a>
        <a class="nav-link" href="/your-day">Your Day</a>
        <a class="nav-link" href="/">Chat</a>
      </div>
    </section>

    <section class="layout">
      <div class="intro-grid">
        <section class="panel">
          <h2 id="m-peer-headline">What M-Peer means</h2>
          <p id="m-peer-body" class="muted"></p>
        </section>
        <section class="panel">
          <h2>How This Works In 3 Steps</h2>
          <ul id="m-peer-safe-defaults"></ul>
          <ul id="m-peer-onboarding-steps"></ul>
        </section>
      </div>

      <section class="panel">
        <div class="step-badge">Step 2</div>
        <h2>Choose How Another AIDE Can Work With You</h2>
        <p class="muted">Start with a simple relationship type. These templates are written to be understandable first and editable later.</p>
        <div id="template-grid" class="template-grid"></div>
      </section>

      <div class="intro-grid">
        <section class="panel">
          <div class="step-badge">Step 1</div>
          <h2>Invite Your AIDE</h2>
          <p class="muted">Show this QR code or copy the invite when you want another person to connect their AIDE to yours. This shares identity, not memory.</p>
          <div id="m-peer-invite-card"></div>
        </section>
        <section class="panel">
          <div class="step-badge">Step 1</div>
          <h2>Connect Another AIDE</h2>
          <p class="muted">Paste another person's invite and choose the relationship you want from day one. Start with the smallest relationship that fits.</p>
          <div class="form-grid">
            <label>Trust template
              <select id="m-peer-connect-scope"></select>
            </label>
          </div>
          <div id="m-peer-template-preview" class="peer-card" style="margin-top:14px;"></div>
          <div class="form-grid" style="margin-top:14px;">
            <label>Invitation payload
              <textarea id="m-peer-connect-payload" placeholder="Paste the signed peer invitation payload here."></textarea>
            </label>
          </div>
          <div class="task-actions">
            <button id="m-peer-connect-button" type="button">Connect Peer</button>
          </div>
          <div id="m-peer-connect-status" class="status-note"></div>
        </section>
      </div>

      <section class="panel">
        <h2>People You've Connected</h2>
        <p class="muted">These are external AIDEs, not owner devices. You can rename the relationship, change its template, add notes, pause it, or revoke it any time.</p>
        <div id="peer-grid" class="peer-grid"></div>
        <div id="peer-empty" class="empty" style="display:none;">No connected peers yet. Start by sharing your invite or pasting someone else's invitation above.</div>
      </section>

      <section id="peer-task-panel" class="panel">
        <div class="step-badge">Step 3</div>
        <h2>Ask Another AIDE For Help</h2>
        <p class="muted">Once you've connected someone, you can send a narrow, explicit request here. Keep the ask simple and the shared context intentional.</p>
        <div class="form-grid">
          <label>Peer
            <select id="peer-task-peer"></select>
          </label>
          <label>Task type
            <select id="peer-task-type"></select>
          </label>
          <label>Context type
            <select id="peer-task-context-type"></select>
          </label>
          <label>Title
            <input id="peer-task-title" placeholder="Compare our availability windows">
          </label>
        </div>
        <div class="form-grid" style="margin-top:14px;">
          <label>Instruction
            <textarea id="peer-task-instruction" placeholder="Please compare these times and suggest the best overlap."></textarea>
          </label>
          <label>Context
            <textarea id="peer-task-context" placeholder='Optional. Plain text or JSON, for example {"facts":["Tuesday 11:00-12:00"]}'></textarea>
          </label>
        </div>
        <div class="task-actions">
          <button id="peer-task-send" type="button">Send Peer Task</button>
        </div>
        <div id="peer-task-status" class="status-note"></div>
        <div id="peer-task-empty" class="empty" style="display:none;">Step 3 unlocks after you connect at least one peer.</div>
      </section>

      <section class="panel">
        <h2>Recent Peer Tasks</h2>
        <p class="muted">This is the bilateral task timeline. It shows which side asked, which side is responding, and where approvals or execution currently stand.</p>
        <div id="task-grid" class="task-grid"></div>
        <div id="task-empty" class="empty" style="display:none;">No peer tasks yet.</div>
      </section>
    </section>
  </div>
<script>
  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function renderTemplates(templates) {
    const el = document.getElementById('template-grid');
    if (!templates.length) {
      el.innerHTML = '<div class="empty">No templates have been seeded yet.</div>';
      return;
    }
    el.innerHTML = templates.map(template => {
      const risk = (template.risk_level || 'low').toLowerCase();
      const tasks = (() => {
        try {
          const parsed = JSON.parse(template.allowed_task_types || '[]');
          return Array.isArray(parsed) ? parsed.join(', ') : '';
        } catch (_) {
          return '';
        }
      })();
      return `
        <article class="template-card">
          <div class="badge risk-${escapeHtml(risk)}">${escapeHtml(risk)} risk</div>
          <h3>${escapeHtml(template.scope_name || template.scope_id)}</h3>
          <p class="muted">${escapeHtml(template.description || '')}</p>
          <div class="meta">
            <div>${escapeHtml(template.allows_summary || '')}</div>
            <div>${escapeHtml(template.context_summary || '')}</div>
            <div>${escapeHtml(template.memory_summary || '')}</div>
            <div>${escapeHtml(template.approval_summary || '')}</div>
            <div><strong>Tasks:</strong> ${escapeHtml(tasks || 'none')}</div>
            <div><strong>Context policy:</strong> ${escapeHtml(template.can_receive_context || 'explicit_only')}</div>
            <div><strong>Sandbox:</strong> ${escapeHtml(template.sandbox_mode || 'strict')}</div>
          </div>
        </article>
      `;
    }).join('');
  }

  function scopeOptionsHtml(templates, selectedScopeId) {
    return (templates || []).map(template => `
      <option value="${escapeHtml(template.scope_id)}" ${template.scope_id === selectedScopeId ? 'selected' : ''}>
        ${escapeHtml(template.scope_name || template.scope_id)}
      </option>
    `).join('');
  }

  function renderInvitation(invitation, templates) {
    const inviteCard = document.getElementById('m-peer-invite-card');
    const connectScope = document.getElementById('m-peer-connect-scope');
    connectScope.innerHTML = scopeOptionsHtml(templates, 'assistant_introduction');
    connectScope.onchange = () => renderSelectedTemplatePreview(templates);
    if (!invitation || !invitation.available) {
      inviteCard.innerHTML = '<div class="empty">Local peer identity is not ready yet.</div>';
      return;
    }
    inviteCard.innerHTML = `
      <article class="peer-card">
        <div class="badge">shareable</div>
        <h3>${escapeHtml(invitation.display_name || 'Local AIDE')}</h3>
        <p class="muted">Owner: ${escapeHtml(invitation.owner_name || 'Unknown')}</p>
        <div style="margin-top:16px; display:flex; justify-content:center;">
          <img src="${escapeHtml(invitation.qr_data_uri || '')}" alt="M-Peer invitation QR" style="width:min(280px,100%); border-radius:20px; border:1px solid rgba(24,33,27,0.12); background:white; padding:12px;">
        </div>
        <div class="form-grid" style="margin-top:14px;">
          <label>Invitation payload
            <textarea id="m-peer-invite-payload" readonly>${escapeHtml(invitation.payload || '')}</textarea>
          </label>
        </div>
        <div class="task-actions">
          <button type="button" onclick="copyPeerInvitation()">Copy Payload</button>
        </div>
      </article>
    `;
    renderSelectedTemplatePreview(templates);
  }

  function renderSelectedTemplatePreview(templates) {
    const preview = document.getElementById('m-peer-template-preview');
    const selectedId = document.getElementById('m-peer-connect-scope').value;
    const template = (templates || []).find(item => item.scope_id === selectedId) || templates?.[0];
    if (!template) {
      preview.innerHTML = '<div class="empty">Pick a trust template to see what it means.</div>';
      return;
    }
    preview.innerHTML = `
      <div class="badge risk-${escapeHtml((template.risk_level || 'low').toLowerCase())}">${escapeHtml(template.risk_level || 'low')} risk</div>
      <h3 style="margin-top:10px;">${escapeHtml(template.scope_name || template.scope_id)}</h3>
      <p class="muted">${escapeHtml(template.description || '')}</p>
      <div class="meta">
        <div>${escapeHtml(template.allows_summary || '')}</div>
        <div>${escapeHtml(template.context_summary || '')}</div>
        <div>${escapeHtml(template.memory_summary || '')}</div>
        <div>${escapeHtml(template.approval_summary || '')}</div>
        <div>${escapeHtml(template.change_later_summary || '')}</div>
      </div>
    `;
  }

  function renderPeers(peers, templates) {
    const grid = document.getElementById('peer-grid');
    const empty = document.getElementById('peer-empty');
    if (!peers.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = peers.map(peer => `
      <article class="peer-card">
        <div class="badge ${peer.revoked_at ? 'revoked' : (peer.paused ? 'warning' : 'risk-' + escapeHtml((peer.risk_level || 'low').toLowerCase()))}">
          ${peer.revoked_at ? 'revoked' : (peer.paused ? 'paused' : escapeHtml((peer.risk_level || 'low') + ' risk'))}
        </div>
        <h3>${escapeHtml(peer.display_name || peer.peer_agent_id)}</h3>
        <p class="muted">${escapeHtml(peer.scope_description || 'No scope description yet.')}</p>
        <div class="meta">
          <div><strong>Owner:</strong> ${escapeHtml(peer.owner_name || 'Unknown')}</div>
          <div><strong>Scope:</strong> ${escapeHtml(peer.scope_name || peer.scope_id || 'Unknown')}</div>
          <div><strong>Connection:</strong> ${escapeHtml(peer.connection_type || 'offline')}</div>
          <div><strong>Paired:</strong> ${escapeHtml(peer.paired_at || 'Unknown')}</div>
          <div><strong>Last seen:</strong> ${escapeHtml(peer.last_seen_at || 'Not seen yet')}</div>
          <div>${escapeHtml(peer.allows_summary || '')}</div>
          <div>${escapeHtml(peer.context_summary || '')}</div>
          <div>${escapeHtml(peer.memory_summary || '')}</div>
          <div>${escapeHtml(peer.approval_summary || '')}</div>
          <div>${escapeHtml(peer.change_later_summary || '')}</div>
          ${peer.pause_reason ? `<div><strong>Pause reason:</strong> ${escapeHtml(peer.pause_reason)}</div>` : ''}
          ${peer.revocation_reason ? `<div><strong>Revocation reason:</strong> ${escapeHtml(peer.revocation_reason)}</div>` : ''}
        </div>
        <div class="caps">
          ${(peer.capabilities || []).map(cap => `<span class="badge">${escapeHtml(cap)}</span>`).join('')}
        </div>
        <div class="form-grid" style="margin-top:14px;">
          <label>Peer label
            <input id="peer-name-${escapeHtml(peer.peer_agent_id)}" value="${escapeHtml(peer.display_name || '')}">
          </label>
          <label>Trust template
            <select id="peer-scope-${escapeHtml(peer.peer_agent_id)}">
              ${scopeOptionsHtml(templates, peer.scope_id)}
            </select>
          </label>
          <label>Relationship notes
            <textarea id="peer-notes-${escapeHtml(peer.peer_agent_id)}" placeholder="What is this peer relationship for?">${escapeHtml(peer.trust_notes || '')}</textarea>
          </label>
        </div>
        <div class="task-actions">
          <button class="secondary" type="button" onclick="renamePeer('${escapeHtml(peer.peer_agent_id)}')">Rename</button>
          <button class="secondary" type="button" onclick="changePeerScope('${escapeHtml(peer.peer_agent_id)}')">Change Template</button>
          <button class="secondary" type="button" onclick="savePeerNotes('${escapeHtml(peer.peer_agent_id)}')">Save Notes</button>
          ${peer.paused
            ? `<button class="secondary" type="button" onclick="resumePeer('${escapeHtml(peer.peer_agent_id)}')">Resume</button>`
            : `<button class="secondary" type="button" onclick="pausePeer('${escapeHtml(peer.peer_agent_id)}')">Pause</button>`}
          ${peer.revoked_at
            ? `<button class="secondary" type="button" onclick="restorePeer('${escapeHtml(peer.peer_agent_id)}')">Restore</button>`
            : `<button class="secondary" type="button" onclick="revokePeer('${escapeHtml(peer.peer_agent_id)}')">Revoke</button>`}
        </div>
      </article>
    `).join('');
  }

  function parseJsonList(value) {
    try {
      const parsed = JSON.parse(value || '[]');
      return Array.isArray(parsed) ? parsed : [];
    } catch (_) {
      return [];
    }
  }

  function buildPeerScopeLookup(peers) {
    const lookup = {};
    for (const peer of peers || []) {
      lookup[peer.peer_agent_id] = peer;
    }
    return lookup;
  }

  function renderTaskComposer(peers) {
    const peerSelect = document.getElementById('peer-task-peer');
    const typeSelect = document.getElementById('peer-task-type');
    const contextSelect = document.getElementById('peer-task-context-type');
    const panel = document.getElementById('peer-task-panel');
    const emptyState = document.getElementById('peer-task-empty');
    const sendButton = document.getElementById('peer-task-send');
    const activePeers = (peers || []).filter(peer => !peer.revoked_at && !peer.paused);
    if (!activePeers.length) {
      panel.style.opacity = '0.72';
      peerSelect.innerHTML = '<option value="">No peers available</option>';
      typeSelect.innerHTML = '<option value="">No task types</option>';
      contextSelect.innerHTML = '<option value="explicit_facts">explicit_facts</option>';
      sendButton.disabled = true;
      emptyState.style.display = 'block';
      return;
    }
    panel.style.opacity = '1';
    sendButton.disabled = false;
    emptyState.style.display = 'none';
    peerSelect.innerHTML = activePeers.map(peer => `<option value="${escapeHtml(peer.peer_agent_id)}">${escapeHtml(peer.display_name || peer.peer_agent_id)}</option>`).join('');
    const syncScopeFields = () => {
      const current = activePeers.find(peer => peer.peer_agent_id === peerSelect.value) || activePeers[0];
      const taskTypes = parseJsonList(current.allowed_task_types || '[]');
      const contextTypes = parseJsonList(current.allowed_context_types || '[]');
      typeSelect.innerHTML = taskTypes.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('') || '<option value="research">research</option>';
      contextSelect.innerHTML = contextTypes.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('') || '<option value="explicit_facts">explicit_facts</option>';
    };
    peerSelect.onchange = syncScopeFields;
    syncScopeFields();
  }

  function renderRecentTasks(tasks) {
    const grid = document.getElementById('task-grid');
    const empty = document.getElementById('task-empty');
    if (!tasks.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = tasks.map(task => `
      <article class="peer-card">
        <div class="badge">${escapeHtml(task.current_state || 'unknown')}</div>
        <h3>${escapeHtml(task.title || task.task_type || task.request_id)}</h3>
        <p class="muted">${escapeHtml(task.result_summary || 'No result summary yet.')}</p>
        <div class="meta">
          <div><strong>From:</strong> ${escapeHtml(task.requesting_peer_name || task.requesting_peer_id || 'Unknown')}</div>
          <div><strong>To:</strong> ${escapeHtml(task.responding_peer_name || task.responding_peer_id || 'Unknown')}</div>
          <div><strong>Requester approval:</strong> ${escapeHtml(task.requesting_approval_state || 'unknown')}</div>
          <div><strong>Responder approval:</strong> ${escapeHtml(task.responding_approval_state || 'unknown')}</div>
          <div><strong>Requested:</strong> ${escapeHtml(task.requested_at || 'Unknown')}</div>
          ${task.execution_time_ms ? `<div><strong>Execution:</strong> ${escapeHtml(String(task.execution_time_ms))} ms</div>` : ''}
        </div>
        ${task.can_cancel ? `<div class="task-actions"><button class="secondary" type="button" onclick="cancelPeerTask('${escapeHtml(task.request_id)}')">Cancel</button></div>` : ''}
      </article>
    `).join('');
  }

  async function sendPeerTask() {
    const peerId = document.getElementById('peer-task-peer').value;
    if (!peerId) return;
    const body = {
      title: document.getElementById('peer-task-title').value.trim(),
      instruction: document.getElementById('peer-task-instruction').value.trim(),
      task_type: document.getElementById('peer-task-type').value,
      context_type: document.getElementById('peer-task-context-type').value,
      context_text: document.getElementById('peer-task-context').value.trim() || null,
    };
    const status = document.getElementById('peer-task-status');
    status.textContent = 'Sending...';
    const response = await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) {
      status.textContent = payload.detail || 'Could not send peer task.';
      return;
    }
    status.textContent = payload.status === 'awaiting_requester_approval'
      ? 'Task created and waiting for your approval.'
      : `Task sent. Current state: ${payload.status || 'routed'}.`;
    await loadMPeer();
  }

  async function cancelPeerTask(requestId) {
    const response = await fetch(`/api/m-peer/tasks/${encodeURIComponent(requestId)}/cancel`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'Cancelled from dashboard.' }),
    });
    const payload = await response.json();
    const status = document.getElementById('peer-task-status');
    if (!response.ok) {
      status.textContent = payload.detail || 'Could not cancel peer task.';
      return;
    }
    status.textContent = 'Peer task cancelled.';
    await loadMPeer();
  }

  async function copyPeerInvitation() {
    const field = document.getElementById('m-peer-invite-payload');
    if (!field) return;
    await navigator.clipboard.writeText(field.value);
  }

  async function acceptPeerInvitation() {
    const payload = document.getElementById('m-peer-connect-payload').value.trim();
    const trust_scope_id = document.getElementById('m-peer-connect-scope').value;
    const status = document.getElementById('m-peer-connect-status');
    if (!payload) {
      status.textContent = 'Paste a signed invitation payload first.';
      return;
    }
    status.textContent = 'Connecting peer...';
    const response = await fetch('/api/m-peer/invitations/accept', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ payload, trust_scope_id }),
    });
    const result = await response.json();
    if (!response.ok) {
      status.textContent = result.detail || 'Could not accept peer invitation.';
      return;
    }
    document.getElementById('m-peer-connect-payload').value = '';
    status.textContent = 'Peer connected successfully.';
    await loadMPeer();
  }

  async function renamePeer(peerId) {
    const input = document.getElementById(`peer-name-${peerId}`);
    const name = input?.value?.trim() || '';
    if (!name) return;
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/name`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    await loadMPeer();
  }

  async function changePeerScope(peerId) {
    const select = document.getElementById(`peer-scope-${peerId}`);
    const trust_scope_id = select?.value || '';
    if (!trust_scope_id) return;
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/scope`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trust_scope_id }),
    });
    await loadMPeer();
  }

  async function pausePeer(peerId) {
    const reason = window.prompt('Why are you pausing this collaboration?', 'Paused from dashboard.') || 'Paused from dashboard.';
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/pause`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    await loadMPeer();
  }

  async function resumePeer(peerId) {
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/resume`, { method: 'POST' });
    await loadMPeer();
  }

  async function revokePeer(peerId) {
    const reason = window.prompt('Why are you revoking this peer?', 'Revoked from dashboard.') || 'Revoked from dashboard.';
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
    await loadMPeer();
  }

  async function restorePeer(peerId) {
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/restore`, { method: 'POST' });
    await loadMPeer();
  }

  async function savePeerNotes(peerId) {
    const notes = document.getElementById(`peer-notes-${peerId}`)?.value || '';
    await fetch(`/api/m-peer/peers/${encodeURIComponent(peerId)}/notes`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes }),
    });
    await loadMPeer();
  }

  async function loadMPeer() {
    const response = await fetch('/api/m-peer');
    const payload = await response.json();
    document.getElementById('m-peer-headline').textContent = payload.intro?.headline || 'What M-Peer means';
    document.getElementById('m-peer-body').textContent = payload.intro?.body || '';
    document.getElementById('m-peer-safe-defaults').innerHTML = (payload.safe_defaults || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    document.getElementById('m-peer-onboarding-steps').innerHTML = (payload.onboarding_steps || []).map(item => `<li>${escapeHtml(item)}</li>`).join('');
    renderInvitation(payload.invitation || {}, payload.scope_templates || []);
    renderTemplates(payload.scope_templates || []);
    renderPeers(payload.peers || [], payload.scope_templates || []);
    renderTaskComposer(payload.peers || []);
    renderRecentTasks(payload.recent_tasks || []);
  }

  document.getElementById('peer-task-send').addEventListener('click', sendPeerTask);
  document.getElementById('m-peer-connect-button').addEventListener('click', acceptPeerInvitation);
  loadMPeer();
</script>
</body>
</html>"""


MESH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Owner Mesh</title>
<style>
  :root {
    --bg: #f4f0e4;
    --panel: rgba(255, 252, 245, 0.92);
    --line: rgba(24, 33, 27, 0.12);
    --ink: #18211b;
    --muted: #5b655d;
    --accent: #2f7d5e;
    --danger: #8f4634;
    --warn: #b56a1b;
    --shadow: 0 18px 48px rgba(24, 33, 27, 0.12);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background: #f6f7f9;
    color: #111;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    overflow-x: hidden;
  }
  * { box-sizing: border-box; min-width: 0; }
  .shell { 
    width: 100%;
    max-width: 1200px; 
    margin-inline: auto; 
    padding: 24px; 
  }
  .layout { 
    display: grid; 
    grid-template-columns: 1fr; 
    gap: 20px; 
    width: 100%;
  }
  .stack { 
    display: grid; 
    gap: 20px; 
    min-width: 0;
  }
  .task-item, .task-title, .task-header, .section-head { min-width: 0; }
  
  h1 { font-size: 24px; font-weight: 700; line-height: 1.2; margin-bottom: 8px; color: #111; }
  .section-head h2 { font-size: 16px; font-weight: 600; margin: 0 0 8px 0; color: #111; }
  .section-head p { font-size: 13px; color: #666; margin-bottom: 16px; line-height: 1.5; }
  
  .hero {
    margin-bottom: 24px;
    text-align: left;
  }
  .hero .headline { font-size: 15px; color: #666; max-width: 700px; line-height: 1.5; }
  
  .hero, .peer-card, .pairing-card, .discovery-card, .tasks-card { 
    padding: 20px; 
    border-radius: 12px; 
    background: #fff;
    border: 1px solid #ddd;
    box-shadow: 0 2px 4px rgba(0,0,0,0.02);
  }
  
  @media (min-width: 1100px) {
    .layout { grid-template-columns: 1fr 1.2fr 1fr; }
  }
  
  .peer-grid { 
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
    gap: 16px; 
  }
  
  button, a.nav-link, input, textarea, select { 
    font-family: inherit;
    border-radius: 6px;
    border: 1px solid #ddd;
    transition: all 0.1s ease;
  }
  button { 
    cursor: pointer; 
    font-weight: 500;
    background: #fff;
    color: #333;
  }
  button:hover { background: #f9f9f9; border-color: #ccc; }
  button.primary { background: #111; color: #fff; border: none; }
  button.primary:hover { background: #333; }
  button.danger { color: #d32f2f; }
  button.danger:hover { background: #fef2f2; border-color: #fca5a5; }
  
  a.nav-link { 
    text-decoration: none; 
    color: #666; 
    font-size: 13px; 
    padding: 6px 12px;
    background: #fff;
    border: 1px solid #ddd;
  }
  a.nav-link:hover { color: #111; background: #f9f9f9; }
  
  input, textarea, select { 
    padding: 6px 10px; 
    font-size: 13px; 
    background: #fff;
  }
  .shell { 
    width: 100%;
    max-width: 1100px; 
    margin-inline: auto; 
    padding: clamp(20px, 5vw, 40px); 
  }
  .layout { 
    display: grid; 
    grid-template-columns: 1fr; 
    gap: 20px; 
    width: 100%;
  }
  .hero, .peer-card, .pairing-card, .discovery-card, .tasks-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 28px;
    box-shadow: var(--shadow);
  }
  .hero { padding: 28px; }
  .eyebrow {
    display: inline-flex;
    padding: 7px 12px;
    border-radius: 999px;
    border: 1px solid rgba(47,125,94,0.18);
    background: rgba(47,125,94,0.08);
    color: #19553d;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 12px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  h1 { margin: 16px 0 8px; font-size: clamp(40px, 5vw, 68px); line-height: .96; letter-spacing: -0.04em; }
  .headline { margin: 0; color: var(--muted); line-height: 1.6; max-width: 70ch; font-size: 17px; }
  .hero-actions, .peer-actions, .toggle-row, .pairing-actions, .discovery-actions { display: flex; gap: 10px; flex-wrap: wrap; }
  .hero-actions { margin-top: 20px; }
  .hero-subnav-note {
    margin-top: 12px;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }
  .hero-subnav-note a {
    color: #19553d;
    text-decoration: none;
    border-bottom: 1px solid rgba(25,85,61,0.18);
  }
  .layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.95fr); gap: 18px; margin-top: 22px; align-items: start; }
  .stack { display: grid; gap: 18px; }
  .peer-grid { display: grid; gap: 18px; }
  .pairing-card, .discovery-card, .peer-card, .tasks-card { padding: 22px; }
  .section-head { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; margin-bottom: 14px; }
  .section-head h2 { margin: 0; font-size: 24px; letter-spacing: -0.03em; }
  .section-head p { margin: 0; color: var(--muted); font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 13px; }
  .peer-top { display: flex; justify-content: space-between; gap: 14px; align-items: flex-start; }
  .peer-top, .task-header, .section-head { min-width: 0; }
  .peer-top h3 { margin: 0; font-size: 22px; letter-spacing: -0.03em; }
  .meta { margin-top: 14px; display: grid; gap: 8px; color: var(--muted); font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 14px; }
  .badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.08);
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    white-space: nowrap;
  }
  .badge.trusted { background: rgba(47,125,94,0.14); color: #19553d; }
  .badge.revoked { background: rgba(143,70,52,0.12); color: var(--danger); }
  .badge.discovery { background: rgba(181,106,27,0.14); color: var(--warn); }
  .caps { margin-top: 16px; display: grid; gap: 10px; }
  .toggle-row {
    justify-content: space-between;
    align-items: center;
    padding: 10px 12px;
    background: rgba(255,255,255,0.84);
    border: 1px solid var(--line);
    border-radius: 16px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  button, a.nav-link, input[type="text"], textarea, select {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  button, a.nav-link {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 11px 16px;
    background: rgba(24,33,27,0.06);
    color: var(--ink);
    cursor: pointer;
    text-decoration: none;
  }
  button.primary { background: var(--ink); color: white; }
  button.danger { background: rgba(143,70,52,0.12); color: var(--danger); border-color: rgba(143,70,52,0.18); }
  button.warn { background: rgba(181,106,27,0.14); color: var(--warn); border-color: rgba(181,106,27,0.2); }
  button:disabled { opacity: 0.6; cursor: not-allowed; }
  input[type="text"], textarea, select {
    width: 100%;
    padding: 11px 13px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.92);
    color: var(--ink);
  }
  textarea { min-height: 132px; resize: vertical; }
  .task-form {
    margin-top: 14px;
    display: grid;
    gap: 12px;
  }
  .task-form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  .task-checkbox {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
  }
  .task-list {
    display: grid;
    gap: 12px;
  }
  .collapsible-card {
    overflow: hidden;
  }
  .collapsible-card summary {
    list-style: none;
    cursor: pointer;
  }
  .collapsible-card summary::-webkit-details-marker {
    display: none;
  }
  .collapsible-card .section-head {
    margin-bottom: 0;
  }
  .collapsible-card .collapse-body {
    margin-top: 14px;
  }
  .collapsible-card summary::after {
    content: 'Show';
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .collapsible-card[open] summary::after {
    content: 'Hide';
  }
  .task-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    justify-content: flex-end;
  }
  .task-item {
    padding: 16px 18px;
    border-radius: 20px;
    background: rgba(255,255,255,0.84);
    border: 1px solid var(--line);
    display: grid;
    gap: 12px;
  }
  .task-header {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    align-items: flex-start;
  }
  .task-title {
    display: grid;
    gap: 6px;
    min-width: 0;
  }
  .task-title strong {
    font-size: 17px;
    line-height: 1.3;
  }
  .task-kicker {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .task-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .task-chip {
    display: inline-flex;
    align-items: center;
    padding: 5px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.06);
    border: 1px solid rgba(24,33,27,0.08);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    max-width: 100%;
    overflow-wrap: anywhere;
  }
  .task-chip.primary {
    background: rgba(47,125,94,0.10);
    color: #19553d;
    border-color: rgba(47,125,94,0.14);
  }
  .task-chip.warn {
    background: rgba(181,106,27,0.10);
    color: var(--warn);
    border-color: rgba(181,106,27,0.16);
  }
  .task-chip.muted {
    background: rgba(24,33,27,0.04);
    color: var(--muted);
  }
  .task-body {
    display: grid;
    gap: 10px;
  }
  .task-preview {
    padding: 10px 12px;
    border-radius: 14px;
    background: rgba(24,33,27,0.04);
    color: var(--ink);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.55;
  }
  .task-preview strong {
    color: var(--muted);
  }
  .task-state {
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.08);
    color: var(--muted);
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .task-state.executing, .task-state.routed { background: rgba(47,125,94,0.14); color: #19553d; }
  .task-state.completed { background: rgba(47,125,94,0.18); color: #19553d; }
  .task-state.failed { background: rgba(143,70,52,0.12); color: var(--danger); }
  .task-state.waiting_approval { background: rgba(181,106,27,0.14); color: var(--warn); }
  .task-state.cancelled { background: rgba(24,33,27,0.08); color: var(--muted); }
  .task-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 14px;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    line-height: 1.5;
  }
  .task-meta strong {
    color: var(--ink);
    font-weight: 600;
  }
  .task-status-note {
    padding: 10px 12px;
    border-radius: 14px;
    background: rgba(181,106,27,0.08);
    color: #7f4a10;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.5;
  }
  .task-group {
    display: grid;
    gap: 10px;
  }
  .task-group > summary {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    cursor: pointer;
  }
  .task-group .task-list {
    margin-top: 12px;
  }
  .archive-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 14px;
  }
  .task-filters {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }
  .task-saved-views {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }
  .task-status-strip {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }
  .status-card {
    padding: 12px 14px;
    border-radius: 16px;
    border: 1px solid rgba(24,33,27,0.08);
    background: rgba(255,255,255,0.80);
    display: grid;
    gap: 4px;
    text-align: left;
    min-width: 0;
  }
  .status-card strong {
    font-size: 24px;
    letter-spacing: -0.04em;
  }
  .status-card span {
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .status-card.active {
    background: rgba(47,125,94,0.10);
    border-color: rgba(47,125,94,0.18);
    color: #19553d;
  }
  .saved-view-btn {
    min-width: 0;
    padding: 8px 12px;
    border-radius: 999px;
    border: 1px solid rgba(24,33,27,0.10);
    background: rgba(255,255,255,0.82);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    letter-spacing: 0.02em;
    white-space: normal;
  }
  .saved-view-btn.active {
    background: rgba(47,125,94,0.10);
    color: #19553d;
    border-color: rgba(47,125,94,0.18);
  }
  .task-filters select {
    width: 100%;
  }
  .task-modal {
    position: fixed;
    inset: 0;
    background: rgba(24,33,27,0.44);
    display: none;
    align-items: center;
    justify-content: center;
    padding: 24px;
    z-index: 50;
  }
  .task-modal.open {
    display: flex;
  }
  .task-modal-card {
    width: min(860px, 100%);
    max-height: min(84vh, 920px);
    overflow: auto;
    background: rgba(255,252,245,0.98);
    border: 1px solid var(--line);
    border-radius: 26px;
    box-shadow: var(--shadow);
    padding: 22px;
    display: grid;
    gap: 16px;
  }
  .task-modal-head {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    align-items: flex-start;
  }
  .task-modal-head h3 {
    margin: 0;
    font-size: 28px;
    letter-spacing: -0.03em;
  }
  .task-modal-close {
    min-width: 0;
    padding-inline: 14px;
  }
  .task-modal-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 14px;
  }
  .binding-list {
    display: grid;
    gap: 12px;
    margin-top: 12px;
  }
  .binding-grid {
    display: grid;
    gap: 14px;
  }
  .binding-row {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    background: rgba(255,255,255,0.6);
    border: 1px solid rgba(24,33,27,0.05);
    border-radius: 16px;
  }
  @media (min-width: 600px) {
    .binding-row {
      flex-direction: row;
      align-items: center;
    }
  }
  .binding-device-info {
    min-width: 140px;
    display: grid;
    gap: 2px;
  }
  .binding-device-info strong { font-size: 14px; }
  .binding-device-info small { font-size: 10px; color: var(--muted); }
  .binding-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
  }
  .binding-field {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .binding-field input {
    padding: 4px 8px;
    font-size: 12px;
    border-radius: 8px;
    border: 1px solid var(--line);
    background: white;
    width: 100%;
    max-width: 120px;
  }
  .binding-field input.chat-id {
    max-width: 180px;
  }
  .binding-field button {
    padding: 4px 8px;
    font-size: 11px;
  }
  .binding-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .binding-actions button {
    padding: 4px 10px;
    font-size: 11px;
  }
  .binding-card {
    background: rgba(255,255,255,0.6);
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 18px;
    display: grid;
    gap: 14px;
    transition: border-color 0.2s ease;
    min-width: 0;
  }
  .binding-card:hover {
    border-color: rgba(47,125,94,0.3);
  }
  .binding-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 4px;
  }
  .binding-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: flex-end;
  }
  .binding-badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 10px;
    border-radius: 999px;
    background: rgba(24,33,27,0.06);
    border: 1px solid rgba(24,33,27,0.08);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    max-width: 100%;
    overflow-wrap: anywhere;
  }
  .binding-badge.ok {
    background: rgba(47,125,94,0.10);
    border-color: rgba(47,125,94,0.16);
    color: #19553d;
  }
  .binding-badge.warn {
    background: rgba(181,106,27,0.10);
    border-color: rgba(181,106,27,0.16);
    color: var(--warn);
  }
  .binding-badge.danger {
    background: rgba(143,70,52,0.10);
    border-color: rgba(143,70,52,0.16);
    color: var(--danger);
  }
  .binding-head-info {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .binding-head-info strong {
    font-size: 15px;
    color: var(--ink);
    overflow-wrap: anywhere;
  }
  .binding-head-info small {
    font-size: 11px;
    color: var(--muted);
    font-family: ui-monospace, monospace;
    overflow-wrap: anywhere;
  }
  .binding-row {
    display: grid;
    grid-template-columns: 100px 1fr;
    gap: 12px;
    align-items: center;
    padding: 8px 0;
  }
  .binding-section {
    display: grid;
    gap: 10px;
    padding: 14px;
    border-radius: 16px;
    background: rgba(255,255,255,0.74);
    border: 1px solid rgba(24,33,27,0.06);
    min-width: 0;
  }
  .binding-section-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 10px;
  }
  .binding-section-title {
    display: grid;
    gap: 4px;
    min-width: 0;
  }
  .binding-section-title strong {
    font-size: 13px;
    letter-spacing: 0.02em;
  }
  .binding-section-title span {
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    line-height: 1.5;
  }
  .binding-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--muted);
    font-weight: 600;
  }
  .binding-input-group {
    display: grid;
    gap: 8px;
    min-width: 0;
  }
  .binding-input-group.mesh-bind {
    grid-template-columns: minmax(0, 1.35fr) minmax(84px, 0.65fr) auto;
  }
  .binding-input-group.telegram-bind {
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .binding-input-group input {
    padding: 10px 12px;
    font-size: 12px;
    border-radius: 12px;
    border: 1px solid var(--line);
    background: white;
    width: 100%;
    min-width: 0;
  }
  .binding-input-group input.chat-id {
    max-width: none;
  }
  .binding-input-group button {
    padding: 10px 14px;
    font-size: 11px;
    flex-shrink: 0;
    align-self: stretch;
  }
  .binding-footer {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 8px;
    margin-top: 2px;
    padding-top: 14px;
    border-top: 1px solid rgba(24,33,27,0.05);
  }
  .binding-footer button {
    width: 100%;
    justify-content: center;
    padding: 10px 14px;
    font-size: 12px;
  }
  .task-modal-field {
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(24,33,27,0.04);
    border: 1px solid rgba(24,33,27,0.07);
    display: grid;
    gap: 4px;
  }
  .task-modal-field strong {
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .task-modal-section {
    display: grid;
    gap: 8px;
  }
  .task-modal-section h4 {
    margin: 0;
    font-size: 15px;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    letter-spacing: 0.02em;
  }
  .task-modal-text,
  .task-modal-code {
    padding: 14px 16px;
    border-radius: 18px;
    background: rgba(24,33,27,0.04);
    border: 1px solid rgba(24,33,27,0.07);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.65;
    color: var(--ink);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .task-modal-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 12px;
  }
  .peer-ask-form {
    display: grid;
    gap: 12px;
  }
  .peer-thread {
    display: grid;
    gap: 10px;
    max-height: 320px;
    overflow: auto;
    padding-right: 4px;
  }
  .peer-thread-item {
    display: grid;
    gap: 6px;
    padding: 12px 14px;
    border-radius: 16px;
    border: 1px solid rgba(24,33,27,0.08);
    background: rgba(24,33,27,0.04);
  }
  .peer-thread-item.local {
    background: rgba(47,125,94,0.10);
    border-color: rgba(47,125,94,0.18);
  }
  .peer-thread-meta {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 12px;
    color: var(--muted);
  }
  .peer-thread-speaker {
    font-weight: 700;
    color: var(--ink);
  }
  .peer-thread-empty {
    padding: 14px 16px;
    border-radius: 16px;
    background: rgba(24,33,27,0.04);
    border: 1px solid rgba(24,33,27,0.07);
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }
  .peer-ask-textarea {
    width: 100%;
    min-height: 120px;
    border-radius: 18px;
    border: 1px solid var(--line);
    background: rgba(255,255,255,0.82);
    padding: 14px 16px;
    font: inherit;
    color: var(--ink);
    resize: vertical;
  }
  .peer-ask-hint {
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }
  .rename-row, .pairing-body { margin-top: 16px; display: grid; gap: 10px; }
  .manual-bind {
    margin-top: 16px;
    padding: 14px;
    border-radius: 18px;
    background: rgba(255,255,255,0.78);
    border: 1px solid var(--line);
    display: grid;
    gap: 10px;
  }
  .manual-bind p {
    margin: 0;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    line-height: 1.6;
  }
  .pairing-meta { display: grid; gap: 8px; color: var(--muted); font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 14px; }
  .qr-shell {
    display: grid;
    gap: 12px;
    justify-items: center;
    padding: 18px;
    background: rgba(255,255,255,0.75);
    border: 1px solid var(--line);
    border-radius: 22px;
    box-sizing: border-box;
  }
  .qr-shell img {
    width: min(100%, 260px);
    height: auto;
    border-radius: 16px;
    border: 1px solid var(--line);
    background: white;
    padding: 10px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  }
  .qr-caption {
    font-size: 13px;
    color: #666;
    text-align: center;
  }
  .discovery-list { display: grid; gap: 12px; }
  .discovery-item {
    padding: 14px 16px;
    border-radius: 18px;
    background: rgba(255,255,255,0.84);
    border: 1px solid var(--line);
    display: grid;
    gap: 8px;
  }
  .empty {
    padding: 28px;
    text-align: center;
    color: var(--muted);
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .toast {
    position: fixed;
    right: 24px;
    bottom: 24px;
    padding: 14px 18px;
    border-radius: 16px;
    background: rgba(24,33,27,0.92);
    color: white;
    opacity: 0;
    transform: translateY(10px);
    transition: opacity 180ms ease, transform 180ms ease;
    pointer-events: none;
    font-family: 'Helvetica Neue', Arial, sans-serif;
  }
  .toast.show { opacity: 1; transform: translateY(0); }
  @media (max-width: 980px) {
    .shell {
      padding: 20px 16px 44px;
    }
    .layout {
      grid-template-columns: 1fr;
      gap: 16px;
    }
    .hero, .peer-card, .pairing-card, .discovery-card, .tasks-card {
      padding: 18px;
      border-radius: 22px;
    }
    .peer-top, .toggle-row, .task-header, .binding-head {
      flex-direction: column;
      align-items: flex-start;
    }
    .task-status-strip {
      grid-template-columns: 1fr 1fr;
    }
    .task-filters,
    .task-form-grid,
    .task-modal-grid,
    .binding-row {
      grid-template-columns: 1fr;
    }
    .binding-input-group,
    .binding-input-group.mesh-bind,
    .binding-input-group.telegram-bind {
      grid-template-columns: 1fr;
    }
    .binding-head,
    .binding-section-head {
      flex-direction: column;
      align-items: flex-start;
    }
    .binding-badges {
      justify-content: flex-start;
    }
  }
</style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Owner Mesh</div>
      <h1>Peer Trust</h1>
      <p class="headline">Pair new peers, inspect discovery, test live routes, and manage trust from one place. The dashboard now covers the full operator loop from pairing through revoke and restore.</p>
      <div class="hero-actions">
        <button class="primary" onclick="refreshAll()">Refresh Dashboard</button>
        <a class="nav-link" href="/your-day">Back to Your Day</a>
        <a class="nav-link" href="/m-peer">M-Peer</a>
        <a class="nav-link" href="/">Back to Chat</a>
      </div>
      <div class="hero-subnav-note"><a href="/m-peer">M-Peer</a> lets your AIDE work with another person's AIDE.</div>
    </section>

    <section class="layout">
      <div class="stack">
        <section class="pairing-card">
          <div class="section-head">
            <h2>Pairing</h2>
            <p>Use QR or paste a signed pairing payload from another full node.</p>
          </div>
          <div class="pairing-meta" id="pairing-meta"></div>
          <div class="qr-shell">
            <img id="pairing-qr" alt="Mesh pairing QR">
            <div class="qr-caption">Scan to pair device</div>
          </div>
          <div class="pairing-body">
            <textarea id="pairing-payload" readonly></textarea>
            <textarea id="pairing-import" placeholder="Paste a signed pairing payload from another device here..."></textarea>
            <div class="pairing-actions">
              <button onclick="copyPairingPayload()">Copy Local Payload</button>
              <button class="primary" onclick="completePairing()">Add Peer From Payload</button>
            </div>
          </div>
        </section>
        <section class="tasks-card">
          <div class="section-head">
            <h2>Transport Binding</h2>
            <p>Manage how devices are reached. Correct IP/Port or Telegram IDs here if they change.</p>
          </div>
          <div id="transport-binding-panel">
            <div class="empty">Loading device bindings...</div>
          </div>
        </section>
        <section class="discovery-card">
          <div class="section-head">
            <h2>Discovered On LAN</h2>
            <p>Discovery is transport-only. Trust still comes from pairing.</p>
          </div>
          <div id="discovery-list" class="discovery-list"></div>
        </section>
        <section class="tasks-card">
          <div class="section-head">
            <h2>Active Peer Thread</h2>
            <p>Shows which peer Ask Device and Telegram will continue talking to right now.</p>
          </div>
          <div id="active-peer-thread-panel" class="task-list">
            <div class="task-item"><div class="task-meta">No active peer thread.</div></div>
          </div>
        </section>
        <details class="tasks-card collapsible-card">
          <summary>
            <div class="section-head">
              <div>
                <h2>Owner Mesh Tasks</h2>
                <p>Recent delegated work across your trusted devices.</p>
              </div>
            </div>
          </summary>
          <div class="collapse-body">
            <div class="task-status-strip" id="task-status-strip"></div>
            <div class="task-saved-views" id="task-saved-views">
              <button class="saved-view-btn active" onclick="applyTaskSavedView('all', event)">All</button>
              <button class="saved-view-btn" onclick="applyTaskSavedView('waiting_approval', event)">Waiting Approval</button>
              <button class="saved-view-btn" onclick="applyTaskSavedView('running', event)">Running Now</button>
              <button class="saved-view-btn" onclick="applyTaskSavedView('completed_today', event)">Completed Today</button>
              <button class="saved-view-btn" onclick="applyTaskSavedView('android_work', event)">Android Work</button>
              <button class="saved-view-btn" onclick="applyTaskSavedView('research_only', event)">Research Only</button>
            </div>
            <div class="task-filters">
              <select id="task-filter-state" onchange="applyTaskFilters()">
                <option value="">All states</option>
              </select>
              <select id="task-filter-workflow" onchange="applyTaskFilters()">
                <option value="">All workflows</option>
              </select>
              <select id="task-filter-device" onchange="applyTaskFilters()">
                <option value="">All devices</option>
              </select>
            </div>
            <div id="task-list" class="task-list"></div>
          </div>
        </details>

        <details class="tasks-card collapsible-card">
          <summary>
            <div class="section-head">
              <div>
                <h2>Task Archive</h2>
                <p>Older delegated work grouped by workflow and executor.</p>
              </div>
            </div>
          </summary>
          <div class="collapse-body">
            <div class="task-saved-views" id="archive-saved-views">
              <button class="saved-view-btn active" onclick="applyArchiveSavedView('all', event)">All</button>
              <button class="saved-view-btn" onclick="applyArchiveSavedView('completed', event)">Completed</button>
              <button class="saved-view-btn" onclick="applyArchiveSavedView('failed_cancelled', event)">Failed / Cancelled</button>
              <button class="saved-view-btn" onclick="applyArchiveSavedView('android_work', event)">Android Work</button>
              <button class="saved-view-btn" onclick="applyArchiveSavedView('research_only', event)">Research Only</button>
            </div>
            <div class="task-filters">
              <select id="archive-filter-state" onchange="applyArchiveFilters()">
                <option value="">All states</option>
              </select>
              <select id="archive-filter-workflow" onchange="applyArchiveFilters()">
                <option value="">All workflows</option>
              </select>
              <select id="archive-filter-device" onchange="applyArchiveFilters()">
                <option value="">All devices</option>
              </select>
            </div>
            <div class="peer-actions" style="margin-bottom: 14px;">
              <button class="action-btn" onclick="backupArchive()">Backup Archive</button>
              <button class="warn" id="delete-archive-btn" onclick="deleteArchivedTasks()" disabled>Delete Archived Tasks</button>
            </div>
            <div id="archive-status" class="task-meta" style="margin-bottom: 12px;"></div>
            <div id="archive-overview" class="archive-overview"></div>
            <div id="archive-list" class="task-list"></div>
          </div>
        </details>
      </div>

      <div class="stack">
        <section class="tasks-card">
          <div class="section-head">
            <h2>Delegate Work</h2>
            <p>Route real owner-mesh tasks to a trusted executor without leaving the dashboard.</p>
          </div>
          <div class="task-form">
            <textarea id="delegate-task-input" placeholder="Summarize unread priority emails from this morning."></textarea>
            <div class="task-form-grid">
              <select id="delegate-workflow-type">
                <option value="generic_task">Generic Task</option>
                <option value="research_brief">Research Brief</option>
                <option value="email_triage">Email Triage</option>
                <option value="follow_up_draft">Follow-up Draft</option>
                <option value="daily_preparation">Daily Preparation</option>
              </select>
              <select id="delegate-target-device">
                <option value="">Best available executor</option>
              </select>
            </div>
            <div class="task-meta">Trusted devices stay visible here. Only full nodes with execution enabled can be selected as executors.</div>
            <input type="text" id="delegate-context-notes" placeholder="Optional context notes for the executor node.">
            <label class="task-checkbox">
              <input type="checkbox" id="delegate-requires-approval">
              <span>Mark as approval-aware work</span>
            </label>
            <div class="peer-actions">
              <button class="primary" onclick="delegateTask()">Run Now</button>
            </div>
          </div>
        </section>

        <section id="peer-grid" class="peer-grid"></section>
        <div id="empty-state" class="pairing-card empty" style="display:none;">No mesh peers are registered yet.</div>
      </div>
    </section>
  </div>
  <div id="toast" class="toast"></div>
  <div id="task-detail-modal" class="task-modal" onclick="dismissTaskDetail(event)">
    <div class="task-modal-card" role="dialog" aria-modal="true" aria-labelledby="task-detail-title">
      <div class="task-modal-head">
        <div class="task-title">
          <div class="task-kicker" id="task-detail-kicker"></div>
          <h3 id="task-detail-title">Task Details</h3>
        </div>
        <button class="task-modal-close" onclick="closeTaskDetail()">Close</button>
      </div>
      <div id="task-detail-grid" class="task-modal-grid"></div>
      <div class="task-modal-section">
        <h4>Context Notes</h4>
        <div id="task-detail-notes" class="task-modal-text">No context notes.</div>
      </div>
      <div class="task-modal-section">
        <h4>Result</h4>
        <div id="task-detail-result" class="task-modal-text">No result recorded yet.</div>
      </div>
      <div class="task-modal-section">
        <h4>Task Payload</h4>
        <div id="task-detail-payload" class="task-modal-code">{}</div>
      </div>
    </div>
  </div>
  <div id="ask-peer-modal" class="task-modal" onclick="dismissAskPeer(event)">
    <div class="task-modal-card" role="dialog" aria-modal="true" aria-labelledby="ask-peer-title">
      <div class="task-modal-head">
        <div class="task-title">
          <div class="task-kicker" id="ask-peer-kicker"></div>
          <h3 id="ask-peer-title">Ask Device</h3>
        </div>
        <button class="task-modal-close" onclick="closeAskPeer()">Close</button>
      </div>
      <div class="peer-ask-form">
        <div class="peer-ask-hint">This sends a real question to the selected peer node. The reply comes from that device's own agent context and memory.</div>
        <textarea id="ask-peer-input" class="peer-ask-textarea" placeholder="What did we last discuss about the inbox workflow?"></textarea>
        <div class="peer-actions">
          <button class="primary" id="ask-peer-submit" onclick="submitAskPeer()">Ask Now</button>
        </div>
      </div>
      <div class="task-modal-section">
        <h4>Conversation</h4>
        <div id="ask-peer-thread" class="peer-thread">
          <div class="peer-thread-empty">No peer conversation history yet.</div>
        </div>
      </div>
      <div class="task-modal-section">
        <h4>Peer Reply</h4>
        <div id="ask-peer-response" class="task-modal-text">No reply yet.</div>
      </div>
    </div>
  </div>
<script>
  const TASK_VIEW_STORAGE_KEY = 'aide.ownerMesh.taskView';
  const ARCHIVE_VIEW_STORAGE_KEY = 'aide.ownerMesh.archiveView';
  let archiveBackupToken = null;
  let currentTasks = [];
  let currentArchive = {count: 0, by_workflow: [], by_device: []};
  let currentTaskSavedView = 'all';
  let currentArchiveSavedView = 'all';
  let currentAskPeerId = '';
  let currentAskPeerName = '';
  let currentActivePeerThread = null;

  function escapeHtml(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function showToast(message, kind='info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.style.background = kind === 'error' ? 'rgba(143,70,52,0.94)' : 'rgba(24,33,27,0.92)';
    toast.classList.add('show');
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2600);
  }

  function safeLoadViewState(storageKey) {
    try {
      const raw = window.localStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : {};
    } catch {
      return {};
    }
  }

  function safeSaveViewState(storageKey, value) {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      // Ignore storage failures; filters still work for the current session.
    }
  }

  function currentFilterState(prefix) {
    return {
      savedView: prefix === 'task' ? currentTaskSavedView : currentArchiveSavedView,
      state: document.getElementById(`${prefix}-filter-state`)?.value || '',
      workflow: document.getElementById(`${prefix}-filter-workflow`)?.value || '',
      device: document.getElementById(`${prefix}-filter-device`)?.value || '',
    };
  }

  function persistFilterState(prefix) {
    const key = prefix === 'task' ? TASK_VIEW_STORAGE_KEY : ARCHIVE_VIEW_STORAGE_KEY;
    safeSaveViewState(key, currentFilterState(prefix));
  }

  function restoreFilterState(prefix) {
    const key = prefix === 'task' ? TASK_VIEW_STORAGE_KEY : ARCHIVE_VIEW_STORAGE_KEY;
    const saved = safeLoadViewState(key);
    if (!saved || typeof saved !== 'object') return;
    if (prefix === 'task' && typeof saved.savedView === 'string') {
      currentTaskSavedView = saved.savedView;
    }
    if (prefix === 'archive' && typeof saved.savedView === 'string') {
      currentArchiveSavedView = saved.savedView;
    }
    const stateSelect = document.getElementById(`${prefix}-filter-state`);
    const workflowSelect = document.getElementById(`${prefix}-filter-workflow`);
    const deviceSelect = document.getElementById(`${prefix}-filter-device`);
    if (stateSelect && typeof saved.state === 'string') stateSelect.value = saved.state;
    if (workflowSelect && typeof saved.workflow === 'string') workflowSelect.value = saved.workflow;
    if (deviceSelect && typeof saved.device === 'string') deviceSelect.value = saved.device;
  }

  async function loadTransportBindings() {
    const response = await fetch('/api/mesh/devices');
    const devices = await response.json();
    const panel = document.getElementById('transport-binding-panel');
    if (!devices || !devices.length) {
      panel.innerHTML = '<div class="empty">No devices registered in the mesh.</div>';
      return;
    }
    let html = '<div class="binding-grid">';
    devices.forEach(dev => {
      const host = dev.mesh_host || '';
      const port = dev.mesh_port || '7432';
      const chat_id = dev.telegram_chat_id || '';
      const trustBtn = dev.is_trusted
        ? `<button class="danger" onclick="toggleTrust('${dev.device_id}', false)">Revoke Trust</button>`
        : `<button class="primary" onclick="toggleTrust('${dev.device_id}', true)">Trust Device</button>`;
      const trustBadgeClass = dev.is_trusted ? 'ok' : 'danger';
      const trustBadgeLabel = dev.is_trusted ? 'Trusted' : 'Revoked';
      const meshBadgeClass = dev.mesh_host ? 'ok' : 'warn';
      const meshBadgeLabel = dev.mesh_host ? `Mesh ${escapeHtml(host)}:${escapeHtml(port)}` : 'Mesh Unbound';
      const telegramBadgeClass = chat_id ? 'ok' : 'warn';
      const telegramBadgeLabel = chat_id ? 'Telegram Bound' : 'Telegram Unbound';
      html += `
        <article class="binding-card">
          <div class="binding-head">
            <div class="binding-head-info">
              <strong>${escapeHtml(dev.device_name)}</strong>
              <small>${escapeHtml(dev.device_id)}</small>
            </div>
            <div class="binding-badges">
              <span class="binding-badge ${trustBadgeClass}">${trustBadgeLabel}</span>
              <span class="binding-badge">${escapeHtml(dev.device_type || 'unknown')}</span>
            </div>
          </div>
          <div class="binding-section">
            <div class="binding-section-head">
              <div class="binding-section-title">
                <strong>Mesh Route</strong>
                <span>Direct node transport for full peers and executors.</span>
              </div>
              <span class="binding-badge ${meshBadgeClass}">${meshBadgeLabel}</span>
            </div>
            <div class="binding-input-group mesh-bind">
              <input type="text" id="bind-host-${dev.device_id}" value="${escapeHtml(host)}" placeholder="192.168.1.24">
              <input type="text" id="bind-port-${dev.device_id}" value="${escapeHtml(port)}" placeholder="7432">
              <button onclick="updateMeshBind('${dev.device_id}')">Save Mesh</button>
            </div>
          </div>
          <div class="binding-section">
            <div class="binding-section-head">
              <div class="binding-section-title">
                <strong>Telegram Bridge</strong>
                <span>Use this for sovereign terminals and approval routing.</span>
              </div>
              <span class="binding-badge ${telegramBadgeClass}">${telegramBadgeLabel}</span>
            </div>
            <div class="binding-input-group telegram-bind">
              <input type="text" id="bind-chat-${dev.device_id}" class="chat-id" value="${escapeHtml(chat_id)}" placeholder="Chat ID">
              <button onclick="updateTelegramBind('${dev.device_id}')">Save Telegram</button>
            </div>
          </div>
          <div class="binding-footer">
            ${trustBtn}
            <button onclick="pingDevice('${dev.device_id}')">Ping Device</button>
          </div>
        </article>
      `;
    });
    html += '</div>';
    panel.innerHTML = html;
  }
  
  async function updateMeshBind(deviceId) {
    const host = document.getElementById(`bind-host-${deviceId}`).value;
    const port = parseInt(document.getElementById(`bind-port-${deviceId}`).value);
    const res = await fetch(`/api/mesh/devices/${encodeURIComponent(deviceId)}/bind-mesh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, port }),
    });
    if (res.ok) showToast('Mesh binding updated.'); else showToast('Failed to update mesh bind.', 'error');
    await loadTransportBindings();
  }
  
  async function updateTelegramBind(deviceId) {
    const chat_id = document.getElementById(`bind-chat-${deviceId}`).value;
    const res = await fetch(`/api/mesh/devices/${encodeURIComponent(deviceId)}/bind-telegram`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id }),
    });
    if (res.ok) showToast('Telegram binding updated.'); else showToast('Failed to update Telegram bind.', 'error');
    await loadTransportBindings();
  }
  
  async function toggleTrust(deviceId, trusted) {
    const res = await fetch(`/api/mesh/devices/${encodeURIComponent(deviceId)}/trust`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trusted }),
    });
    if (res.ok) showToast(trusted ? 'Device trusted.' : 'Device revoked.'); else showToast('Trust update failed.', 'error');
    await loadTransportBindings();
  }
  
  async function pingDevice(deviceId) {
    const res = await fetch(`/api/mesh/devices/${encodeURIComponent(deviceId)}/ping`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_id: deviceId }),
    });
    const data = await res.json();
    showToast(data.status === 'success' ? 'Ping successful!' : 'Ping failed: ' + (data.response || 'Timeout'), data.status === 'success' ? 'info' : 'error');
  }
  
  function peerCard(peer) {
    const trustClass = peer.is_trusted ? 'trusted' : 'revoked';
    const transports = peer.transports?.length ? peer.transports.join(', ') : 'none';
    const hostValue = peer.mesh_endpoint ? peer.mesh_endpoint.split(':')[0] : '';
    const portValue = peer.mesh_endpoint ? peer.mesh_endpoint.split(':').slice(1).join(':') : '7432';
    return `
      <article class="peer-card">
        <div class="peer-top">
          <div>
            <h3>${escapeHtml(peer.device_name || peer.device_id)}</h3>
            <div class="meta">
              <div><strong>ID:</strong> ${escapeHtml(peer.device_id)}</div>
              <div><strong>Type:</strong> ${escapeHtml(peer.device_type || 'unknown')}</div>
              <div><strong>Fingerprint:</strong> ${escapeHtml(peer.public_key_preview || 'not available')}</div>
              <div><strong>Paired:</strong> ${escapeHtml(peer.paired_at || 'not recorded')}</div>
              <div><strong>Last seen:</strong> ${escapeHtml(peer.last_seen_at || 'never')}</div>
              <div><strong>Transport:</strong> ${escapeHtml(transports)}${peer.mesh_endpoint ? ` · ${escapeHtml(peer.mesh_endpoint)}` : ''}</div>
              <div><strong>Trust source:</strong> ${escapeHtml(peer.trust_source || 'not recorded')}</div>
            </div>
          </div>
          <span class="badge ${trustClass}">${peer.is_trusted ? 'Trusted' : 'Revoked'}</span>
        </div>
        <div class="rename-row">
          <input type="text" id="rename-${escapeHtml(peer.device_id)}" value="${escapeHtml(peer.device_name || '')}" ${peer.is_local ? 'disabled' : ''}>
          <div class="peer-actions">
            <button ${peer.is_local ? 'disabled' : ''} onclick="renamePeer('${escapeHtml(peer.device_id)}')">Rename Peer</button>
            ${peer.is_trusted
              ? `<button class="danger" ${peer.is_local ? 'disabled' : ''} onclick="revokePeer('${escapeHtml(peer.device_id)}')">Revoke Trust</button>`
              : `<button ${peer.is_local ? 'disabled' : ''} onclick="restorePeer('${escapeHtml(peer.device_id)}')">Restore Trust</button>`}
            <button class="warn" ${peer.is_local ? 'disabled' : ''} onclick="forgetPeer('${escapeHtml(peer.device_id)}')">Forget Peer</button>
          </div>
          <div class="peer-actions">
            <button ${!peer.is_trusted || !peer.mesh_endpoint ? 'disabled' : ''} onclick="pingPeer('${escapeHtml(peer.device_id)}')">Ping Peer</button>
            <button ${!peer.is_trusted || !peer.mesh_endpoint || peer.is_local ? 'disabled' : ''} onclick='openAskPeer(${JSON.stringify(peer.device_id)}, ${JSON.stringify(peer.device_name || peer.device_id)})'>Ask Device</button>
            <button ${!peer.is_trusted || !peer.capabilities?.can_execute ? 'disabled' : ''} onclick="prefillDelegateTask('${escapeHtml(peer.device_id)}')">Run On This Device</button>
            <button ${!peer.is_trusted || !peer.capabilities?.can_execute ? 'disabled' : ''} onclick="testTaskPeer('${escapeHtml(peer.device_id)}')">Test Task</button>
          </div>
        </div>
        <div class="caps">
          ${toggleRow(peer, 'can_execute', 'Executor capability')}
          ${toggleRow(peer, 'can_receive_brief', 'Daily brief delivery')}
          ${toggleRow(peer, 'can_approve', 'Approval routing')}
        </div>
        <div class="manual-bind">
          <strong>Manual Mesh Bind</strong>
          <p>Use this when pairing succeeded but live transport did not bind automatically. Enter the peer's LAN IP address and mesh port, then test with Ping Peer.</p>
          <div class="rename-row">
            <input type="text" id="mesh-host-${escapeHtml(peer.device_id)}" value="${escapeHtml(hostValue)}" placeholder="192.168.1.24" ${!peer.is_trusted || peer.is_local ? 'disabled' : ''}>
            <input type="text" id="mesh-port-${escapeHtml(peer.device_id)}" value="${escapeHtml(portValue)}" placeholder="7432" ${!peer.is_trusted || peer.is_local ? 'disabled' : ''}>
          </div>
          <div class="peer-actions">
            <button ${!peer.is_trusted || peer.is_local ? 'disabled' : ''} onclick="manualBindPeer('${escapeHtml(peer.device_id)}')">Bind Mesh Route</button>
            <button ${!peer.mesh_endpoint || peer.is_local ? 'disabled' : ''} onclick="clearMeshRoute('${escapeHtml(peer.device_id)}')">Clear Route</button>
          </div>
        </div>
      </article>
    `;
  }

  function toggleRow(peer, key, label) {
    const checked = peer.capabilities?.[key] ? 'checked' : '';
    const disabled = peer.is_local ? 'disabled' : '';
    return `
      <label class="toggle-row">
        <span>${escapeHtml(label)}</span>
        <input type="checkbox" ${checked} ${disabled} onchange="toggleCapability('${escapeHtml(peer.device_id)}', '${key}', this.checked)">
      </label>
    `;
  }

  function discoveryCard(peer) {
    const status = peer.trusted ? 'Trusted + discovered' : (peer.known ? 'Known but not trusted' : 'Discovered only');
    return `
      <div class="discovery-item">
        <div class="peer-top">
          <div>
            <strong>${escapeHtml(peer.device_name || peer.device_id || 'Unknown peer')}</strong>
            <div class="meta">
              <div><strong>Device ID:</strong> ${escapeHtml(peer.device_id || 'unknown')}</div>
              <div><strong>Endpoint:</strong> ${escapeHtml(peer.address || 'unknown')}:${escapeHtml(peer.port || '')}</div>
              <div><strong>Status:</strong> ${escapeHtml(status)}</div>
            </div>
          </div>
          <span class="badge discovery">${peer.trusted ? 'Live' : 'Seen on LAN'}</span>
        </div>
      </div>
    `;
  }

  function renderPeers(payload) {
    const grid = document.getElementById('peer-grid');
    const empty = document.getElementById('empty-state');
    if (Object.prototype.hasOwnProperty.call(payload || {}, 'active_peer_thread')) {
      renderActivePeerThread(payload.active_peer_thread || null);
    }
    if (!payload.available || !(payload.peers || []).length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = payload.peers.map(peerCard).join('');
    renderDelegateTargetOptions(payload.peers || []);
  }

  function renderDelegateTargetOptions(peers) {
    const select = document.getElementById('delegate-target-device');
    const current = select.value;
    const trustedPeers = (peers || []).filter(peer => peer.is_trusted);
    const executable = trustedPeers.filter(peer => peer.capabilities?.can_execute && ['mesh', 'local'].includes(peer.transport_type || ''));
    const unavailable = trustedPeers.filter(peer => !executable.some(candidate => candidate.device_id === peer.device_id));
    const options = ['<option value="">Best available executor</option>'];
    if (executable.length) {
      options.push('<optgroup label="Available executors">');
      executable.forEach(peer => {
        options.push(`<option value="${escapeHtml(peer.device_id)}">${escapeHtml(peer.device_name || peer.device_id)}</option>`);
      });
      options.push('</optgroup>');
    }
    if (unavailable.length) {
      options.push('<optgroup label="Visible but unavailable">');
      unavailable.forEach(peer => {
        const transport = peer.transport_type || 'no transport';
        const reason = peer.capabilities?.can_execute ? transport : 'cannot execute';
        options.push(`<option value="" disabled>${escapeHtml((peer.device_name || peer.device_id) + ' — ' + reason)}</option>`);
      });
      options.push('</optgroup>');
    }
    select.innerHTML = options.join('');
    if ([...select.options].some(option => option.value === current)) {
      select.value = current;
    }
  }

  function renderActivePeerThread(thread) {
    currentActivePeerThread = thread || null;
    const panel = document.getElementById('active-peer-thread-panel');
    const snapshot = thread || {};
    const hasPeer = Boolean(snapshot.device_id && snapshot.device_name);
    if (!hasPeer) {
      panel.innerHTML = '<div class="task-item"><div class="task-meta">No active peer thread. Start one from Ask Device or with <code>/peer &lt;device&gt; &lt;question&gt;</code> on Telegram.</div></div>';
      return;
    }

    let continuation = 'Off';
    if (snapshot.active) {
      const remaining = Number(snapshot.expires_in_seconds || 0);
      const minutes = Math.max(1, Math.ceil(remaining / 60));
      continuation = `On for about ${minutes} more min`;
    } else if (snapshot.status === 'expired') {
      continuation = 'Off · expired';
    } else {
      continuation = `Off · ${snapshot.status || snapshot.mode || 'idle'}`;
    }

    panel.innerHTML = `
      <div class="task-item">
        <div class="task-header">
          <div class="task-title">
            <div class="task-kicker">
              <span class="task-chip primary">${escapeHtml(snapshot.device_name)}</span>
              <span class="task-chip">${escapeHtml(snapshot.device_id)}</span>
              <span class="task-chip ${snapshot.active ? 'primary' : 'muted'}">${escapeHtml(continuation)}</span>
            </div>
            <strong>Peer mode is armed for this device until you close it or the time window expires.</strong>
            <div class="task-meta">
              <span><strong>Last activity:</strong> ${escapeHtml(snapshot.updated_at || 'not recorded')}</span>
              <span><strong>Mode:</strong> ${escapeHtml(snapshot.mode || 'idle')}</span>
            </div>
          </div>
          <div class="task-actions">
            <button class="action-btn" onclick='openAskPeer(${JSON.stringify(snapshot.device_id)}, ${JSON.stringify(snapshot.device_name)})'>Open Thread</button>
            <button class="warn" onclick="clearActivePeerThread()">Close Peer Mode</button>
          </div>
        </div>
      </div>
    `;
  }

  async function clearActivePeerThread() {
    const response = await fetch('/api/mesh/active-peer-thread/clear', {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not clear the active peer thread.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers || [], active_peer_thread: payload.active_peer_thread});
    renderDiscovery(payload.discovered || []);
    showToast('Peer mode cleared. Telegram is back to local chat.');
  }

  function setSelectOptions(selectId, options, emptyLabel) {
    const select = document.getElementById(selectId);
    if (!select) return;
    const current = select.value;
    const uniqueOptions = [...new Set((options || []).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    select.innerHTML = [`<option value="">${escapeHtml(emptyLabel)}</option>`]
      .concat(uniqueOptions.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`))
      .join('');
    if (uniqueOptions.includes(current)) {
      select.value = current;
    }
  }

  function syncTaskFilterOptions(tasks) {
    setSelectOptions('task-filter-state', (tasks || []).map(task => task.state), 'All states');
    setSelectOptions('task-filter-workflow', (tasks || []).map(task => task.workflow_display_name || task.workflow_type), 'All workflows');
    setSelectOptions('task-filter-device', (tasks || []).map(task => task.assigned_device_name || task.assigned_device_id || 'Unassigned'), 'All devices');
    restoreFilterState('task');
  }

  function syncArchiveFilterOptions(archive) {
    const tasks = [
      ...((archive?.by_workflow || []).flatMap(group => group.tasks || [])),
    ];
    setSelectOptions('archive-filter-state', tasks.map(task => task.state), 'All states');
    setSelectOptions('archive-filter-workflow', tasks.map(task => task.workflow_display_name || task.workflow_type), 'All workflows');
    setSelectOptions('archive-filter-device', tasks.map(task => task.assigned_device_name || task.assigned_device_id || 'Unassigned'), 'All devices');
    restoreFilterState('archive');
  }

  function filterTaskList(tasks, prefix) {
    const state = document.getElementById(`${prefix}-filter-state`)?.value || '';
    const workflow = document.getElementById(`${prefix}-filter-workflow`)?.value || '';
    const device = document.getElementById(`${prefix}-filter-device`)?.value || '';
    return (tasks || []).filter(task => {
      const workflowLabel = task.workflow_display_name || task.workflow_type || '';
      const deviceLabel = task.assigned_device_name || task.assigned_device_id || 'Unassigned';
      if (state && task.state !== state) return false;
      if (workflow && workflowLabel !== workflow) return false;
      if (device && deviceLabel !== device) return false;
      return true;
    });
  }

  function isTodayIso(value) {
    if (!value) return false;
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return false;
    const now = new Date();
    return parsed.getFullYear() === now.getFullYear()
      && parsed.getMonth() === now.getMonth()
      && parsed.getDate() === now.getDate();
  }

  function applySavedViewFilter(tasks, viewName) {
    if (!viewName || viewName === 'all') return tasks;
    return (tasks || []).filter(task => {
      const executor = (task.assigned_device_name || task.assigned_device_id || '').toLowerCase();
      const workflow = (task.workflow_type || '').toLowerCase();
      const state = (task.state || '').toLowerCase();
      switch (viewName) {
        case 'waiting_approval':
          return state === 'waiting_approval';
        case 'running':
          return ['created', 'planned', 'queued', 'routed', 'executing'].includes(state);
        case 'completed_today':
          return state === 'completed' && isTodayIso(task.updated_at || task.created_at);
        case 'android_work':
          return executor.includes('aide') || executor.includes('dreamer') || executor.includes('android');
        case 'research_only':
          return workflow === 'research_brief';
        case 'completed':
          return state === 'completed';
        case 'failed_cancelled':
          return state === 'failed' || state === 'cancelled';
        default:
          return true;
      }
    });
  }

  function syncSavedViewButtons(containerId, activeView) {
    const container = document.getElementById(containerId);
    if (!container) return;
    [...container.querySelectorAll('.saved-view-btn')].forEach(button => {
      const isActive = button.getAttribute('data-view') === activeView;
      button.classList.toggle('active', isActive);
    });
  }

  function setSavedViewButtonMetadata() {
    document.querySelectorAll('#task-saved-views .saved-view-btn').forEach((button, index) => {
      const views = ['all', 'waiting_approval', 'running', 'completed_today', 'android_work', 'research_only'];
      button.setAttribute('data-view', views[index]);
    });
    document.querySelectorAll('#archive-saved-views .saved-view-btn').forEach((button, index) => {
      const views = ['all', 'completed', 'failed_cancelled', 'android_work', 'research_only'];
      button.setAttribute('data-view', views[index]);
    });
    syncSavedViewButtons('task-saved-views', currentTaskSavedView);
    syncSavedViewButtons('archive-saved-views', currentArchiveSavedView);
  }

  function renderTaskStatusStrip(tasks) {
    const el = document.getElementById('task-status-strip');
    if (!el) return;
    const activeTasks = tasks || [];
    const archiveTasks = [...((currentArchive.by_workflow || []).flatMap(group => group.tasks || []))];
    const metrics = [
      {
        key: 'waiting_approval',
        label: 'Waiting Approval',
        count: activeTasks.filter(task => task.state === 'waiting_approval').length,
        apply: () => applyTaskSavedView('waiting_approval'),
      },
      {
        key: 'running',
        label: 'Running',
        count: activeTasks.filter(task => ['created', 'planned', 'queued', 'routed', 'executing'].includes(task.state)).length,
        apply: () => applyTaskSavedView('running'),
      },
      {
        key: 'completed_today',
        label: 'Completed Today',
        count: activeTasks.filter(task => task.state === 'completed' && isTodayIso(task.updated_at || task.created_at)).length,
        apply: () => applyTaskSavedView('completed_today'),
      },
      {
        key: 'archived',
        label: 'Archived',
        count: archiveTasks.length,
        apply: () => {
          const archiveCard = document.querySelector('#archive-saved-views');
          const archiveDetails = archiveCard?.closest('details');
          if (archiveDetails && !archiveDetails.open) {
            archiveDetails.open = true;
          }
          applyArchiveSavedView('all');
        },
      },
    ];
    el.innerHTML = metrics.map(metric => `
      <button class="status-card ${metric.count ? 'active' : ''}" onclick="applyStatusMetric('${metric.key}')">
        <strong>${escapeHtml(metric.count)}</strong>
        <span>${escapeHtml(metric.label)}</span>
      </button>
    `).join('');
  }

  function applyStatusMetric(metricKey) {
    switch (metricKey) {
      case 'waiting_approval':
        applyTaskSavedView('waiting_approval');
        break;
      case 'running':
        applyTaskSavedView('running');
        break;
      case 'completed_today':
        applyTaskSavedView('completed_today');
        break;
      case 'archived': {
        const archiveViews = document.querySelector('#archive-saved-views');
        const archiveDetails = archiveViews?.closest('details');
        if (archiveDetails && !archiveDetails.open) archiveDetails.open = true;
        applyArchiveSavedView('all');
        break;
      }
      default:
        break;
    }
  }

  function renderTasks(tasks) {
    currentTasks = tasks || [];
    syncTaskFilterOptions(currentTasks);
    setSavedViewButtonMetadata();
    renderTaskStatusStrip(currentTasks);
    const filteredTasks = applySavedViewFilter(filterTaskList(currentTasks, 'task'), currentTaskSavedView);
    const el = document.getElementById('task-list');
    if (!(filteredTasks || []).length) {
      el.innerHTML = '<div class="task-item"><div class="task-meta">No active delegated work right now.</div></div>';
      return;
    }
    el.innerHTML = filteredTasks.map(task => `
      <div class="task-item">
        <div class="task-header">
          <div class="task-title">
            <div class="task-kicker">
              <span class="task-chip primary">${escapeHtml(task.workflow_display_name || task.workflow_type || 'Generic Task')}</span>
              <span class="task-chip">${escapeHtml(task.assigned_device_name || task.assigned_device_id || 'Unassigned')}</span>
              ${task.requires_approval ? `<span class="task-chip warn">${escapeHtml(formatApprovalStateLabel(task.approval_state || 'pending'))}</span>` : ''}
              ${task.replayed_to_your_day ? '<span class="task-chip muted">Replayed to Your Day</span>' : ''}
            </div>
            <strong>${escapeHtml(task.description)}</strong>
            <div class="task-meta">
              <span><strong>Origin:</strong> ${escapeHtml(task.origin_device_name || task.origin_device_id)}</span>
              <span><strong>Updated:</strong> ${escapeHtml(task.updated_at || task.created_at || 'unknown')}</span>
              ${task.context_notes ? `<span><strong>Notes:</strong> ${escapeHtml(task.context_notes)}</span>` : ''}
            </div>
          </div>
          <div style="display:grid; gap:10px; justify-items:end;">
            <span class="task-state ${escapeHtml(task.state)}">${escapeHtml(formatTaskStateLabel(task.state))}</span>
            <div class="task-actions">
              ${task.can_approve ? `<button class="primary" onclick="approveTask('${escapeHtml(task.task_id)}')">Approve & Run</button>` : ''}
              ${task.can_deny ? `<button class="warn" onclick="denyTask('${escapeHtml(task.task_id)}')">Deny</button>` : ''}
              ${task.can_retry ? `<button class="action-btn" onclick="retryTask('${escapeHtml(task.task_id)}')">Retry</button>` : ''}
              ${task.can_cancel ? `<button class="action-btn" onclick="cancelTask('${escapeHtml(task.task_id)}')">Cancel</button>` : ''}
              ${task.state === 'completed' ? `<button class="action-btn" ${task.replayed_to_your_day ? 'disabled' : ''} onclick="replayTaskToYourDay('${escapeHtml(task.task_id)}')">${task.replayed_to_your_day ? 'Replayed' : 'Replay to Your Day'}</button>` : ''}
              ${task.can_archive ? `<button class="action-btn" onclick="archiveTask('${escapeHtml(task.task_id)}')">Archive</button>` : ''}
              <button class="action-btn" onclick="openTaskDetail('${escapeHtml(task.task_id)}')">View Details</button>
            </div>
          </div>
        </div>
        <div class="task-body">
          ${task.is_blocked ? `<div class="task-status-note">Waiting for owner approval before execution can continue.</div>` : ''}
          ${task.result_preview ? `<div class="task-preview"><strong>Result preview:</strong> ${escapeHtml(task.result_preview)}</div>` : ''}
        </div>
      </div>
    `).join('');
  }

  function applyTaskFilters() {
    currentTaskSavedView = 'all';
    syncSavedViewButtons('task-saved-views', currentTaskSavedView);
    persistFilterState('task');
    renderTasks(currentTasks);
  }

  function applyTaskSavedView(viewName, event) {
    if (event) event.preventDefault();
    currentTaskSavedView = viewName;
    document.getElementById('task-filter-state').value = '';
    document.getElementById('task-filter-workflow').value = '';
    document.getElementById('task-filter-device').value = '';
    syncSavedViewButtons('task-saved-views', currentTaskSavedView);
    persistFilterState('task');
    renderTasks(currentTasks);
  }

  function archiveGroupCard(group, emptyLabel) {
    if (!(group.tasks || []).length) {
      return `<div class="task-item"><div class="task-meta">${escapeHtml(emptyLabel)}</div></div>`;
    }
    return `
      <details class="task-item task-group">
        <summary>
          <strong>${escapeHtml(group.label)}</strong>
          <span class="task-chip muted">${escapeHtml(group.count)} archived</span>
        </summary>
        <div class="task-list">
          ${group.tasks.map(task => `
            <div class="task-item">
              <div class="task-header">
                <div class="task-title">
                  <div class="task-kicker">
                    <span class="task-chip primary">${escapeHtml(task.workflow_display_name || task.workflow_type)}</span>
                    <span class="task-chip">${escapeHtml(task.assigned_device_name || 'Unassigned')}</span>
                    ${task.replayed_to_your_day ? '<span class="task-chip muted">Replayed</span>' : ''}
                  </div>
                  <strong>${escapeHtml(task.description)}</strong>
                </div>
                <span class="task-state ${escapeHtml(task.state)}">${escapeHtml(formatTaskStateLabel(task.state))}</span>
              </div>
              <div class="task-meta">
                <span><strong>Archived:</strong> ${escapeHtml(task.archived_at || 'unknown')}</span>
                <span><strong>Reason:</strong> ${escapeHtml(task.archive_reason || 'manual')}</span>
                <span><strong>Origin:</strong> ${escapeHtml(task.origin_device_name || task.origin_device_id || 'unknown')}</span>
              </div>
              ${task.result_preview ? `<div class="task-preview"><strong>Result preview:</strong> ${escapeHtml(task.result_preview)}</div>` : ''}
              <div class="task-actions">
                <button class="action-btn" onclick="openTaskDetail('${escapeHtml(task.task_id)}')">View Details</button>
              </div>
            </div>
          `).join('')}
        </div>
      </details>
    `;
  }

  function renderArchive(archive) {
    currentArchive = archive || {count: 0, by_workflow: [], by_device: []};
    syncArchiveFilterOptions(currentArchive);
    setSavedViewButtonMetadata();
    const filteredTasks = applySavedViewFilter(filterTaskList(
      [...((currentArchive.by_workflow || []).flatMap(group => group.tasks || []))],
      'archive'
    ), currentArchiveSavedView);
    const el = document.getElementById('archive-list');
    const status = document.getElementById('archive-status');
    const overview = document.getElementById('archive-overview');
    const deleteBtn = document.getElementById('delete-archive-btn');
    const allArchiveTasks = [...((currentArchive.by_workflow || []).flatMap(group => group.tasks || []))];
    const archivedTodayCount = allArchiveTasks.filter(task => isSameCalendarDay(task.archived_at)).length;
    deleteBtn.disabled = !archiveBackupToken;
    status.innerHTML = archiveBackupToken
      ? `Backup prepared at <strong>${escapeHtml(archiveBackupToken)}</strong>. Delete will only remove that exported archive snapshot.`
      : 'Backup is required before deleting archived tasks.';
    const overviewMetrics = [
      ['Archived total', allArchiveTasks.length],
      ['Archived today', archivedTodayCount],
      ['Completed', allArchiveTasks.filter(task => task.state === 'completed').length],
      ['Failed / Cancelled', allArchiveTasks.filter(task => task.state === 'failed' || task.state === 'cancelled').length],
      ['Replayed', allArchiveTasks.filter(task => task.replayed_to_your_day).length],
      ['Latest archive', formatLatestArchiveLabel(allArchiveTasks)],
    ];
    overview.innerHTML = overviewMetrics.map(([label, count]) => `
      <div class="archive-overview-card">
        <strong>${escapeHtml(count)}</strong>
        <span>${escapeHtml(label)}</span>
      </div>
    `).join('');
    if (!(filteredTasks || []).length) {
      el.innerHTML = '<div class="task-item"><div class="task-meta">No archived owner mesh tasks yet.</div></div>';
      if (!archiveBackupToken) {
        deleteBtn.disabled = true;
      }
      return;
    }
    const rebuildGroups = (tasks, keyName, labelName) => {
      const groups = new Map();
      for (const task of tasks) {
        const key = task[keyName] || 'unknown';
        if (!groups.has(key)) {
          groups.set(key, {
            key,
            label: task[labelName] || key,
            count: 0,
            tasks: [],
          });
        }
        const group = groups.get(key);
        group.count += 1;
        group.tasks.push(task);
      }
      return [...groups.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    };
    const workflowGroups = rebuildGroups(filteredTasks, 'workflow_type', 'workflow_display_name')
      .map(group => archiveGroupCard(group, 'No archived workflow groups.')).join('');
    const deviceGroups = rebuildGroups(filteredTasks, 'assigned_device_id', 'assigned_device_name')
      .map(group => archiveGroupCard(group, 'No archived device groups.')).join('');
    el.innerHTML = `
      <details class="task-item task-group" open>
        <summary>
          <strong>By Workflow</strong>
          <span class="task-chip muted">${escapeHtml(filteredTasks.length)} archived task${filteredTasks.length === 1 ? '' : 's'}</span>
        </summary>
        <div class="task-list">${workflowGroups}</div>
      </details>
      <details class="task-item task-group">
        <summary>
          <strong>By Device</strong>
          <span class="task-chip muted">${escapeHtml(filteredTasks.length)} archived task${filteredTasks.length === 1 ? '' : 's'}</span>
        </summary>
        <div class="task-list">${deviceGroups}</div>
      </details>
    `;
  }

  function applyArchiveFilters() {
    currentArchiveSavedView = 'all';
    syncSavedViewButtons('archive-saved-views', currentArchiveSavedView);
    persistFilterState('archive');
    renderArchive(currentArchive);
  }

  function applyArchiveSavedView(viewName, event) {
    if (event) event.preventDefault();
    currentArchiveSavedView = viewName;
    document.getElementById('archive-filter-state').value = '';
    document.getElementById('archive-filter-workflow').value = '';
    document.getElementById('archive-filter-device').value = '';
    syncSavedViewButtons('archive-saved-views', currentArchiveSavedView);
    persistFilterState('archive');
    renderArchive(currentArchive);
  }

  function renderDiscovery(rows) {
    const el = document.getElementById('discovery-list');
    if (!(rows || []).length) {
      el.innerHTML = '<div class="discovery-item"><div class="meta">No peers discovered on the LAN right now.</div></div>';
      return;
    }
    el.innerHTML = rows.map(discoveryCard).join('');
  }

  async function loadPairing() {
    const response = await fetch('/api/mesh/pairing');
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not load pairing payload.', 'error');
      return;
    }
    document.getElementById('pairing-meta').innerHTML = `
      <div><strong>Name:</strong> ${escapeHtml(payload.device_name)}</div>
      <div><strong>Device ID:</strong> ${escapeHtml(payload.device_id)}</div>
      <div><strong>Type:</strong> ${escapeHtml(payload.device_type)}</div>
    `;
    document.getElementById('pairing-payload').value = payload.payload;
    document.getElementById('pairing-qr').src = payload.qr_data_uri;
  }

  async function loadPeers() {
    const response = await fetch('/api/mesh/peers');
    const payload = await response.json();
    renderPeers(payload);
    renderDiscovery(payload.discovered || []);
  }

  async function loadTasks() {
    const response = await fetch('/api/mesh/tasks');
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not load owner mesh tasks.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
  }

  async function loadArchive() {
    const response = await fetch('/api/mesh/tasks/archive');
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not load archived owner mesh tasks.', 'error');
      return;
    }
    renderArchive(payload.archive || {});
  }

  function downloadArchiveExport(filename, exportPayload) {
    const blob = new Blob([JSON.stringify(exportPayload, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function closeTaskDetail() {
    document.getElementById('task-detail-modal').classList.remove('open');
  }

  function dismissTaskDetail(event) {
    if (event.target.id === 'task-detail-modal') {
      closeTaskDetail();
    }
  }

  function renderTaskDetail(task) {
    const kicker = document.getElementById('task-detail-kicker');
    const grid = document.getElementById('task-detail-grid');
    const notes = document.getElementById('task-detail-notes');
    const result = document.getElementById('task-detail-result');
    const payload = document.getElementById('task-detail-payload');
    const title = document.getElementById('task-detail-title');

    title.textContent = task.description || 'Task Details';
    kicker.innerHTML = `
      <span class="task-chip primary">${escapeHtml(task.workflow_display_name || task.workflow_type || 'Generic Task')}</span>
      <span class="task-state ${escapeHtml(task.state)}">${escapeHtml(formatTaskStateLabel(task.state))}</span>
      ${task.requires_approval ? `<span class="task-chip warn">${escapeHtml(formatApprovalStateLabel(task.approval_state || 'pending'))}</span>` : ''}
      ${task.archived_at ? '<span class="task-chip muted">Archived</span>' : ''}
    `;
    grid.innerHTML = [
      ['Task ID', task.task_id],
      ['Origin', task.origin_device_name || task.origin_device_id || 'unknown'],
      ['Executor', task.assigned_device_name || task.assigned_device_id || 'not assigned'],
      ['Approval Device', task.approval_device_name || task.approval_device_id || 'not required'],
      ['Created', task.created_at || 'unknown'],
      ['Updated', task.updated_at || 'unknown'],
      ['Retries', String(task.retries ?? 0)],
      ['Your Day', task.replayed_to_your_day ? `Replayed${task.your_day_item_id ? ` · ${task.your_day_item_id}` : ''}` : 'Not replayed'],
      ['Archived At', task.archived_at || 'Not archived'],
      ['Archive Reason', task.archive_reason || 'n/a'],
    ].map(([label, value]) => `
      <div class="task-modal-field">
        <strong>${escapeHtml(label)}</strong>
        <span>${escapeHtml(value)}</span>
      </div>
    `).join('');
    notes.textContent = task.context_notes || 'No context notes.';
    result.textContent = task.result || task.result_preview || 'No result recorded yet.';
    payload.textContent = JSON.stringify(task.payload || {}, null, 2);
  }

  async function openTaskDetail(taskId) {
    const response = await fetch(`/api/mesh/tasks/detail/${encodeURIComponent(taskId)}`);
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not load task details.', 'error');
      return;
    }
    renderTaskDetail(payload.task || {});
    document.getElementById('task-detail-modal').classList.add('open');
  }

  function openAskPeer(deviceId, deviceName) {
    currentAskPeerId = deviceId;
    currentAskPeerName = deviceName || deviceId;
    document.getElementById('ask-peer-title').textContent = `Ask ${currentAskPeerName}`;
    document.getElementById('ask-peer-kicker').innerHTML = `<span class="task-chip primary">${escapeHtml(currentAskPeerName)}</span><span class="task-chip">Live mesh chat</span>`;
    document.getElementById('ask-peer-input').value = '';
    document.getElementById('ask-peer-response').textContent = 'No reply yet.';
    document.getElementById('ask-peer-submit').disabled = false;
    renderAskPeerConversation([]);
    document.getElementById('ask-peer-modal').classList.add('open');
    document.getElementById('ask-peer-input').focus();
    loadAskPeerConversation(deviceId);
  }

  function closeAskPeer() {
    document.getElementById('ask-peer-modal').classList.remove('open');
  }

  function dismissAskPeer(event) {
    if (event.target.id === 'ask-peer-modal') {
      closeAskPeer();
    }
  }

  function renderAskPeerConversation(rows) {
    const el = document.getElementById('ask-peer-thread');
    const entries = rows || [];
    if (!entries.length) {
      el.innerHTML = '<div class="peer-thread-empty">No peer conversation history yet.</div>';
      return;
    }
    el.innerHTML = entries.map(entry => `
      <div class="peer-thread-item ${escapeHtml(entry.role || 'peer')}">
        <div class="peer-thread-meta">
          <span class="peer-thread-speaker">${escapeHtml(entry.speaker || (entry.role === 'local' ? 'You' : currentAskPeerName || 'Peer'))}</span>
          <span>${escapeHtml(entry.created_at || 'unknown time')}</span>
          <span>${escapeHtml(entry.via_transport || 'mesh')}</span>
        </div>
        <div>${escapeHtml(entry.text || '')}</div>
      </div>
    `).join('');
    el.scrollTop = el.scrollHeight;
  }

  async function loadAskPeerConversation(deviceId) {
    const response = await fetch(`/api/mesh/peers/${encodeURIComponent(deviceId)}/conversation`);
    const payload = await response.json();
    if (!response.ok) {
      renderAskPeerConversation([]);
      return;
    }
    renderAskPeerConversation(payload.conversation || []);
  }

  async function submitAskPeer() {
    if (!currentAskPeerId) {
      showToast('Choose a peer first.', 'error');
      return;
    }
    const prompt = document.getElementById('ask-peer-input').value.trim();
    if (!prompt) {
      showToast('Enter a question first.', 'error');
      return;
    }
    const responseEl = document.getElementById('ask-peer-response');
    const submitBtn = document.getElementById('ask-peer-submit');
    responseEl.textContent = `Waiting for ${currentAskPeerName || 'peer'}...`;
    submitBtn.disabled = true;
    const response = await fetch(`/api/mesh/peers/${encodeURIComponent(currentAskPeerId)}/ask`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prompt}),
    });
    const payload = await response.json();
    submitBtn.disabled = false;
    if (!response.ok) {
      responseEl.textContent = payload.detail || 'Peer did not answer.';
      showToast(payload.detail || 'Peer chat failed.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers, active_peer_thread: payload.active_peer_thread});
    renderDiscovery(payload.discovered || []);
    renderAskPeerConversation(payload.conversation || []);
    responseEl.textContent = payload.response || 'Peer answered without a text reply.';
    showToast('Peer answered.');
  }

  async function refreshAll() {
    await Promise.all([loadPairing(), loadPeers(), loadTasks(), loadArchive(), loadTransportBindings()]);
  }

  async function copyPairingPayload() {
    const value = document.getElementById('pairing-payload').value;
    await navigator.clipboard.writeText(value);
    showToast('Local pairing payload copied.');
  }

  async function completePairing() {
    const payload = document.getElementById('pairing-import').value.trim();
    if (!payload) {
      showToast('Paste a pairing payload first.', 'error');
      return;
    }
    const response = await fetch('/api/mesh/pairing', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({payload}),
    });
    const data = await response.json();
    if (!response.ok) {
      showToast(data.detail || 'Pairing failed.', 'error');
      return;
    }
    document.getElementById('pairing-import').value = '';
    renderPeers({available: true, peers: data.peers});
    renderDiscovery(data.discovered || []);
    showToast('Peer paired successfully.');
  }

  async function revokePeer(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/revoke`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not revoke peer.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    showToast('Peer trust revoked.');
  }

  async function restorePeer(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/restore`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not restore peer.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    showToast('Peer trust restored.');
  }

  async function renamePeer(deviceId) {
    const value = document.getElementById(`rename-${deviceId}`).value.trim();
    const response = await fetch(`/api/mesh/peers/${deviceId}/name`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: value}),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not rename peer.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    showToast('Peer renamed.');
  }

  async function toggleCapability(deviceId, capability, enabled) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/capabilities/${capability}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({enabled}),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not update capability.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    showToast('Peer capability updated.');
  }

  async function pingPeer(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/ping`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Ping failed.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    renderDiscovery(payload.discovered || []);
    showToast('Peer responded to ping.');
  }

  async function manualBindPeer(deviceId) {
    const host = document.getElementById(`mesh-host-${deviceId}`).value.trim();
    const port = Number(document.getElementById(`mesh-port-${deviceId}`).value.trim());
    const response = await fetch(`/api/mesh/peers/${deviceId}/bind-mesh`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({host, port}),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not bind mesh route.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    renderDiscovery(payload.discovered || []);
    showToast('Manual mesh route saved. Try Ping Peer next.');
  }

  async function clearMeshRoute(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/unbind-mesh`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not clear mesh route.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    renderDiscovery(payload.discovered || []);
    showToast('Manual mesh route cleared.');
  }

  async function testTaskPeer(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/test-task`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({task: 'Return a one-line mesh health acknowledgement.'}),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Test task failed.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    renderDiscovery(payload.discovered || []);
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Mesh test task routed successfully.');
  }

  function prefillDelegateTask(deviceId) {
    document.getElementById('delegate-target-device').value = deviceId;
    document.getElementById('delegate-task-input').focus();
  }

  async function delegateTask() {
    const task = document.getElementById('delegate-task-input').value.trim();
    if (!task) {
      showToast('Enter a task first.', 'error');
      return;
    }
    const workflowType = document.getElementById('delegate-workflow-type').value;
    const targetDeviceId = document.getElementById('delegate-target-device').value || null;
    const contextNotes = document.getElementById('delegate-context-notes').value.trim();
    const requiresApproval = document.getElementById('delegate-requires-approval').checked;

    const response = await fetch('/api/mesh/tasks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        task,
        workflow_type: workflowType,
        target_device_id: targetDeviceId,
        requires_approval: requiresApproval,
        context_notes: contextNotes || null,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not delegate task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    renderPeers({available: true, peers: payload.peers || []});
    renderDiscovery(payload.discovered || []);
    document.getElementById('delegate-task-input').value = '';
    document.getElementById('delegate-context-notes').value = '';
    document.getElementById('delegate-requires-approval').checked = false;
    showToast('Owner mesh task delegated.');
  }

  async function replayTaskToYourDay(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/replay-to-your-day`, {
      method: 'POST',
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not replay task into Your Day.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Task replayed into Your Day.');
  }

  async function approveTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/approve`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not approve task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Task approved and routed.');
  }

  async function denyTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/deny`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not deny task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Task denied.');
  }

  async function retryTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/retry`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not retry task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast(payload.result?.status === 'awaiting_approval' ? 'Task moved back to waiting approval.' : 'Task retried.');
  }

  async function cancelTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/cancel`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not cancel task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Task cancelled.');
  }

  async function archiveTask(taskId) {
    const response = await fetch(`/api/mesh/tasks/${encodeURIComponent(taskId)}/archive`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not archive task.', 'error');
      return;
    }
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast('Task archived.');
  }

  async function backupArchive() {
    const response = await fetch('/api/mesh/tasks/archive/backup', {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not back up archive.', 'error');
      return;
    }
    archiveBackupToken = payload.backup_exported_at || null;
    renderArchive(payload.archive || {});
    downloadArchiveExport(payload.filename || 'owner-mesh-archive.json', payload.export || {});
    showToast('Archive backup downloaded.');
  }

  async function deleteArchivedTasks() {
    if (!archiveBackupToken) {
      showToast('Back up the archive before deleting it.', 'error');
      return;
    }
    const response = await fetch('/api/mesh/tasks/archive/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({backup_exported_at: archiveBackupToken}),
    });
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not delete archived tasks.', 'error');
      return;
    }
    archiveBackupToken = null;
    renderTasks(payload.tasks || []);
    renderArchive(payload.archive || {});
    showToast(`Deleted ${payload.deleted_count || 0} archived task${payload.deleted_count === 1 ? '' : 's'}.`);
  }

  async function forgetPeer(deviceId) {
    const response = await fetch(`/api/mesh/peers/${deviceId}/forget`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) {
      showToast(payload.detail || 'Could not forget peer.', 'error');
      return;
    }
    renderPeers({available: true, peers: payload.peers});
    renderDiscovery(payload.discovered || []);
    showToast('Peer forgotten.');
  }

  refreshAll();
</script>
</body>
</html>"""
