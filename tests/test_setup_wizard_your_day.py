from pathlib import Path

from core import setup_wizard


def test_setup_writer_preserves_existing_keys_and_adds_your_day_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text("EXISTING_KEY=keep\nGROQ_API_KEY=old\n", encoding="utf-8")
    example_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(setup_wizard, "ENV_PATH", env_path)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE_PATH", example_path)

    result = setup_wizard.write_setup_values(
        {
            "GROQ_API_KEY": "gsk_new",
            "EMAIL_ACCOUNT_PRIMARY_ADDRESS": "owner@example.com",
            "EMAIL_ACCOUNT_PRIMARY_PASSWORD": "app-password",
            "EMAIL_ACCOUNT_PRIMARY_IMAP_HOST": "imap.example.com",
            "EMAIL_ACCOUNT_PRIMARY_SMTP_HOST": "smtp.example.com",
            "CALENDAR_ACCOUNT_PERSONAL_ICS_PATH": str(tmp_path / "calendar.ics"),
            "CALENDAR_ACCOUNT_PERSONAL_LABEL": "Personal",
            "MORNING_BRIEF_HOUR": "8",
            "UNMANAGED_SECRET": "drop-me",
        }
    )

    written = env_path.read_text(encoding="utf-8")
    assert "EXISTING_KEY=keep" in written
    assert "GROQ_API_KEY=gsk_new" in written
    assert "EMAIL_ACCOUNT_PRIMARY_ADDRESS=owner@example.com" in written
    assert "CALENDAR_ACCOUNT_PERSONAL_LABEL=Personal" in written
    assert "MORNING_BRIEF_HOUR=8" in written
    assert "UNMANAGED_SECRET" not in written
    assert "UNMANAGED_SECRET" in result["skipped_unknown_or_empty"]
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_your_day_status_masks_email_and_reports_calendar(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    calendar_path = tmp_path / "calendar.ics"
    calendar_path.write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
    env_path.write_text(
        "\n".join(
            [
                "EMAIL_ACCOUNT_PRIMARY_ADDRESS=owner@example.com",
                "EMAIL_ACCOUNT_PRIMARY_PASSWORD=app-password",
                "EMAIL_ACCOUNT_PRIMARY_IMAP_HOST=imap.example.com",
                "EMAIL_ACCOUNT_PRIMARY_SMTP_HOST=smtp.example.com",
                f"CALENDAR_ACCOUNT_PERSONAL_ICS_PATH={calendar_path}",
                "CALENDAR_ACCOUNT_PERSONAL_LABEL=Personal",
                "MORNING_BRIEF_HOUR=6",
                "",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(setup_wizard, "ENV_PATH", env_path)
    monkeypatch.setattr(setup_wizard, "ENV_EXAMPLE_PATH", tmp_path / ".env.example")

    emails = setup_wizard._email_account_status()
    calendars = setup_wizard._calendar_source_status()

    assert emails == [
        {
            "name": "primary",
            "address_masked": "owne•••••••••.com",
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
        }
    ]
    assert calendars[0]["name"] == "personal"
    assert calendars[0]["provider"] == "ics"
    assert calendars[0]["label"] == "Personal"
