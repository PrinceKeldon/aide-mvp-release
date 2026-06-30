"""
AIDE Mesh -- Daily Brief Message Types
Typed, signed messages for multi-device brief sync.

Mac (Full Node)    → generates brief
Android (Hybrid)   → receives full brief, can take actions
BB10 (Terminal)    → receives approval cards only, no brief content
"""
from datetime import datetime


def create_brief_share(from_device: str, to_device: str, brief_data: dict) -> dict:
    """Mac → Android: share today's full brief."""
    return {
        "message_id": f"brief_{datetime.utcnow().timestamp()}",
        "type":        "DAILY_BRIEF_SHARE",
        "from":        from_device,
        "to":          to_device,
        "timestamp":   int(datetime.utcnow().timestamp()),
        "payload": {
            "brief_date":    brief_data.get("date"),
            "summary":       brief_data.get("summary"),
            "items":         brief_data.get("items", []),
            "generated_at":  brief_data.get("generated_at"),
        },
    }


def create_brief_request(from_device: str, to_device: str, target_date: str) -> dict:
    """Android → Mac: request today's brief."""
    return {
        "message_id": f"req_{datetime.utcnow().timestamp()}",
        "type":        "DAILY_BRIEF_REQUEST",
        "from":        from_device,
        "to":          to_device,
        "timestamp":   int(datetime.utcnow().timestamp()),
        "payload": {
            "requested_date": target_date,
        },
    }


def create_action_taken(
    from_device: str,
    to_device: str,
    item_id: str,
    action: str,
    item_data: dict,
) -> dict:
    """Any device → Mac: user took action on a brief item."""
    return {
        "message_id": f"action_{datetime.utcnow().timestamp()}",
        "type":        "DAILY_BRIEF_ACTION",
        "from":        from_device,
        "to":          to_device,
        "timestamp":   int(datetime.utcnow().timestamp()),
        "payload": {
            "item_id":          item_id,
            "action":           action,
            "item_data":        item_data,
            "action_timestamp": datetime.utcnow().isoformat(),
        },
    }


def create_approval_request(
    from_device: str,
    to_device: str,
    approval_item: dict,
) -> dict:
    """Mac → BB10/Android: request approval for a sensitive action."""
    return {
        "message_id": f"approval_{datetime.utcnow().timestamp()}",
        "type":        "APPROVAL_REQUEST",
        "from":        from_device,
        "to":          to_device,
        "timestamp":   int(datetime.utcnow().timestamp()),
        "payload": {
            "approval_id":          approval_item.get("id"),
            "action_type":          approval_item.get("action_type"),
            "action_description":   approval_item.get("action_description"),
            "reason":               approval_item.get("reason"),
            "consequence":          approval_item.get("consequence", ""),
            "timeout_seconds":      300,
        },
    }


def create_approval_response(
    from_device: str,
    to_device: str,
    approval_id: str,
    decision: str,  # "approved" or "denied"
) -> dict:
    """BB10/Android → Mac: user's approval decision."""
    return {
        "message_id": f"resp_{datetime.utcnow().timestamp()}",
        "type":        "APPROVAL_RESPONSE",
        "from":        from_device,
        "to":          to_device,
        "reply_to":    approval_id,
        "timestamp":   int(datetime.utcnow().timestamp()),
        "payload": {
            "approval_id": approval_id,
            "decision":    decision,
            "decided_at":  datetime.utcnow().isoformat(),
            "device_id":   from_device,
        },
    }
