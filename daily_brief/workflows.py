"""
Workflow helpers for interactive Your Day actions.
"""
from __future__ import annotations

from datetime import datetime
import json
import uuid
import re

from daily_brief.prompts import MASTER_SYSTEM_PROMPT
from daily_brief.schema import DailyBriefItem, ItemType, Priority


EMAIL_ITEM_DRAFT_PROMPT = """Create a single concise email reply draft for the specific email below.

Rules:
- Use only the provided email context.
- Do not invent commitments, dates, or facts.
- Keep the reply natural and ready to send with minimal editing.
- If there is not enough information to reply safely, return a polite acknowledgement and promise to follow up.
- Return only valid JSON object.

Format:
{
  "recipient_name": "Jane Doe",
  "channel": "email",
  "subject": "Re: Quarterly update",
  "body": "Hi Jane, thanks for sending this. I will review it today and get back to you before Friday.",
  "reason": "Jane asked for a review before Friday."
}
"""


def _email_context_from_item(item: dict) -> dict:
    content = item.get("content", {})
    subject = content.get("subject") or item.get("title", "").removeprefix("Email: ").strip()
    sender = (
        content.get("from")
        or content.get("sender")
        or content.get("reply_to")
        or "Unknown sender"
    )
    return {
        "uid": content.get("uid", ""),
        "from": sender,
        "subject": subject,
        "summary": content.get("summary", ""),
        "snippet": content.get("snippet", ""),
        "body": content.get("body", ""),
        "account": content.get("account", ""),
        "received_at": content.get("received_at", ""),
        "reply_to": content.get("reply_to", ""),
    }


def _extract_email_address(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", value, re.IGNORECASE)
    return match.group(1).strip() if match else ""


async def generate_email_reply_draft(item: dict, llm) -> dict:
    email_context = _email_context_from_item(item)
    prompt = (
        f"{MASTER_SYSTEM_PROMPT}\n\n"
        f"{EMAIL_ITEM_DRAFT_PROMPT}\n\n"
        f"Email context:\n{json.dumps(email_context, indent=2)}\n\n"
        "Generate output now (return only valid JSON):"
    )
    result = await llm.chat([{"role": "user", "content": prompt}])
    if isinstance(result, dict):
        content = result.get("content") or result.get("choices", [{}])
        if isinstance(content, list):
            block = content[0]
            text = block.get("text") or block.get("message", {}).get("content", "")
        else:
            text = str(content)
    else:
        text = str(result)

    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    try:
        parsed = json.loads(cleaned.strip())
    except Exception:
        parsed = None

    if not isinstance(parsed, dict):
        subject = email_context.get("subject") or "(no subject)"
        parsed = {
            "recipient_name": email_context.get("from") or "contact",
            "channel": "email",
            "subject": f"Re: {subject}" if not str(subject).lower().startswith("re:") else subject,
            "body": "Hi, thanks for your email. I have seen this and will follow up shortly.",
            "reason": "Prepared from the unread email item in Your Day.",
        }

    parsed.setdefault("channel", "email")
    parsed.setdefault("account", email_context.get("account", ""))
    parsed.setdefault("uid", email_context.get("uid", ""))
    parsed.setdefault("to", _extract_email_address(email_context.get("reply_to") or email_context.get("from", "")))
    parsed.setdefault("source_item_id", item.get("id", ""))
    parsed.setdefault("source_subject", email_context.get("subject", ""))
    parsed.setdefault("source_sender", email_context.get("from", ""))
    return parsed


def build_draft_item_from_email(source_item: dict, draft: dict) -> DailyBriefItem:
    source_content = source_item.get("content", {})
    recipient_name = draft.get("recipient_name") or draft.get("to") or draft.get("source_sender") or "contact"
    reason = draft.get("reason", "Prepared from an unread email that appears actionable today.")
    draft = {
        **draft,
        "uid": draft.get("uid") or source_content.get("uid", ""),
        "account": draft.get("account") or source_content.get("account", ""),
        "source_item_id": draft.get("source_item_id") or source_item.get("id", ""),
        "quoted_context": source_content.get("body") or source_content.get("summary", ""),
        "quoted_subject": source_content.get("subject", ""),
        "quoted_sender": source_content.get("from") or source_content.get("sender", ""),
    }
    return DailyBriefItem(
        id=f"msg_{uuid.uuid4().hex[:8]}",
        type=ItemType.DRAFT_MESSAGE,
        priority=Priority.HIGH if "today" in reason.lower() or "urgent" in reason.lower() else Priority.MEDIUM,
        title=f"Reply to {recipient_name}",
        reason=reason,
        content=draft,
        actions=["send", "snooze", "dismiss"],
        created_at=datetime.now(),
        state="ready",
    )


def build_prepared_item_from_mesh_task(task: dict) -> DailyBriefItem:
    workflow_display_name = task.get("workflow_display_name") or task.get("workflow_type") or "Delegated Work"
    executor_name = task.get("assigned_device_name") or task.get("assigned_device_id") or "owner mesh"
    result_preview = task.get("result_preview") or "Completed delegated work is ready to review."
    result_body = task.get("result") or result_preview
    context_notes = task.get("context_notes")
    requires_approval = bool(task.get("requires_approval"))
    priority = Priority.HIGH if requires_approval else Priority.MEDIUM
    reason = f"Completed by {executor_name} through Owner Mesh."

    content = {
        "source": "owner_mesh",
        "mesh_task_id": task.get("task_id"),
        "workflow_type": task.get("workflow_type", "generic_task"),
        "workflow_display_name": workflow_display_name,
        "result_kind": task.get("result_kind", "mesh_result"),
        "summary": result_preview,
        "body": result_body,
        "executor_device_id": task.get("assigned_device_id"),
        "executor_device_name": executor_name,
        "origin_device_id": task.get("origin_device_id"),
        "origin_device_name": task.get("origin_device_name"),
        "context_notes": context_notes,
        "requires_approval": requires_approval,
        "replayed_at": datetime.now().isoformat(),
    }

    title = f"{workflow_display_name}: {task.get('description', 'Delegated work')}"
    actions = ["view", "archive_source_task", "dismiss"]
    workflow_type = content["workflow_type"]
    if workflow_type == "follow_up_draft":
        actions.insert(1, "open_as_draft")
    else:
        actions.insert(1, "follow_up_task")
    created_at = _coerce_datetime(task.get("updated_at")) or _coerce_datetime(task.get("created_at")) or datetime.now()
    return DailyBriefItem(
        id=f"mesh_result_{task.get('task_id')}",
        type=ItemType.PREPARED_TASK,
        priority=priority,
        title=title,
        reason=reason,
        content=content,
        actions=actions,
        created_at=created_at,
        state="ready",
    )


def build_draft_item_from_mesh_result(source_item: dict) -> DailyBriefItem:
    content = source_item.get("content", {})
    workflow_display_name = content.get("workflow_display_name") or "Follow-up Draft"
    sections = _markdown_section_map(content.get("body") or "")
    subject = sections.get("subject") or source_item.get("title", "").replace(f"{workflow_display_name}: ", "", 1).strip() or "Follow-up"
    draft_body = sections.get("draft body") or content.get("body") or content.get("summary") or ""
    rationale = sections.get("rationale") or source_item.get("reason", "")
    executor = content.get("executor_device_name") or "Owner Mesh"
    recipient = content.get("to") or ""
    actions = ["dismiss"]
    if recipient.strip() and draft_body.strip():
        actions = ["send", "snooze", "dismiss"]
    return DailyBriefItem(
        id=f"msg_{uuid.uuid4().hex[:8]}",
        type=ItemType.DRAFT_MESSAGE,
        priority=Priority.HIGH,
        title=f"Draft from {executor}",
        reason=f"Prepared from delegated work completed by {executor}. Add recipient details if needed, then review and send.",
        content={
            "to": recipient,
            "subject": subject,
            "body": draft_body,
            "account": "",
            "uid": "",
            "source_item_id": source_item.get("id", ""),
            "quoted_context": rationale or content.get("summary") or "",
            "quoted_subject": source_item.get("title", ""),
            "quoted_sender": executor,
            "workflow_display_name": workflow_display_name,
            "mesh_task_id": content.get("mesh_task_id"),
            "generated_for": "owner_mesh_draft",
        },
        actions=actions,
        created_at=datetime.now(),
        state="ready",
    )


def _coerce_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _markdown_section_map(markdown_text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key: str | None = None
    lines: list[str] = []
    for raw_line in (markdown_text or "").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(lines).strip()
            current_key = line[3:].strip().lower()
            lines = []
            continue
        if current_key is not None:
            lines.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(lines).strip()
    return sections
