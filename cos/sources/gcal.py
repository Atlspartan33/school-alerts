"""
Google Calendar source: events from now through the configured horizon.
Used by the briefing pipeline.
"""

from datetime import datetime, timedelta, timezone

import config


def fetch_upcoming_events(calendar_service, days_ahead: int | None = None) -> list[dict]:
    """Fetch events from all visible calendars, today through `days_ahead` days out."""
    days_ahead = days_ahead if days_ahead is not None else config.CALENDAR_DAYS_AHEAD
    now = datetime.now(timezone.utc)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = now + timedelta(days=days_ahead)

    calendars_result = calendar_service.calendarList().list().execute()
    calendar_ids = [
        cal["id"] for cal in calendars_result.get("items", [])
        if cal.get("selected", True)
    ]

    all_events = []
    for cal_id in calendar_ids:
        events_result = calendar_service.events().list(
            calendarId=cal_id,
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        for event in events_result.get("items", []):
            start = event.get("start", {})
            end = event.get("end", {})
            all_day = "date" in start and "dateTime" not in start

            all_events.append({
                "title": event.get("summary", "(no title)"),
                "start": start.get("dateTime", start.get("date", "")),
                "end": end.get("dateTime", end.get("date", "")),
                "location": event.get("location", ""),
                "all_day": all_day,
                "calendar": event.get("organizer", {}).get("displayName", ""),
            })

    all_events.sort(key=lambda e: e["start"])
    return all_events
