"""
Google Calendar integration for Your Day.

This module is optional at runtime. It is only activated when:
- Google calendar sources are configured in .env
- the Google client libraries are installed
- OAuth desktop credentials and a token are available
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.settings import settings


SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarConfigError(RuntimeError):
    pass


def _import_google_client():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError as exc:
        raise GoogleCalendarConfigError(
            "Google Calendar libraries are not installed. "
            "Install google-api-python-client, google-auth-httplib2, and google-auth-oauthlib."
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, HttpError


def _resolve_credentials_path(path: str | Path | None = None) -> Path:
    return Path(path or settings.google_calendar_credentials_path).expanduser()


def _resolve_token_path(path: str | Path | None = None) -> Path:
    return Path(path or settings.google_calendar_token_path).expanduser()


def build_google_calendar_service(
    *,
    credentials_path: str | Path | None = None,
    token_path: str | Path | None = None,
    interactive: bool = False,
):
    Request, Credentials, InstalledAppFlow, build, _ = _import_google_client()

    credentials_file = _resolve_credentials_path(credentials_path)
    token_file = _resolve_token_path(token_path)
    token_file.parent.mkdir(parents=True, exist_ok=True)

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif interactive:
            if not credentials_file.exists():
                raise GoogleCalendarConfigError(
                    f"Google OAuth credentials not found at {credentials_file}"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_file), SCOPES
            )
            creds = flow.run_local_server(port=0)
        else:
            raise GoogleCalendarConfigError(
                "Google Calendar token missing or invalid. "
                f"Run the Google calendar auth helper first and ensure {credentials_file} exists."
            )

        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("calendar", "v3", credentials=creds)


def authorize_google_calendar(
    *,
    credentials_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> Path:
    build_google_calendar_service(
        credentials_path=credentials_path,
        token_path=token_path,
        interactive=True,
    )
    return _resolve_token_path(token_path)


def list_google_calendars(
    *,
    credentials_path: str | Path | None = None,
    token_path: str | Path | None = None,
) -> list[dict]:
    service = build_google_calendar_service(
        credentials_path=credentials_path,
        token_path=token_path,
        interactive=False,
    )
    calendars = service.calendarList().list(showHidden=True).execute().get("items", [])
    return [
        {
            "id": item.get("id", ""),
            "summary": item.get("summary", ""),
            "primary": bool(item.get("primary")),
            "access_role": item.get("accessRole", ""),
            "time_zone": item.get("timeZone", ""),
        }
        for item in calendars
    ]


def fetch_google_calendar_events(target_date: date, source: dict) -> list[dict]:
    service = build_google_calendar_service(interactive=False)

    timezone_name = source.get("timezone") or "UTC"
    tzinfo = ZoneInfo(timezone_name)
    day_start = datetime.combine(target_date, time.min, tzinfo=tzinfo)
    day_end = day_start + timedelta(days=1)

    events_result = (
        service.events()
        .list(
            calendarId=source["google_calendar_id"],
            timeMin=day_start.astimezone(timezone.utc).isoformat(),
            timeMax=day_end.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    items = events_result.get("items", [])

    events: list[dict] = []
    for item in items:
        if item.get("status") == "cancelled":
            continue
        parsed = _normalize_google_event(item, source, target_date, tzinfo)
        if parsed is not None:
            events.append(parsed)
    return events


def _normalize_google_event(
    item: dict, source: dict, target_date: date, tzinfo: ZoneInfo
) -> dict | None:
    timezone_name = source.get("timezone", "UTC")
    start_info = item.get("start", {})
    end_info = item.get("end", {})

    if "dateTime" in start_info:
        start_at = datetime.fromisoformat(
            start_info["dateTime"].replace("Z", "+00:00")
        ).astimezone(tzinfo)
        all_day = False
    elif "date" in start_info:
        start_at = datetime.combine(
            date.fromisoformat(start_info["date"]), time.min, tzinfo=tzinfo
        )
        all_day = True
    else:
        return None

    if "dateTime" in end_info:
        end_at = datetime.fromisoformat(
            end_info["dateTime"].replace("Z", "+00:00")
        ).astimezone(tzinfo)
    elif "date" in end_info:
        end_at = datetime.combine(
            date.fromisoformat(end_info["date"]), time.min, tzinfo=tzinfo
        )
    else:
        end_at = start_at + (timedelta(days=1) if all_day else timedelta(hours=1))

    if end_at.date() < target_date or start_at.date() > target_date:
        return None

    return {
        "id": item.get("id", f"{source['name']}-{start_at.isoformat()}"),
        "calendar": source["name"],
        "calendar_label": source["label"],
        "owner_identity": source.get("owner_identity", ""),
        "title": item.get("summary", "(untitled event)"),
        "location": item.get("location", ""),
        "description": item.get("description", ""),
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "all_day": all_day,
        "timezone": timezone_name,
    }
