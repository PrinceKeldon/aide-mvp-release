from datetime import datetime, date

import pytest

from core.daily_brief_integration import VeraDailyBriefService
from daily_brief.generator import DailyBriefGenerator
from daily_brief.schema import DailyBrief, DailyBriefSummary, ItemType
from daily_brief.storage import (
    load_daily_brief,
    save_daily_brief,
    sync_daily_brief_approval_state,
    upsert_daily_brief_approval_item,
)
from mesh.device_registry import DeviceRegistry


class EmptyBriefLLM:
    async def chat(self, messages):
        return "[]"


class FakeRouter:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, to_device_id, message_type, payload, from_device_id="mac_primary"):
        self.sent.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        return True

    async def send_with_response(self, to_device_id, message_type, payload, from_device_id="mac_primary"):
        self.sent.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        return {"status": "sent"}


def _sample_brief() -> DailyBrief:
    today = date.today().isoformat()
    return DailyBrief(
        date=today,
        summary=DailyBriefSummary(
            headline="Good morning. I've prepared your day.",
            counts={
                "calendar_events": 0,
                "calendar_conflicts": 0,
                "unread_emails": 0,
                "calendar_agenda": 0,
                "email_digest": 0,
                "draft_messages": 0,
                "schedule_suggestions": 0,
                "prepared_tasks": 0,
                "approval_requests": 0,
            },
        ),
        items=[],
        generated_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_daily_brief_generator_creates_approval_items(monkeypatch):
    monkeypatch.setattr(
        "daily_brief.generator.gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "timezone": "Europe/Berlin",
            "calendar_events": [],
            "calendar_conflicts": [],
            "calendar_suggestions": [],
            "unread_emails": [],
            "pending_approvals": [
                {
                    "action_id": "approve123",
                    "tool_name": "send_email",
                    "description": "Send email to client",
                    "status": "pending",
                    "created_at": "2026-04-02T07:00:00+00:00",
                    "expires_at": "2026-04-02T07:30:00+00:00",
                }
            ],
        },
    )
    monkeypatch.setattr(
        "daily_brief.generator.load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(EmptyBriefLLM()).generate(date(2026, 4, 2))

    assert brief.summary.counts["approval_requests"] == 1
    approval_items = [item for item in brief.items if item.type == ItemType.APPROVAL_REQUEST]
    assert len(approval_items) == 1
    assert approval_items[0].content["approval_id"] == "approve123"


def test_brief_storage_upserts_and_removes_approval_items(tmp_path, monkeypatch):
    from core.settings import settings

    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    brief = _sample_brief()
    target_day = date.fromisoformat(brief.date)
    save_daily_brief(brief)

    created = upsert_daily_brief_approval_item(
        action_id="approve123",
        description="Send email to client",
        tool_name="send_email",
        created_at="2026-04-02T07:00:00+00:00",
        expires_at="2026-04-02T07:30:00+00:00",
        target_date=target_day,
    )
    assert created is True

    brief = load_daily_brief(target_day)
    assert brief["summary"]["counts"]["approval_requests"] == 1
    assert brief["items"][0]["type"] == "approval_request"

    removed = sync_daily_brief_approval_state(
        "approve123",
        status="approved",
        resolved_by="Android Device",
        target_date=target_day,
    )
    assert removed is True

    updated = load_daily_brief(target_day)
    assert updated["summary"]["counts"]["approval_requests"] == 0
    assert updated["items"] == []


@pytest.mark.asyncio
async def test_your_day_service_syncs_approval_state_and_notifies_owner_devices(tmp_path, monkeypatch):
    from core.settings import settings

    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "daily_briefs")
    brief = _sample_brief()
    target_day = date.fromisoformat(brief.date)
    save_daily_brief(brief)

    registry = DeviceRegistry(tmp_path / "device_registry.db")
    local_device_id = registry.create_device("Mac", device_type="full_node", device_id="local_mac")
    registry.bind_mesh(local_device_id, "127.0.0.1", 7432)
    phone_id = registry.create_device("Owner Phone", device_id="owner_phone")
    registry.bind_telegram(phone_id, 111)

    router = FakeRouter()
    service = VeraDailyBriefService(
        llm=object(),
        registry=registry,
        router=router,
        local_device_id=local_device_id,
    )

    await service.sync_approval_status(
        action_id="approve123",
        status="pending",
        description="Send email to client",
        tool_name="send_email",
        created_at="2026-04-02T07:00:00+00:00",
        expires_at="2026-04-02T07:30:00+00:00",
    )
    pending_brief = load_daily_brief(target_day)
    assert pending_brief["summary"]["counts"]["approval_requests"] == 1
    assert router.sent[-1]["message_type"] == "EXECUTION_STATE"
    assert "Approval pending" in router.sent[-1]["payload"]["current_step"]

    await service.sync_approval_status(
        action_id="approve123",
        status="approved",
        description="Send email to client",
        resolved_by="Android Device",
    )
    updated_brief = load_daily_brief(target_day)
    assert updated_brief["summary"]["counts"]["approval_requests"] == 0
    assert router.sent[-1]["message_type"] == "EXECUTION_STATE"
    assert "approved" in router.sent[-1]["payload"]["current_step"].lower()
