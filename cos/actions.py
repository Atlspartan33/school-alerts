"""
Actions the chief of staff can take on the family's behalf:
  - create a Google Calendar event
  - create a Monday.com task
Both return a human-readable confirmation string (or raise).
"""

import logging
import os
from datetime import datetime, timedelta

import httpx

import config
from cos.sources.monday import MONDAY_API_URL

log = logging.getLogger("family-cos")


class InsufficientScope(Exception):
    """Calendar write attempted with a read-only token."""


# --- Google Calendar ---

def create_calendar_event(calendar_service, event: dict) -> str:
    """Create an event on the primary calendar.

    event: {"title": str, "start": ISO datetime or date, "end": same or None,
            "all_day": bool, "location": str|None, "notes": str|None}
    """
    title = event.get("title") or "Family event"
    start = event["start"]
    end = event.get("end")
    all_day = bool(event.get("all_day"))

    if all_day:
        start_date = start[:10]
        end_date = (end or start)[:10]
        # Google all-day end dates are exclusive
        end_date = (datetime.fromisoformat(end_date) + timedelta(days=1)).strftime("%Y-%m-%d")
        body_start = {"date": start_date}
        body_end = {"date": end_date}
        when = start_date
    else:
        if not end:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
        body_start = {"dateTime": start, "timeZone": config.TIMEZONE}
        body_end = {"dateTime": end, "timeZone": config.TIMEZONE}
        when = datetime.fromisoformat(start).strftime("%a %b %d, %I:%M %p")

    body = {"summary": title, "start": body_start, "end": body_end}
    if event.get("location"):
        body["location"] = event["location"]
    if event.get("notes"):
        body["description"] = event["notes"]

    try:
        calendar_service.events().insert(calendarId="primary", body=body).execute()
    except Exception as e:
        if "insufficient" in str(e).lower() or "403" in str(e):
            raise InsufficientScope(
                "Calendar is read-only with the current Google token. "
                "Run: python main.py --reauth (then update the GMAIL_REFRESH_TOKEN secret)."
            ) from e
        raise

    return f'Added to calendar: "{title}" — {when}'


# --- Monday.com ---

def _monday_request(query: str, variables: dict) -> dict:
    token = (os.environ.get("MONDAY_API_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("MONDAY_API_TOKEN not configured")
    resp = httpx.post(
        MONDAY_API_URL,
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Monday.com API errors: {data['errors']}")
    return data["data"]


def create_monday_task(name: str, group_title: str | None = None,
                       due_date: str | None = None) -> str:
    """Create a task on the first configured Monday board.

    group_title: e.g. "Kids", "House" (defaults to config.DEFAULT_TASK_GROUP)
    due_date: "YYYY-MM-DD" or None
    """
    board_ids_str = (os.environ.get("MONDAY_BOARD_IDS") or "").strip()
    if not board_ids_str:
        raise RuntimeError("MONDAY_BOARD_IDS not configured")
    board_id = board_ids_str.split(",")[0].strip()

    meta = _monday_request(
        "query ($boardIds: [ID!]) { boards(ids: $boardIds) { groups { id title } columns { id title type } } }",
        {"boardIds": [board_id]},
    )["boards"][0]

    wanted = (group_title or config.DEFAULT_TASK_GROUP).lower()
    group_id = None
    for g in meta.get("groups", []):
        if g["title"].lower() == wanted:
            group_id = g["id"]
            break
    if group_id is None and meta.get("groups"):
        group_id = meta["groups"][0]["id"]

    column_values = {}
    if due_date:
        for col in meta.get("columns", []):
            if col["type"] == "date":
                column_values[col["id"]] = {"date": due_date}
                break

    import json as _json
    variables = {
        "boardId": board_id,
        "groupId": group_id,
        "name": name,
        "columnValues": _json.dumps(column_values),
    }
    _monday_request(
        """
        mutation ($boardId: ID!, $groupId: String, $name: String!, $columnValues: JSON) {
            create_item(board_id: $boardId, group_id: $groupId, item_name: $name,
                        column_values: $columnValues) { id }
        }
        """,
        variables,
    )

    group_label = group_title or config.DEFAULT_TASK_GROUP
    due_label = f", due {due_date}" if due_date else ""
    return f'Created task: "{name}" ({group_label}{due_label})'
