from datetime import datetime

import pytest

from core.daily_brief_integration import VeraDailyBriefService
from core.scheduler import ProactiveScheduler
from daily_brief.schema import DailyBrief, DailyBriefSummary
from mesh.device_registry import DeviceRegistry


class FakeRouter:
    def __init__(self) -> None:
        self.calls = []

    async def send_with_response(self, to_device_id, message_type, payload, from_device_id):
        self.calls.append(
            {
                "to_device_id": to_device_id,
                "message_type": message_type,
                "payload": payload,
                "from_device_id": from_device_id,
            }
        )
        return {"status": "sent"}


class StubMemory:
    def __init__(self) -> None:
        self.facts = {}

    def store_fact(self, key, value):
        self.facts[key] = value

    def get_fact(self, key):
        return self.facts.get(key)


class StubBriefService:
    def __init__(self) -> None:
        self.called = False

    async def generate_and_deliver(self):
        self.called = True


def _sample_brief() -> DailyBrief:
    return DailyBrief(
        date="2026-04-01",
        summary=DailyBriefSummary(
            headline="Good morning. I've prepared your day.",
            counts={"draft_messages": 0, "schedule_suggestions": 0, "prepared_tasks": 0, "approval_requests": 0},
        ),
        items=[],
        generated_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_your_day_routes_only_to_remote_brief_capable_devices(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    local_device_id = registry.create_device("Mac", device_type="full_node", device_id="local_mac")
    registry.bind_mesh(local_device_id, "127.0.0.1", 7432)

    phone_id = registry.create_device("Owner Phone", device_id="owner_phone")
    registry.bind_telegram(phone_id, 111)

    tablet_id = registry.create_device("Muted Tablet", device_id="muted_tablet")
    registry.bind_telegram(tablet_id, 222)
    registry.update_capabilities(tablet_id, can_receive_brief=0)

    router = FakeRouter()
    service = VeraDailyBriefService(
        llm=object(),
        registry=registry,
        router=router,
        local_device_id=local_device_id,
    )

    result = await service.deliver_brief(_sample_brief())

    assert result["delivered"] == ["Owner Phone"]
    assert result["failed"] == []
    assert result["fallback_used"] is False
    assert [call["to_device_id"] for call in router.calls] == [phone_id]
    assert router.calls[0]["message_type"] == "BRIEF_SHARE"
    assert router.calls[0]["from_device_id"] == local_device_id


@pytest.mark.asyncio
async def test_your_day_falls_back_to_owner_notify_when_no_remote_device(tmp_path):
    registry = DeviceRegistry(tmp_path / "device_registry.db")
    local_device_id = registry.create_device("Mac", device_type="full_node", device_id="local_mac")
    registry.bind_mesh(local_device_id, "127.0.0.1", 7432)

    notifications = []

    async def notify(message):
        notifications.append(message)

    service = VeraDailyBriefService(
        llm=object(),
        registry=registry,
        router=FakeRouter(),
        local_device_id=local_device_id,
        fallback_notify=notify,
    )

    result = await service.deliver_brief(_sample_brief())

    assert result["delivered"] == []
    assert result["fallback_used"] is True
    assert len(notifications) == 1
    assert "prepared your day" in notifications[0].lower()


@pytest.mark.asyncio
async def test_scheduler_uses_your_day_service_for_morning_brief():
    memory = StubMemory()
    service = StubBriefService()
    scheduler = ProactiveScheduler(memory=memory, project_manager=object())

    scheduler.attach(daily_brief_service=service)
    await scheduler._morning_brief()

    assert service.called is True
    assert "last_morning_brief" in memory.facts
