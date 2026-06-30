"""
Authorize local access to Google Calendar and list available calendars.

Usage:
    ./venv/bin/python google_calendar_auth.py
"""
from daily_brief.google_calendar import (
    authorize_google_calendar,
    list_google_calendars,
)


def main() -> None:
    token_path = authorize_google_calendar()
    print(f"Saved Google Calendar token to: {token_path}")
    print("\nAvailable calendars:\n")
    for calendar in list_google_calendars():
        label = calendar["summary"] or "(untitled)"
        primary = " [primary]" if calendar.get("primary") else ""
        print(f"- {label}{primary}")
        print(f"  id: {calendar['id']}")
        if calendar.get("time_zone"):
            print(f"  timezone: {calendar['time_zone']}")
        if calendar.get("access_role"):
            print(f"  access: {calendar['access_role']}")
        print()


if __name__ == "__main__":
    main()
