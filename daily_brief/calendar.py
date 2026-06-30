"""
Calendar ingestion and schedule analysis for Your Day.

Current implementation supports:
  - local .ics sources
  - Google Calendar API sources

Examples:
  CALENDAR_ACCOUNT_<NAME>_ICS_PATH=/path/to/calendar.ics
  CALENDAR_ACCOUNT_<NAME>_PROVIDER=google
  CALENDAR_ACCOUNT_<NAME>_GOOGLE_CALENDAR_ID=primary
  CALENDAR_ACCOUNT_<NAME>_LABEL=Work Calendar
  CALENDAR_ACCOUNT_<NAME>_ALIASES=work,office
  CALENDAR_ACCOUNT_<NAME>_TIMEZONE=Europe/Berlin
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from loguru import logger

from daily_brief.google_calendar import (
    GoogleCalendarConfigError,
    fetch_google_calendar_events,
)


def _load_dotenv_values(path: str | None = None) -> dict[str, str]:
    if path is None:
        from core.settings import resolve_env_file
        env_path = resolve_env_file()
    else:
        env_path = Path(path)
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _unfold_ics_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line.rstrip())
    return lines


def _parse_property(line: str) -> tuple[str, dict[str, str], str]:
    key_part, value = line.split(":", 1)
    segments = key_part.split(";")
    key = segments[0]
    params = {}
    for segment in segments[1:]:
        if "=" in segment:
            param_key, param_value = segment.split("=", 1)
            params[param_key] = param_value
    return key, params, value


def _parse_ics_datetime(value: str, params: dict[str, str], default_timezone: str) -> tuple[datetime, bool]:
    tz_name = params.get("TZID", default_timezone)
    tzinfo = _resolve_tzinfo(tz_name, default_timezone)

    if re.fullmatch(r"\d{8}", value):
        day = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(day, time.min, tzinfo=tzinfo), True

    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(tzinfo), False

    dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return dt.replace(tzinfo=tzinfo), False


def _resolve_tzinfo(tz_name: str, default_timezone: str):
    try:
        return ZoneInfo(tz_name)
    except Exception:
        pass

    gmt_match = re.fullmatch(r"GMT([+-])(\d{2})(\d{2})", tz_name or "")
    if gmt_match:
        sign, hours, minutes = gmt_match.groups()
        delta = timedelta(hours=int(hours), minutes=int(minutes))
        if sign == "-":
            delta = -delta
        return timezone(delta)

    return ZoneInfo(default_timezone)


def load_calendar_sources() -> list[dict]:
    dotenv_values = _load_dotenv_values()
    grouped: dict[str, dict[str, str]] = {}
    pattern = re.compile(
        r"^CALENDAR_ACCOUNT_([A-Z0-9_]+)_(ICS_PATH|LABEL|ALIASES|TIMEZONE|OWNER_IDENTITY|PROVIDER|GOOGLE_CALENDAR_ID)$"
    )

    for key, value in dotenv_values.items():
        match = pattern.match(key)
        if not match:
            continue
        name, field = match.groups()
        grouped.setdefault(name.lower(), {})[field.lower()] = value

    sources = []
    for name, config in grouped.items():
        provider = (config.get("provider") or ("google" if config.get("google_calendar_id") else "ics")).lower()
        path_value = config.get("ics_path")
        google_calendar_id = config.get("google_calendar_id")
        if provider == "ics" and not path_value:
            continue
        if provider == "google" and not google_calendar_id:
            continue
        source = {
            "name": name,
            "label": config.get("label", name.replace("_", " ").title()),
            "aliases": [a.strip() for a in config.get("aliases", "").split(",") if a.strip()],
            "provider": provider,
            "timezone": config.get("timezone", "UTC"),
            "owner_identity": config.get("owner_identity", ""),
        }
        if path_value:
            source["ics_path"] = Path(path_value).expanduser()
        if google_calendar_id:
            source["google_calendar_id"] = google_calendar_id
        sources.append(source)
    return sources


def load_calendar_events(target_date: date, sources: list[dict] | None = None) -> list[dict]:
    sources = sources or load_calendar_sources()
    events: list[dict] = []

    for source in sources:
        provider = source.get("provider", "ics")
        if provider == "google":
            try:
                events.extend(fetch_google_calendar_events(target_date, source))
            except GoogleCalendarConfigError as e:
                logger.warning(f"Google Calendar source {source.get('label', source.get('name'))} skipped: {e}")
            except Exception as e:
                logger.warning(f"Google Calendar source {source.get('label', source.get('name'))} failed: {e}")
            continue

        path = source.get("ics_path")
        if not path or not path.exists():
            continue
        effective_source = dict(source)
        inferred_label = _load_ics_calendar_name(path)
        if inferred_label and source.get("label") in {None, "", "Apple Calendar"}:
            effective_source["label"] = inferred_label
        events.extend(_load_ics_events(path, effective_source, target_date))

    events.sort(key=lambda item: item["start_at"])
    return events


def _load_ics_events(path: Path, source: dict, target_date: date) -> list[dict]:
    lines = _unfold_ics_lines(path.read_text(encoding="utf-8"))
    events = []
    current: dict[str, tuple[dict[str, str], str]] | None = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                parsed = _build_event(current, source, target_date)
                if parsed is not None:
                    events.append(parsed)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, params, value = _parse_property(line)
        current[key] = (params, value)

    return events


def _load_ics_calendar_name(path: Path) -> str:
    try:
        lines = _unfold_ics_lines(path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    for line in lines[:80]:
        if line.startswith("X-WR-CALNAME:"):
            return line.split(":", 1)[1].strip()
    return ""


def _build_event(current: dict[str, tuple[dict[str, str], str]], source: dict, target_date: date) -> dict | None:
    status = current.get("STATUS", ({}, ""))[1].upper()
    if status == "CANCELLED" or "DTSTART" not in current:
        return None

    start_params, start_value = current["DTSTART"]
    start_dt, all_day = _parse_ics_datetime(start_value, start_params, source["timezone"])

    if "DTEND" in current:
        end_params, end_value = current["DTEND"]
        end_dt, _ = _parse_ics_datetime(end_value, end_params, source["timezone"])
    elif all_day:
        end_dt = start_dt + timedelta(days=1)
    else:
        end_dt = start_dt + timedelta(hours=1)

    event_start = start_dt.astimezone(ZoneInfo(source["timezone"]))
    event_end = end_dt.astimezone(ZoneInfo(source["timezone"]))

    if event_end.date() < target_date or event_start.date() > target_date:
        return None

    return {
        "id": current.get("UID", ({}, f"{source['name']}-{start_value}"))[1],
        "calendar": source["name"],
        "calendar_label": source["label"],
        "owner_identity": source["owner_identity"],
        "title": current.get("SUMMARY", ({}, "(untitled event)"))[1],
        "location": current.get("LOCATION", ({}, ""))[1],
        "description": current.get("DESCRIPTION", ({}, ""))[1],
        "start_at": event_start.isoformat(),
        "end_at": event_end.isoformat(),
        "all_day": all_day,
        "timezone": source["timezone"],
    }


def detect_schedule_conflicts(events: list[dict]) -> list[dict]:
    timed_events = [event for event in events if not event.get("all_day")]
    conflicts: list[dict] = []

    parsed = [
        (
            datetime.fromisoformat(event["start_at"]),
            datetime.fromisoformat(event["end_at"]),
            event,
        )
        for event in timed_events
    ]
    parsed.sort(key=lambda item: item[0])

    for index, (start_at, end_at, event) in enumerate(parsed):
        for next_start, next_end, next_event in parsed[index + 1:]:
            if next_start >= end_at:
                break
            overlap_start = max(start_at, next_start)
            overlap_end = min(end_at, next_end)
            conflicts.append(
                {
                    "event_a": event,
                    "event_b": next_event,
                    "overlap_start": overlap_start.isoformat(),
                    "overlap_end": overlap_end.isoformat(),
                    "description": (
                        f"{event['title']} overlaps with {next_event['title']} "
                        f"between {overlap_start.strftime('%H:%M')} and {overlap_end.strftime('%H:%M')}."
                    ),
                }
            )
    return conflicts


def suggest_focus_blocks(events: list[dict], target_date: date, timezone_name: str = "UTC") -> list[dict]:
    timed_events = [event for event in events if not event.get("all_day")]
    tzinfo = ZoneInfo(timezone_name)
    day_start = datetime.combine(target_date, time(hour=9, minute=0), tzinfo=tzinfo)
    day_end = datetime.combine(target_date, time(hour=17, minute=0), tzinfo=tzinfo)

    ranges = sorted(
        [
            (
                max(datetime.fromisoformat(event["start_at"]).astimezone(tzinfo), day_start),
                min(datetime.fromisoformat(event["end_at"]).astimezone(tzinfo), day_end),
            )
            for event in timed_events
        ],
        key=lambda item: item[0],
    )

    suggestions = []
    cursor = day_start
    for start_at, end_at in ranges:
        if start_at > cursor and (start_at - cursor) >= timedelta(minutes=90):
            suggestions.append(
                {
                    "description": f"Protect {cursor.strftime('%H:%M')}–{start_at.strftime('%H:%M')} as a focus block.",
                    "start_at": cursor.isoformat(),
                    "end_at": start_at.isoformat(),
                    "type": "focus_block",
                }
            )
        if end_at > cursor:
            cursor = end_at

    if day_end > cursor and (day_end - cursor) >= timedelta(minutes=90):
        suggestions.append(
            {
                "description": f"Protect {cursor.strftime('%H:%M')}–{day_end.strftime('%H:%M')} as a focus block.",
                "start_at": cursor.isoformat(),
                "end_at": day_end.isoformat(),
                "type": "focus_block",
            }
        )

    return suggestions[:2]


def load_calendar_context(target_date: date, timezone_name: str = "UTC") -> dict:
    events = load_calendar_events(target_date)
    conflicts = detect_schedule_conflicts(events)
    suggestions = suggest_focus_blocks(events, target_date, timezone_name=timezone_name)
    return {
        "calendar_events": events,
        "calendar_conflicts": conflicts,
        "calendar_suggestions": suggestions,
    }


def get_calendar_source_status(target_date: date, lookahead_days: int = 21) -> list[dict]:
    statuses: list[dict] = []
    for source in load_calendar_sources():
        label = source.get("label", source.get("name", "Calendar"))
        provider = source.get("provider", "ics")
        status = {
            "name": source.get("name", ""),
            "label": label,
            "provider": provider,
            "ok": True,
            "today_event_count": 0,
            "message": "",
            "calendar_name": label,
            "next_event": None,
        }

        if provider == "google":
            try:
                events = fetch_google_calendar_events(target_date, source)
                status["today_event_count"] = len(events)
                status["message"] = (
                    f"{len(events)} event(s) available today."
                    if events else
                    "Connected, but no events are scheduled today."
                )
            except GoogleCalendarConfigError as e:
                status["ok"] = False
                status["message"] = str(e)
            except Exception as e:
                status["ok"] = False
                status["message"] = str(e)
            statuses.append(status)
            continue

        path = source.get("ics_path")
        if not path or not path.exists():
            status["ok"] = False
            status["message"] = "ICS file not found."
            statuses.append(status)
            continue

        inferred_name = _load_ics_calendar_name(path)
        if inferred_name:
            status["calendar_name"] = inferred_name
            status["label"] = inferred_name

        today_events = _load_ics_events(path, source if not inferred_name else {**source, "label": inferred_name}, target_date)
        status["today_event_count"] = len(today_events)
        if today_events:
            status["message"] = f"{len(today_events)} event(s) available today."
            statuses.append(status)
            continue

        next_event = _find_next_ics_event(path, source if not inferred_name else {**source, "label": inferred_name}, target_date, lookahead_days)
        if next_event:
            status["next_event"] = {
                "title": next_event["title"],
                "start_at": next_event["start_at"],
            }
            status["message"] = (
                "No events today. Next event: "
                f"{next_event['title']} at {next_event['start_at']}."
            )
        else:
            status["message"] = f"No events today or in the next {lookahead_days} days."
        statuses.append(status)

    return statuses


def _find_next_ics_event(path: Path, source: dict, target_date: date, lookahead_days: int) -> dict | None:
    for offset in range(1, lookahead_days + 1):
        day = target_date + timedelta(days=offset)
        events = _load_ics_events(path, source, day)
        if events:
            return sorted(events, key=lambda item: item["start_at"])[0]
    return None
