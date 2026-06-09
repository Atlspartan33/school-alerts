"""
Gist-backed state store (GitHub Actions) with local state.json fallback.

Beyond processed email IDs, state now carries short-term memory the brief
uses for follow-through: summaries of recent school alerts, so the daily
brief can surface open action items and events that never made the calendar.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

import config

log = logging.getLogger("family-cos")

STATE_FILE = "state.json"


def _use_gist() -> bool:
    return bool(os.environ.get("GIST_ID") and os.environ.get("GH_TOKEN"))


def load_state() -> dict:
    """Load state from Gist (CI) or local file."""
    if _use_gist():
        gist_id = os.environ["GIST_ID"].strip()
        token = os.environ["GH_TOKEN"].strip()
        try:
            resp = httpx.get(
                f"https://api.github.com/gists/{gist_id}",
                headers={"Authorization": f"token {token}"},
                timeout=10,
            )
            resp.raise_for_status()
            content = resp.json()["files"]["state.json"]["content"]
            data = json.loads(content)
        except Exception as e:
            log.error(f"Failed to load state from Gist: {e}")
            data = {}
    else:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                data = json.load(f)
        else:
            data = {}

    data.setdefault("processed_ids", [])
    data.setdefault("recent_alerts", [])
    return data


def save_state(state: dict):
    """Save state to Gist (CI) or local file."""
    ids = state.get("processed_ids", [])
    if len(ids) > config.MAX_STATE_IDS:
        state["processed_ids"] = ids[-config.MAX_STATE_IDS:]

    _prune_recent_alerts(state)

    if _use_gist():
        gist_id = os.environ["GIST_ID"].strip()
        token = os.environ["GH_TOKEN"].strip()
        try:
            resp = httpx.patch(
                f"https://api.github.com/gists/{gist_id}",
                headers={"Authorization": f"token {token}"},
                json={"files": {"state.json": {"content": json.dumps(state)}}},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            log.error(f"Failed to save state to Gist: {e}")
    else:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)


# --- Brief memory: recent school alerts ---

def remember_alert(state: dict, result: dict):
    """Store a compact record of an alert the family was sent, for the brief."""
    s = result.get("summary", {})
    state.setdefault("recent_alerts", []).append({
        "date": datetime.now(timezone.utc).isoformat(),
        "child": result.get("child", "General"),
        "urgency": s.get("urgency", ""),
        "headline": s.get("headline", ""),
        "actions": s.get("actions", []),
        "dates": s.get("dates"),
        "calendar": s.get("calendar"),
        "source": s.get("source", ""),
    })


def _prune_recent_alerts(state: dict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.RECENT_ALERT_DAYS)
    state["recent_alerts"] = [
        a for a in state.get("recent_alerts", [])
        if datetime.fromisoformat(a["date"]) >= cutoff
    ]


def get_recent_alerts(state: dict) -> list[dict]:
    _prune_recent_alerts(state)
    return state.get("recent_alerts", [])
