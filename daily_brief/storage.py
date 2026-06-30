"""
AIDE Daily Brief - Storage
Persist daily briefs to disk
"""
from pathlib import Path
import json
import re
from html import unescape
from datetime import date

from core.settings import settings
from daily_brief.schema import DailyBrief


INACTIVE_ITEM_STATES = {"dismissed", "accepted", "completed", "sent", "denied"}
INACTIVE_ITEM_STATES = INACTIVE_ITEM_STATES | {"snoozed"}


def _daily_briefs_dir() -> Path:
    path = settings.daily_brief_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _legacy_daily_briefs_dir() -> Path:
    return Path.home() / ".aide" / "daily_briefs"


def save_daily_brief(brief: DailyBrief):
    """Save daily brief to disk"""

    briefs_dir = _daily_briefs_dir()
    filepath = briefs_dir / f"{brief.date}.json"

    with open(filepath, 'w') as f:
        json.dump(brief.to_dict(), f, indent=2)

    print(f"✅ Daily brief saved: {filepath}")


def load_daily_brief(target_date: date = None) -> dict:
    """Load daily brief from disk"""

    if target_date is None:
        target_date = date.today()

    filename = f"{target_date.isoformat()}.json"
    filepath = _daily_briefs_dir() / filename
    if not filepath.exists():
        legacy_path = _legacy_daily_briefs_dir() / filename
        filepath = legacy_path if legacy_path.exists() else filepath

    if not filepath.exists():
        return None

    with open(filepath, 'r') as f:
        brief = json.load(f)

    if _sanitize_email_digest_items(brief):
        _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))

    return brief


def ensure_daily_brief(target_date: date | None = None) -> dict:
    if target_date is None:
        target_date = date.today()

    brief = load_daily_brief(target_date)
    if brief:
        return brief

    payload = {
        "date": target_date.isoformat(),
        "summary": {
            "headline": "Your Day is ready to collect approvals, inbox work, and delegated results.",
            "counts": {
                "approval_requests": 0,
                "unread_emails": 0,
                "calendar_events": 0,
                "schedule_suggestions": 0,
                "prepared_tasks": 0,
                "draft_messages": 0,
                "email_digest": 0,
            },
        },
        "items": [],
        "generated_at": target_date.isoformat(),
    }
    _refresh_brief_counts(payload)
    _save_brief_payload(payload, target_date=target_date)
    return payload


def _save_brief_payload(payload: dict, target_date: date | None = None) -> Path:
    brief_date = target_date.isoformat() if target_date else payload.get("date")
    filepath = _daily_briefs_dir() / f"{brief_date}.json"
    with open(filepath, "w") as f:
        json.dump(payload, f, indent=2)
    return filepath


def _is_active_item(item: dict) -> bool:
    return (item.get("state") or "new") not in INACTIVE_ITEM_STATES


def _looks_like_html(text: str) -> bool:
    sample = (text or "").strip().lower()
    if not sample:
        return False
    return sample.startswith("<!doctype") or sample.startswith("<html") or bool(re.search(r"<[a-z][^>]*>", sample))


def _html_to_text(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", cleaned)
    cleaned = re.sub(r"(?i)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</p\s*>", "\n\n", cleaned)
    cleaned = re.sub(r"(?i)</div\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?i)</li\s*>", "\n", cleaned)
    cleaned = re.sub(r"(?i)<li\s*>", "- ", cleaned)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace("\r", "").replace("\u00a0", " ")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _summarize_email_text(text: str, max_sentences: int = 4, max_chars: int = 420) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    summary = " ".join(part.strip() for part in parts[:max_sentences] if part.strip()).strip()
    if not summary:
        summary = cleaned
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0].strip() + "…"
    return summary


def _sanitize_email_digest_items(brief: dict) -> bool:
    changed = False
    for item in brief.get("items", []):
        if item.get("type") != "email_digest":
            continue
        content = item.setdefault("content", {})
        body = str(content.get("body", "") or "")
        summary = str(content.get("summary", "") or "")

        cleaned_body = _html_to_text(body) if _looks_like_html(body) else body
        cleaned_summary = _html_to_text(summary) if _looks_like_html(summary) else summary

        if cleaned_body != body:
            content["body"] = cleaned_body
            changed = True
        if cleaned_summary != summary:
            content["summary"] = _summarize_email_text(cleaned_summary)
            changed = True
        if cleaned_body and (not content.get("summary") or _looks_like_html(str(content.get("summary", "")))):
            content["summary"] = _summarize_email_text(cleaned_body)
            changed = True
        elif content.get("summary"):
            compact = _summarize_email_text(str(content.get("summary", "")))
            if compact != content.get("summary", ""):
                content["summary"] = compact
                changed = True
    return changed


def _refresh_brief_counts(brief: dict) -> dict:
    items = brief.setdefault("items", [])
    active_items = [item for item in items if _is_active_item(item)]
    counts = brief.setdefault("summary", {}).setdefault("counts", {})

    counts["calendar_agenda"] = sum(1 for item in active_items if item.get("type") == "calendar_agenda")
    counts["email_digest"] = sum(1 for item in active_items if item.get("type") == "email_digest")
    counts["draft_messages"] = sum(1 for item in active_items if item.get("type") == "draft_message")
    counts["schedule_suggestions"] = sum(1 for item in active_items if item.get("type") == "schedule_suggestion")
    counts["prepared_tasks"] = sum(1 for item in active_items if item.get("type") == "prepared_task")
    counts["approval_requests"] = sum(
        1
        for item in active_items
        if item.get("type") == "approval_request" and item.get("content", {}).get("status") == "pending"
    )
    counts["unread_emails"] = counts.get("unread_emails", 0)
    counts["calendar_events"] = counts.get("calendar_events", 0)
    counts["calendar_conflicts"] = counts.get("calendar_conflicts", 0)

    agenda_events = 0
    for item in active_items:
        if item.get("type") != "calendar_agenda":
            continue
        events = item.get("content", {}).get("events")
        if isinstance(events, list):
            agenda_events += len(events)
    counts["calendar_events"] = agenda_events

    conflict_count = sum(
        1
        for item in active_items
        if item.get("type") == "schedule_suggestion"
        and item.get("content", {}).get("event_a")
        and item.get("content", {}).get("event_b")
    )
    counts["calendar_conflicts"] = conflict_count

    counts["unread_emails"] = sum(1 for item in active_items if item.get("type") == "email_digest")
    return counts


def get_daily_brief_item(item_id: str, target_date: date | None = None) -> tuple[dict | None, dict | None]:
    brief = load_daily_brief(target_date)
    if not brief:
        return None, None

    for item in brief.get("items", []):
        if item.get("id") == item_id:
            return brief, item
    return brief, None


def upsert_daily_brief_item(item: dict, target_date: date | None = None) -> bool:
    brief = load_daily_brief(target_date)
    if not brief:
        return False

    items = brief.setdefault("items", [])
    existing = next((entry for entry in items if entry.get("id") == item.get("id")), None)
    if existing is None:
        items.append(item)
    else:
        existing.update(item)

    _refresh_brief_counts(brief)
    _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))
    return True


def update_daily_brief_item(
    item_id: str,
    *,
    state: str | None = None,
    content_updates: dict | None = None,
    actions: list[str] | None = None,
    title: str | None = None,
    reason: str | None = None,
    target_date: date | None = None,
) -> dict | None:
    brief, item = get_daily_brief_item(item_id, target_date=target_date)
    if not brief or not item:
        return None

    if state is not None:
        item["state"] = state
    if content_updates:
        item.setdefault("content", {}).update(content_updates)
    if actions is not None:
        item["actions"] = actions
    if title is not None:
        item["title"] = title
    if reason is not None:
        item["reason"] = reason

    _refresh_brief_counts(brief)
    _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))
    return item


def prune_invalid_draft_items(target_date: date | None = None) -> bool:
    brief = load_daily_brief(target_date)
    if not brief:
        return False

    items = brief.get("items", [])
    filtered = []
    changed = False
    for item in items:
        if item.get("type") != "draft_message":
            filtered.append(item)
            continue
        content = item.get("content", {})
        if str(content.get("body", "")).strip():
            filtered.append(item)
            continue
        changed = True

    if not changed:
        return False

    brief["items"] = filtered
    _refresh_brief_counts(brief)
    _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))
    return True


def upsert_daily_brief_approval_item(
    *,
    action_id: str,
    description: str,
    tool_name: str,
    created_at: str,
    expires_at: str,
    target_date: date | None = None,
) -> bool:
    brief = load_daily_brief(target_date)
    if not brief:
        return False

    items = brief.setdefault("items", [])
    existing = None
    for item in items:
        if item.get("type") == "approval_request" and item.get("content", {}).get("approval_id") == action_id:
            existing = item
            break

    approval_item = {
        "id": f"approval_{action_id}",
        "type": "approval_request",
        "priority": "high",
        "title": f"Approval: {description}",
        "reason": "Pending owner approval before AIDE can proceed.",
        "content": {
            "approval_id": action_id,
            "action_description": description,
            "action_type": tool_name,
            "consequence": "This action is paused until an approved owner device confirms it.",
            "status": "pending",
            "created_at": created_at,
            "expires_at": expires_at,
        },
        "actions": ["approve", "deny"],
        "created_at": created_at,
    }

    if existing is not None:
        existing.update(approval_item)
    else:
        items.append(approval_item)

    _refresh_brief_counts(brief)
    _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))
    return True


def sync_daily_brief_approval_state(
    action_id: str,
    *,
    status: str,
    resolved_by: str | None = None,
    target_date: date | None = None,
) -> bool:
    brief = load_daily_brief(target_date)
    if not brief:
        return False

    items = brief.get("items", [])
    filtered_items = []
    changed = False
    for item in items:
        content = item.get("content", {})
        if item.get("type") == "approval_request" and content.get("approval_id") == action_id:
            changed = True
            if status == "pending":
                content["status"] = "pending"
                filtered_items.append(item)
            else:
                continue
        else:
            filtered_items.append(item)

    if not changed:
        return False

    brief["items"] = filtered_items
    _refresh_brief_counts(brief)
    _save_brief_payload(brief, target_date=target_date or date.fromisoformat(brief["date"]))
    return True
