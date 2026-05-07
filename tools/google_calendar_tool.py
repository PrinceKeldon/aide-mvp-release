"""
Google Calendar tool for AIDE.
Provides CRUD operations for calendar events.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from loguru import logger
from tools.base import BaseTool
from daily_brief.google_calendar import build_google_calendar_service


class GoogleCalendarTool(BaseTool):
    name = "google_calendar"
    description = "Manage Google Calendar events. Use this to read, create, modify, or delete appointments."

    def __init__(self, default_calendar_id: str = ""):
        self.default_calendar_id = default_calendar_id

    async def execute(self, input_data: dict[str, Any]) -> str:
        """
        Expected input format:
        {
            "action": "list" | "create" | "update" | "delete",
            "calendarId": "optional_id",
            "event_id": "required_for_update_delete",
            "summary": "title",
            "start": "ISO datetime",
            "end": "ISO datetime",
            "description": "text",
            "location": "text",
            "time_min": "ISO datetime for listing",
            "time_max": "ISO datetime for listing"
        }
        """
        try:
            service = build_google_calendar_service(interactive=False)
        except Exception as e:
            return f"Failed to initialize Google Calendar service: {e}"

        action = input_data.get("action")
        calendar_id = input_data.get("calendarId") or self.default_calendar_id
        if not calendar_id:
            return "Google Calendar is not configured. Add a Calendar ID in Settings before using calendar tools."

        if action == "list":
            return self._list_events(service, calendar_id, input_data)
        elif action == "create":
            return self._create_event(service, calendar_id, input_data)
        elif action == "update":
            return self._update_event(service, calendar_id, input_data)
        elif action == "delete":
            return self._delete_event(service, calendar_id, input_data)
        else:
            return f"Invalid action: {action}. Supported actions: list, create, update, delete."

    def _list_events(self, service, calendar_id, data) -> str:
        time_min = data.get("time_min") or datetime.now(timezone.utc).isoformat()
        time_max = (
            data.get("time_max")
            or (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        )

        try:
            events_result = (
                service.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            events = events_result.get("items", [])
            if not events:
                return "No events found for the specified period."

            res = []
            for e in events:
                res.append(
                    f"- {e.get('summary', '(No Title)')} ({e.get('id')}): {e.get('start', {}).get('dateTime') or e.get('start', {}).get('date')}"
                )
            return "\n".join(res)
        except Exception as e:
            return f"Error listing events: {e}"

    def _create_event(self, service, calendar_id, data) -> str:
        if not data.get("summary") or not data.get("start") or not data.get("end"):
            return "Missing required fields for creation: summary, start, and end are required."

        event_body = {
            "summary": data["summary"],
            "description": data.get("description", ""),
            "location": data.get("location", ""),
            "start": {"dateTime": data["start"], "timeZone": "UTC"},
            "end": {"dateTime": data["end"], "timeZone": "UTC"},
        }

        try:
            event = (
                service.events()
                .insert(calendarId=calendar_id, body=event_body)
                .execute()
            )
            return f"Event created successfully: {event.get('htmlLink')}"
        except Exception as e:
            return f"Error creating event: {e}"

    def _update_event(self, service, calendar_id, data) -> str:
        event_id = data.get("event_id")
        if not event_id:
            return "Missing event_id for update."

        try:
            # Get existing event first
            event = (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )

            # Update fields
            if "summary" in data:
                event["summary"] = data["summary"]
            if "description" in data:
                event["description"] = data["description"]
            if "location" in data:
                event["location"] = data["location"]
            if "start" in data:
                event["start"]["dateTime"] = data["start"]
            if "end" in data:
                event["end"]["dateTime"] = data["end"]

            updated_event = (
                service.events()
                .update(calendarId=calendar_id, eventId=event_id, body=event)
                .execute()
            )
            return f"Event updated successfully: {updated_event.get('htmlLink')}"
        except Exception as e:
            return f"Error updating event: {e}"

    def _delete_event(self, service, calendar_id, data) -> str:
        event_id = data.get("event_id")
        if not event_id:
            return "Missing event_id for deletion."

        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return "Event deleted successfully."
        except Exception as e:
            return f"Error deleting event: {e}"
