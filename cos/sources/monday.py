"""
Monday.com source: family to-do board items with owner, priority, due date,
group, and subitem progress.

Columns are mapped dynamically by type/title rather than hardcoded column IDs,
so board restructuring doesn't silently break the integration. (The previous
reader hardcoded a status column ID that no longer exists on the board.)
"""

import logging

import httpx

from cos import clean_env

log = logging.getLogger("family-cos")

MONDAY_API_URL = "https://api.monday.com/v2"

QUERY = """
query ($boardIds: [ID!]) {
    boards(ids: $boardIds) {
        name
        url
        columns { id title type }
        items_page(limit: 100) {
            items {
                id
                name
                group { title }
                column_values { id text type }
                subitems {
                    name
                    column_values { id text type }
                }
            }
        }
    }
}
"""


def _map_columns(columns: list[dict]) -> dict:
    """Map semantic roles to column IDs by inspecting type and title."""
    roles = {}
    for col in columns:
        ctype, title = col.get("type", ""), (col.get("title") or "").lower()
        if ctype == "people" and "owner" not in roles:
            roles["owner"] = col["id"]
        elif ctype == "date" and "due_date" not in roles:
            roles["due_date"] = col["id"]
        elif ctype == "status":
            if "priority" in title and "priority" not in roles:
                roles["priority"] = col["id"]
            elif "priority" not in title and "status" not in roles:
                roles["status"] = col["id"]
    return roles


def _col_text(column_values: list[dict], col_id: str | None) -> str:
    if not col_id:
        return ""
    for cv in column_values:
        if cv["id"] == col_id:
            return cv.get("text") or ""
    return ""


def _subitem_progress(subitems: list[dict]) -> str:
    """Summarize subitem completion, e.g. '2/5 subtasks done'."""
    if not subitems:
        return ""
    done = 0
    for sub in subitems:
        for cv in sub.get("column_values", []):
            if cv.get("type") == "status" and (cv.get("text") or "").lower() == "done":
                done += 1
                break
    return f"{done}/{len(subitems)} subtasks done"


def fetch_monday_items() -> list[dict]:
    """Fetch open items from the configured Monday.com boards."""
    token = clean_env("MONDAY_API_TOKEN")
    board_ids_str = clean_env("MONDAY_BOARD_IDS")

    if not token or not board_ids_str:
        raise RuntimeError("Monday.com not configured (MONDAY_API_TOKEN / MONDAY_BOARD_IDS)")

    board_ids = [b.strip() for b in board_ids_str.split(",") if b.strip()]

    resp = httpx.post(
        MONDAY_API_URL,
        headers={"Authorization": token, "Content-Type": "application/json"},
        json={"query": QUERY, "variables": {"boardIds": board_ids}},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errors"):
        raise RuntimeError(f"Monday.com API errors: {data['errors']}")

    all_items = []
    for board in data.get("data", {}).get("boards", []):
        board_name = board.get("name", "")
        board_url = board.get("url", "")
        roles = _map_columns(board.get("columns", []))

        for item in board.get("items_page", {}).get("items", []):
            cvs = item.get("column_values", [])
            status = _col_text(cvs, roles.get("status"))
            if status.lower() == "done":
                continue

            entry = {
                "name": item.get("name", ""),
                "group": item.get("group", {}).get("title", ""),
                "owner": _col_text(cvs, roles.get("owner")),
                "priority": _col_text(cvs, roles.get("priority")) or "No priority",
                "due_date": _col_text(cvs, roles.get("due_date")) or "No due date",
                "board": board_name,
            }
            if board_url and item.get("id"):
                entry["url"] = f"{board_url}/pulses/{item['id']}"
            if status:
                entry["status"] = status
            progress = _subitem_progress(item.get("subitems", []))
            if progress:
                entry["progress"] = progress
            all_items.append(entry)

    return all_items
