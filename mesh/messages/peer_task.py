"""
AIDE Mesh -- Sovereign Peer Task Messages
Typed message helpers for M-Peer task exchange.
"""
from __future__ import annotations

from datetime import datetime


def _ts() -> int:
    return int(datetime.utcnow().timestamp())


def create_peer_task_request(
    *,
    from_peer: str,
    to_peer: str,
    request_id: str,
    task_type: str,
    title: str,
    instruction: str,
    context: dict | None = None,
) -> dict:
    return {
        "message_id": f"peer_req_{_ts()}",
        "type": "PEER_TASK_REQUEST",
        "from": from_peer,
        "to": to_peer,
        "timestamp": _ts(),
        "payload": {
            "request_id": request_id,
            "task_type": task_type,
            "title": title,
            "instruction": instruction,
            "context": context or {},
        },
    }


def create_peer_task_update(
    *,
    from_peer: str,
    to_peer: str,
    request_id: str,
    state: str,
    summary: str = "",
) -> dict:
    return {
        "message_id": f"peer_update_{_ts()}",
        "type": "PEER_TASK_UPDATE",
        "from": from_peer,
        "to": to_peer,
        "timestamp": _ts(),
        "payload": {
            "request_id": request_id,
            "state": state,
            "summary": summary,
        },
    }


def create_peer_task_result(
    *,
    from_peer: str,
    to_peer: str,
    request_id: str,
    result_type: str,
    result_summary: str,
    result_data: dict | None = None,
) -> dict:
    return {
        "message_id": f"peer_result_{_ts()}",
        "type": "PEER_TASK_RESULT",
        "from": from_peer,
        "to": to_peer,
        "timestamp": _ts(),
        "payload": {
            "request_id": request_id,
            "result_type": result_type,
            "result_summary": result_summary,
            "result_data": result_data or {},
        },
    }


def create_peer_task_cancel(
    *,
    from_peer: str,
    to_peer: str,
    request_id: str,
    reason: str = "",
) -> dict:
    return {
        "message_id": f"peer_cancel_{_ts()}",
        "type": "PEER_TASK_CANCEL",
        "from": from_peer,
        "to": to_peer,
        "timestamp": _ts(),
        "payload": {
            "request_id": request_id,
            "reason": reason,
        },
    }
