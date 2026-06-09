"""
Gist-backed state store (GitHub Actions) with local state.json fallback.

Beyond processed email IDs, state carries:
  - recent_alerts: school alerts from the last 7 days, each with an id and
    open/done status, so the brief can chase follow-through and the inbox
    can mark items handled
  - telegram_offset: getUpdates cursor for the inbox
  - weekly_stats: counters the Sunday retro reads
"""

import hashlib
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
    data.setdefault("weekly_stats", {})
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


# --- Brief memory: recent school alerts with follow-through status ---

def remember_alert(state: dict, result: dict, email_id: str = "") -> str:
    """Store a compact record of an alert the family was sent. Returns its id."""
    alert_id = hashlib.sha1(
        (email_id or json.dumps(result, sort_keys=True)).encode()
    ).hexdigest()[:8]

    s = result.get("summary", {})
    state.setdefault("recent_alerts", []).append({
        "id": alert_id,
        "status": "open",
        "nudges": 0,
        "date": datetime.now(timezone.utc).isoformat(),
        "child": result.get("child", "General"),
        "urgency": s.get("urgency", ""),
        "headline": s.get("headline", ""),
        "actions": s.get("actions", []),
        "dates": s.get("dates"),
        "calendar": s.get("calendar"),
        "source": s.get("source", ""),
    })
    bump_weekly_stat(state, "alerts_sent")
    return alert_id


def get_alert(state: dict, alert_id: str) -> dict | None:
    for a in state.get("recent_alerts", []):
        if a.get("id") == alert_id:
            return a
    return None


def mark_alert_done(state: dict, alert_id: str) -> bool:
    alert = get_alert(state, alert_id)
    if alert is None:
        return False
    if alert.get("status") != "done":
        alert["status"] = "done"
        alert["done_at"] = datetime.now(timezone.utc).isoformat()
        bump_weekly_stat(state, "alerts_cleared")
    return True


def record_nudges(state: dict, alert_ids: list[str]):
    """Count that these open alerts appeared in a brief (drives escalation)."""
    for alert_id in alert_ids:
        alert = get_alert(state, alert_id)
        if alert is not None and alert.get("status") == "open":
            alert["nudges"] = alert.get("nudges", 0) + 1


def _prune_recent_alerts(state: dict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.RECENT_ALERT_DAYS)
    state["recent_alerts"] = [
        a for a in state.get("recent_alerts", [])
        if datetime.fromisoformat(a["date"]) >= cutoff
    ]


def get_recent_alerts(state: dict) -> list[dict]:
    _prune_recent_alerts(state)
    return state.get("recent_alerts", [])


# --- Weekly stats (Sunday retro) ---

def bump_weekly_stat(state: dict, key: str, by: int = 1):
    stats = state.setdefault("weekly_stats", {})
    stats[key] = stats.get(key, 0) + by


def pop_weekly_stats(state: dict) -> dict:
    """Read and reset the weekly counters (called by the Sunday retro)."""
    stats = state.get("weekly_stats", {})
    state["weekly_stats"] = {}
    return stats


# --- Telegram inbox cursor ---

def get_telegram_offset(state: dict) -> int | None:
    return state.get("telegram_offset")


def set_telegram_offset(state: dict, offset: int):
    state["telegram_offset"] = offset
