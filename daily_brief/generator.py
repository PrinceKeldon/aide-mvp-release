"""
AIDE Daily Brief - Generator
Main daily brief generation logic
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Dict, List
import json
import uuid
import re

from daily_brief.context import gather_daily_context, load_user_preferences
from daily_brief.prompts import (
    build_generation_prompt,
    EMAIL_DIGEST_PROMPT,
    EMAIL_REPLY_DRAFTS_PROMPT,
    DRAFT_MESSAGES_PROMPT,
    PREPARED_TASKS_PROMPT,
)
from daily_brief.schema import (
    DailyBrief,
    DailyBriefSummary,
    DailyBriefItem,
    ItemType,
    Priority,
)


class DailyBriefGenerator:
    """
    Generates the daily brief by calling LLM with specialized prompts.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def _llm_text(self, prompt: str) -> str:
        """Call LLM and always return a plain string."""
        result = await self.llm.chat([{"role": "user", "content": prompt}])
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            content = result.get("content") or result.get("choices", [{}])
            if isinstance(content, list):
                block = content[0]
                return block.get("text") or block.get("message", {}).get("content", "")
            return str(content)
        return str(result)

    def _parse_json_array(self, response: str) -> list[dict]:
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, list) else []

    async def generate(self, target_date: date = None) -> DailyBrief:
        """Generate complete daily brief."""
        if target_date is None:
            target_date = date.today()

        print(f"\n🌅 Generating daily brief for {target_date}...")

        context = gather_daily_context(target_date)
        preferences = load_user_preferences()

        calendar_items = self._generate_calendar_items(context)
        approval_items = self._generate_approval_items(context)
        email_digest_items = await self._generate_email_digest_items(context)
        draft_messages = await self._generate_draft_messages(context)
        prepared_tasks = await self._generate_prepared_tasks(context)

        all_items = calendar_items + approval_items + email_digest_items + draft_messages + prepared_tasks
        all_items = self._rank_and_limit(all_items, preferences)
        summary = self._create_summary(
            all_items,
            preferences,
            unread_email_count=len(context.get("unread_emails", [])),
            calendar_event_count=len(context.get("calendar_events", [])),
            calendar_conflict_count=len(context.get("calendar_conflicts", [])),
            approval_request_count=len(context.get("pending_approvals", [])),
        )

        brief = DailyBrief(
            date=target_date.isoformat(),
            summary=summary,
            items=all_items,
            generated_at=datetime.now(),
        )

        print(f"✅ Generated {len(all_items)} items")
        return brief

    def _generate_approval_items(self, context: Dict) -> List[DailyBriefItem]:
        pending = context.get("pending_approvals", [])
        items: List[DailyBriefItem] = []
        for approval in pending[:3]:
            action_id = approval.get("action_id", uuid.uuid4().hex[:8])
            description = approval.get("description", "Owner approval required")
            created_at = approval.get("created_at") or datetime.now().isoformat()
            items.append(
                DailyBriefItem(
                    id=f"approval_{action_id}",
                    type=ItemType.APPROVAL_REQUEST,
                    priority=Priority.HIGH,
                    title=f"Approval: {description}",
                    reason="Pending owner approval before AIDE can proceed.",
                    content={
                        "approval_id": action_id,
                        "action_description": description,
                        "action_type": approval.get("tool_name", ""),
                        "consequence": "This action is paused until an approved owner device confirms it.",
                        "status": approval.get("status", "pending"),
                        "created_at": created_at,
                        "expires_at": approval.get("expires_at", ""),
                    },
                    actions=["approve", "deny"],
                    created_at=datetime.fromisoformat(created_at),
                )
            )
        return items

    def _generate_calendar_items(self, context: Dict) -> List[DailyBriefItem]:
        events = context.get("calendar_events", [])
        conflicts = context.get("calendar_conflicts", [])
        suggestions = context.get("calendar_suggestions", [])

        items: List[DailyBriefItem] = []
        if events:
            items.append(
                DailyBriefItem(
                    id=f"agenda_{uuid.uuid4().hex[:8]}",
                    type=ItemType.CALENDAR_AGENDA,
                    priority=Priority.HIGH if any(not event.get("all_day") for event in events) else Priority.MEDIUM,
                    title="Today's schedule",
                    reason=f"{len(events)} event(s) across {len({event['calendar_label'] for event in events})} calendar(s).",
                    content={
                        "events": [
                            {
                                "title": event["title"],
                                "calendar": event["calendar_label"],
                                "start_at": event["start_at"],
                                "end_at": event["end_at"],
                                "all_day": event["all_day"],
                                "location": event["location"],
                            }
                            for event in events
                        ]
                    },
                    actions=["view", "dismiss"],
                    created_at=datetime.now(),
                )
            )

        for conflict in conflicts[:2]:
            event_a = conflict["event_a"]
            event_b = conflict["event_b"]
            items.append(
                DailyBriefItem(
                    id=f"schedule_{uuid.uuid4().hex[:8]}",
                    type=ItemType.SCHEDULE_SUGGESTION,
                    priority=Priority.HIGH,
                    title="Resolve calendar conflict",
                    reason=conflict["description"],
                    content={
                        "description": conflict["description"],
                        "event_a": {
                            "title": event_a["title"],
                            "calendar": event_a["calendar_label"],
                            "start_at": event_a["start_at"],
                            "end_at": event_a["end_at"],
                        },
                        "event_b": {
                            "title": event_b["title"],
                            "calendar": event_b["calendar_label"],
                            "start_at": event_b["start_at"],
                            "end_at": event_b["end_at"],
                        },
                    },
                    actions=["view", "dismiss"],
                    created_at=datetime.now(),
                )
            )

        if not conflicts:
            for suggestion in suggestions[:1]:
                items.append(
                    DailyBriefItem(
                        id=f"schedule_{uuid.uuid4().hex[:8]}",
                        type=ItemType.SCHEDULE_SUGGESTION,
                        priority=Priority.MEDIUM,
                        title="Suggested focus block",
                        reason=suggestion["description"],
                        content=suggestion,
                        actions=["accept", "dismiss"],
                        created_at=datetime.now(),
                    )
                )

        return items

    async def _generate_email_digest_items(self, context: Dict) -> List[DailyBriefItem]:
        unread_emails = context.get("unread_emails", [])
        if not unread_emails:
            return []

        prompt = build_generation_prompt(EMAIL_DIGEST_PROMPT, {"unread_emails": unread_emails, "date": context.get("date")})

        try:
            response = await self._llm_text(prompt)
            digests = self._parse_json_array(response)
        except Exception as e:
            print(f"⚠️ Failed to generate email digest summaries: {e}")
            digests = []

        if not digests:
            return self._fallback_email_digest_items(unread_emails)

        items = []
        for digest in digests:
            title = digest.get("subject") or "(no subject)"
            sender = digest.get("from") or "Unknown sender"
            summary = digest.get("summary", "").strip()
            why_it_matters = digest.get("why_it_matters", "").strip()
            source_email = self._match_email_record(unread_emails, sender, title, digest.get("account", ""))
            item = DailyBriefItem(
                id=f"email_{uuid.uuid4().hex[:8]}",
                type=ItemType.EMAIL_DIGEST,
                priority=Priority.HIGH if "today" in why_it_matters.lower() or "reply" in why_it_matters.lower() else Priority.MEDIUM,
                title=f"Email: {title}",
                reason=why_it_matters or f"{sender} sent an unread email worth reviewing.",
                content={
                    "from": sender,
                    "subject": title,
                    "account": digest.get("account", ""),
                    "summary": summary,
                    "why_it_matters": why_it_matters,
                    "uid": source_email.get("uid", ""),
                    "snippet": source_email.get("snippet", ""),
                    "body": source_email.get("body", ""),
                    "received_at": source_email.get("received_at", ""),
                    "reply_to": source_email.get("reply_to", ""),
                },
                actions=["open_thread", "show_original", "draft_reply", "snooze", "dismiss"],
                created_at=datetime.now(),
            )
            items.append(item)

        return items

    def _fallback_email_digest_items(self, unread_emails: List[dict]) -> List[DailyBriefItem]:
        items = []
        for email_record in unread_emails[:3]:
            snippet = (email_record.get("snippet") or email_record.get("body") or "").strip()
            summary = snippet[:200] if snippet else "Unread email with no preview available."
            items.append(
                DailyBriefItem(
                    id=f"email_{uuid.uuid4().hex[:8]}",
                    type=ItemType.EMAIL_DIGEST,
                    priority=Priority.MEDIUM,
                    title=f"Email: {email_record.get('subject', '(no subject)')}",
                    reason=f"{email_record.get('from', 'Unknown sender')} sent an unread email in {email_record.get('account', 'your inbox')}.",
                    content={
                        "from": email_record.get("from", ""),
                        "subject": email_record.get("subject", ""),
                        "account": email_record.get("account", ""),
                        "summary": summary,
                        "why_it_matters": "Unread message from the last 48 hours.",
                        "uid": email_record.get("uid", ""),
                        "snippet": email_record.get("snippet", ""),
                        "body": email_record.get("body", ""),
                        "received_at": email_record.get("received_at", ""),
                        "reply_to": email_record.get("reply_to", ""),
                    },
                    actions=["open_thread", "show_original", "draft_reply", "snooze", "dismiss"],
                    created_at=datetime.now(),
                )
            )
        return items

    def _match_email_record(self, unread_emails: List[dict], sender: str, subject: str, account: str) -> dict:
        sender_norm = (sender or "").strip().lower()
        subject_norm = (subject or "").strip().lower()
        account_norm = (account or "").strip().lower()

        for email_record in unread_emails:
            if sender_norm and (email_record.get("from", "").strip().lower() != sender_norm):
                continue
            if subject_norm and (email_record.get("subject", "").strip().lower() != subject_norm):
                continue
            if account_norm and (email_record.get("account", "").strip().lower() != account_norm):
                continue
            return email_record

        for email_record in unread_emails:
            if subject_norm and email_record.get("subject", "").strip().lower() == subject_norm:
                return email_record

        return {}

    async def _generate_draft_messages(self, context: Dict) -> List[DailyBriefItem]:
        unread_emails = context.get("unread_emails", [])
        if unread_emails:
            prompt = build_generation_prompt(
                EMAIL_REPLY_DRAFTS_PROMPT,
                {"unread_emails": unread_emails, "date": context.get("date")},
            )
        else:
            prompt = build_generation_prompt(DRAFT_MESSAGES_PROMPT, context)

        try:
            response = await self._llm_text(prompt)
            drafts = self._parse_json_array(response)
            if unread_emails:
                drafts = self._validate_email_reply_drafts(drafts, unread_emails)

            items = []
            for draft in drafts:
                recipient_name = draft.get("recipient_name") or draft.get("to") or "contact"
                reason = draft.get("reason", "")
                item = DailyBriefItem(
                    id=f"msg_{uuid.uuid4().hex[:8]}",
                    type=ItemType.DRAFT_MESSAGE,
                    priority=Priority.HIGH if "urgent" in reason.lower() or "today" in reason.lower() else Priority.MEDIUM,
                    title=f"Reply to {recipient_name}",
                    reason=reason or "Prepared from an unread email that appears actionable today.",
                    content=draft,
                    actions=["send", "edit", "dismiss"],
                    created_at=datetime.now(),
                )
                items.append(item)

            return items

        except Exception as e:
            print(f"⚠️ Failed to generate draft messages: {e}")
            return []

    def _validate_email_reply_drafts(self, drafts: List[dict], unread_emails: List[dict]) -> List[dict]:
        by_uid = {str(email.get("uid", "")).strip(): email for email in unread_emails if str(email.get("uid", "")).strip()}
        validated: List[dict] = []
        seen = set()
        for draft in drafts:
            uid = str(draft.get("uid", "")).strip()
            if not uid or uid not in by_uid:
                continue
            source = by_uid[uid]
            subject = source.get("subject", "")
            source_subject_lower = subject.strip().lower()
            draft_subject = str(draft.get("subject", "")).strip()
            if draft_subject:
                normalized = draft_subject.lower().removeprefix("re:").strip()
                if source_subject_lower and normalized != source_subject_lower:
                    continue
            reply_target = source.get("reply_to") or source.get("from", "")
            match = re.search(r"([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})", reply_target, re.IGNORECASE)
            recipient_email = match.group(1).strip() if match else ""
            dedupe_key = (uid, draft_subject.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            enriched = dict(draft)
            enriched["account"] = enriched.get("account") or source.get("account", "")
            enriched["to"] = enriched.get("to") or recipient_email
            enriched["source_item_uid"] = uid
            validated.append(enriched)
        return validated

    async def _generate_prepared_tasks(self, context: Dict) -> List[DailyBriefItem]:
        prompt = build_generation_prompt(PREPARED_TASKS_PROMPT, context)

        try:
            response = await self._llm_text(prompt)
            tasks = self._parse_json_array(response)

            items = []
            for task in tasks:
                item = DailyBriefItem(
                    id=f"task_{uuid.uuid4().hex[:8]}",
                    type=ItemType.PREPARED_TASK,
                    priority=Priority.MEDIUM,
                    title=task.get("title", ""),
                    reason="Prepared to reduce effort later",
                    content=task,
                    actions=["view", "dismiss"],
                    created_at=datetime.now(),
                )
                items.append(item)

            return items

        except Exception as e:
            print(f"⚠️ Failed to generate prepared tasks: {e}")
            return []

    def _rank_and_limit(self, items: List[DailyBriefItem], preferences: Dict) -> List[DailyBriefItem]:
        """Rank and limit items (max 6 total)."""
        max_counts = {
            ItemType.CALENDAR_AGENDA: 1,
            ItemType.APPROVAL_REQUEST: 3,
            ItemType.EMAIL_DIGEST: 3,
            ItemType.DRAFT_MESSAGE: 3,
            ItemType.SCHEDULE_SUGGESTION: 2,
            ItemType.PREPARED_TASK: 2,
        }

        by_type = {}
        for item in items:
            by_type.setdefault(item.type, []).append(item)

        priority_order = {
            Priority.HIGH: 0,
            Priority.MEDIUM: 1,
            Priority.LOW: 2,
        }
        for item_type in by_type:
            by_type[item_type].sort(key=lambda x: (priority_order.get(x.priority, 1), x.created_at))

        limited = []
        for item_type, max_count in max_counts.items():
            if item_type in by_type:
                limited.extend(by_type[item_type][:max_count])

        type_order = {
            ItemType.CALENDAR_AGENDA: 0,
            ItemType.SCHEDULE_SUGGESTION: 1,
            ItemType.APPROVAL_REQUEST: 2,
            ItemType.EMAIL_DIGEST: 3,
            ItemType.DRAFT_MESSAGE: 4,
            ItemType.PREPARED_TASK: 5,
        }
        limited.sort(key=lambda x: (type_order.get(x.type, 99), priority_order.get(x.priority, 1)))
        max_items = preferences.get("max_daily_outputs", 6)
        return limited[:max_items]

    def _create_summary(
        self,
        items: List[DailyBriefItem],
        preferences: Dict,
        unread_email_count: int = 0,
        calendar_event_count: int = 0,
        calendar_conflict_count: int = 0,
        approval_request_count: int = 0,
    ) -> DailyBriefSummary:
        counts = {
            "calendar_events": calendar_event_count,
            "calendar_conflicts": calendar_conflict_count,
            "unread_emails": unread_email_count,
            "calendar_agenda": sum(1 for i in items if i.type == ItemType.CALENDAR_AGENDA),
            "email_digest": sum(1 for i in items if i.type == ItemType.EMAIL_DIGEST),
            "draft_messages": sum(1 for i in items if i.type == ItemType.DRAFT_MESSAGE),
            "schedule_suggestions": sum(1 for i in items if i.type == ItemType.SCHEDULE_SUGGESTION),
            "prepared_tasks": sum(1 for i in items if i.type == ItemType.PREPARED_TASK),
            "approval_requests": approval_request_count,
        }

        user_name = preferences.get("user_name")
        if user_name:
            headline = f"Good morning, {user_name}. I've prepared your day."
        else:
            headline = "Good morning. I've prepared your day."

        return DailyBriefSummary(headline=headline, counts=counts)
