from datetime import date

import pytest


class FakeLLM:
    async def chat(self, messages):
        return {"content": "[]"}


@pytest.mark.asyncio
async def test_daily_brief_generator_builds_calendar_and_approval_items(monkeypatch):
    from daily_brief import generator as generator_module
    from daily_brief.generator import DailyBriefGenerator
    from daily_brief.schema import ItemType

    target = date(2026, 5, 7)
    monkeypatch.setattr(
        generator_module,
        "gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "calendar_events": [
                {
                    "title": "MVP release review",
                    "calendar_label": "Work",
                    "start_at": "2026-05-07T09:00:00+02:00",
                    "end_at": "2026-05-07T09:30:00+02:00",
                    "all_day": False,
                    "location": "Desk",
                }
            ],
            "calendar_conflicts": [],
            "calendar_suggestions": [],
            "pending_approvals": [
                {
                    "action_id": "approve-1",
                    "description": "Send draft reply",
                    "tool_name": "send_email",
                    "status": "pending",
                    "created_at": "2026-05-07T06:00:00+00:00",
                }
            ],
            "unread_emails": [],
        },
    )
    monkeypatch.setattr(
        generator_module,
        "load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(FakeLLM()).generate(target)

    assert brief.date == "2026-05-07"
    assert brief.summary.headline == "Good morning, Frank. I've prepared your day."
    assert brief.summary.counts["calendar_events"] == 1
    assert brief.summary.counts["approval_requests"] == 1
    assert [item.type for item in brief.items] == [
        ItemType.CALENDAR_AGENDA,
        ItemType.APPROVAL_REQUEST,
    ]
