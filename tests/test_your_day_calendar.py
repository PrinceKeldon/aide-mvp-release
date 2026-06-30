from datetime import date

import pytest

from daily_brief.calendar import load_calendar_context
from daily_brief.generator import DailyBriefGenerator
from daily_brief.schema import ItemType


WORK_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:work-1
DTSTART;TZID=Europe/Berlin:20260402T090000
DTEND;TZID=Europe/Berlin:20260402T100000
SUMMARY:Planning Sync
LOCATION:Zoom
END:VEVENT
BEGIN:VEVENT
UID:work-2
DTSTART;TZID=Europe/Berlin:20260402T130000
DTEND;TZID=Europe/Berlin:20260402T140000
SUMMARY:Client Review
LOCATION:Office
END:VEVENT
END:VCALENDAR
"""


PERSONAL_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:personal-1
DTSTART;TZID=Europe/Berlin:20260402T093000
DTEND;TZID=Europe/Berlin:20260402T103000
SUMMARY:Dentist Appointment
LOCATION:Clinic
END:VEVENT
END:VCALENDAR
"""

APPLE_GMT_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:apple-gmt-1
DTSTART;TZID=GMT+0100:20260402T093000
DTEND;TZID=GMT+0100:20260402T103000
SUMMARY:Apple TZ Event
LOCATION:Calendar
END:VEVENT
END:VCALENDAR
"""


class EmptyBriefLLM:
    async def chat(self, messages):
        return "[]"


@pytest.fixture
def calendar_env(tmp_path, monkeypatch):
    work_path = tmp_path / "work.ics"
    personal_path = tmp_path / "personal.ics"
    work_path.write_text(WORK_ICS, encoding="utf-8")
    personal_path.write_text(PERSONAL_ICS, encoding="utf-8")

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"CALENDAR_ACCOUNT_WORK_ICS_PATH={work_path}",
                "CALENDAR_ACCOUNT_WORK_LABEL=Work Calendar",
                "CALENDAR_ACCOUNT_WORK_TIMEZONE=Europe/Berlin",
                f"CALENDAR_ACCOUNT_PERSONAL_ICS_PATH={personal_path}",
                "CALENDAR_ACCOUNT_PERSONAL_LABEL=Personal Calendar",
                "CALENDAR_ACCOUNT_PERSONAL_TIMEZONE=Europe/Berlin",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)


def test_load_calendar_context_detects_events_and_conflict(calendar_env):
    context = load_calendar_context(date(2026, 4, 2))

    assert len(context["calendar_events"]) == 3
    assert context["calendar_events"][0]["title"] == "Planning Sync"
    assert len(context["calendar_conflicts"]) == 1
    assert "Dentist Appointment" in context["calendar_conflicts"][0]["description"]


def test_load_calendar_context_supports_apple_gmt_tzid(tmp_path, monkeypatch):
    apple_path = tmp_path / "apple.ics"
    apple_path.write_text(APPLE_GMT_ICS, encoding="utf-8")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"CALENDAR_ACCOUNT_APPLE_ICS_PATH={apple_path}",
                "CALENDAR_ACCOUNT_APPLE_LABEL=Apple Calendar",
                "CALENDAR_ACCOUNT_APPLE_TIMEZONE=Europe/Berlin",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    context = load_calendar_context(date(2026, 4, 2))

    assert len(context["calendar_events"]) == 1
    assert context["calendar_events"][0]["title"] == "Apple TZ Event"


def test_load_calendar_context_uses_ics_calendar_name_when_label_is_generic(tmp_path, monkeypatch):
    apple_path = tmp_path / "apple.ics"
    apple_path.write_text(
        "\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "X-WR-CALNAME:Family",
                "BEGIN:VEVENT",
                "UID:family-1",
                "DTSTART;TZID=Europe/Berlin:20260402T120000",
                "DTEND;TZID=Europe/Berlin:20260402T130000",
                "SUMMARY:Family Lunch",
                "END:VEVENT",
                "END:VCALENDAR",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"CALENDAR_ACCOUNT_APPLE_ICS_PATH={apple_path}",
                "CALENDAR_ACCOUNT_APPLE_LABEL=Apple Calendar",
                "CALENDAR_ACCOUNT_APPLE_TIMEZONE=Europe/Berlin",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    context = load_calendar_context(date(2026, 4, 2))

    assert len(context["calendar_events"]) == 1
    assert context["calendar_events"][0]["calendar_label"] == "Family"


@pytest.fixture
def google_calendar_env(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "CALENDAR_ACCOUNT_WORK_PROVIDER=google",
                "CALENDAR_ACCOUNT_WORK_GOOGLE_CALENDAR_ID=primary",
                "CALENDAR_ACCOUNT_WORK_LABEL=Google Work",
                "CALENDAR_ACCOUNT_WORK_TIMEZONE=Europe/Berlin",
                f"CALENDAR_ACCOUNT_PERSONAL_ICS_PATH={tmp_path / 'personal.ics'}",
                "CALENDAR_ACCOUNT_PERSONAL_LABEL=Personal Calendar",
                "CALENDAR_ACCOUNT_PERSONAL_TIMEZONE=Europe/Berlin",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "personal.ics").write_text(PERSONAL_ICS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def test_load_calendar_context_supports_google_provider(google_calendar_env, monkeypatch):
    def fake_google_events(target_date, source):
        assert source["provider"] == "google"
        assert source["google_calendar_id"] == "primary"
        return [
            {
                "id": "gcal-1",
                "calendar": source["name"],
                "calendar_label": source["label"],
                "owner_identity": source.get("owner_identity", ""),
                "title": "Google Work Sync",
                "location": "Meet",
                "description": "Team sync",
                "start_at": "2026-04-02T09:00:00+02:00",
                "end_at": "2026-04-02T10:00:00+02:00",
                "all_day": False,
                "timezone": "Europe/Berlin",
            }
        ]

    monkeypatch.setattr("daily_brief.calendar.fetch_google_calendar_events", fake_google_events)

    context = load_calendar_context(date(2026, 4, 2))

    assert len(context["calendar_events"]) == 2
    assert len(context["calendar_conflicts"]) == 1
    assert context["calendar_events"][0]["title"] == "Google Work Sync"
    assert "Dentist Appointment" in context["calendar_conflicts"][0]["description"]


@pytest.mark.asyncio
async def test_daily_brief_generator_includes_agenda_and_conflict_items(calendar_env, monkeypatch):
    monkeypatch.setattr(
        "daily_brief.generator.gather_daily_context",
        lambda target_date: {
            "date": target_date.isoformat(),
            "timezone": "Europe/Berlin",
            "calendar_events": load_calendar_context(target_date)["calendar_events"],
            "calendar_conflicts": load_calendar_context(target_date)["calendar_conflicts"],
            "calendar_suggestions": [],
            "unread_emails": [],
        },
    )
    monkeypatch.setattr(
        "daily_brief.generator.load_user_preferences",
        lambda: {"user_name": "Frank", "max_daily_outputs": 6},
    )

    brief = await DailyBriefGenerator(EmptyBriefLLM()).generate(date(2026, 4, 2))

    assert brief.summary.counts["calendar_events"] == 3
    assert brief.summary.counts["calendar_conflicts"] == 1
    assert brief.summary.counts["calendar_agenda"] == 1
    assert brief.summary.counts["schedule_suggestions"] == 1
    assert brief.items[0].type == ItemType.CALENDAR_AGENDA
    assert brief.items[1].type == ItemType.SCHEDULE_SUGGESTION
    assert "Planning Sync" in str(brief.items[0].content["events"])
