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
from cos import clean_env

log = logging.getLogger("family-cos")

STATE_FILE = "state.json"


def _use_gist() -> bool:
    return bool(os.environ.get("GIST_ID") and os.environ.get("GH_TOKEN"))


def load_state() -> dict:
    """Load state from Gist (CI) or local file."""
    if _use_gist():
        gist_id = clean_env("GIST_ID")
        token = clean_env("GH_TOKEN")
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
        gist_id = clean_env("GIST_ID")
        token = clean_env("GH_TOKEN")
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


# --- Proposals (preview + approve before any write) ---

def _short_id(payload) -> str:
    return hashlib.sha1(
        (json.dumps(payload, sort_keys=True, default=str)
         + datetime.now(timezone.utc).isoformat()).encode()
    ).hexdigest()[:8]


def add_proposal(state: dict, ptype: str, payload: dict, chat_id: str) -> str:
    """Store a pending action awaiting Approve/Cancel. Returns its id."""
    pid = _short_id(payload)
    state.setdefault("proposals", []).append({
        "id": pid,
        "type": ptype,           # create_event | create_task | create_reminder
        "payload": payload,
        "chat_id": chat_id,
        "status": "pending",
        "created": datetime.now(timezone.utc).isoformat(),
    })
    return pid


def get_proposal(state: dict, pid: str) -> dict | None:
    for p in state.get("proposals", []):
        if p.get("id") == pid:
            return p
    return None


def resolve_proposal(state: dict, pid: str, status: str):
    p = get_proposal(state, pid)
    if p is not None:
        p["status"] = status  # executed | cancelled


def prune_proposals(state: dict, max_age_hours: int = 48):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    state["proposals"] = [
        p for p in state.get("proposals", [])
        if datetime.fromisoformat(p["created"]) >= cutoff
    ]


# --- Reminders (one-shot, fired by the inbox runs) ---

def add_reminder(state: dict, text: str, when_iso: str, chat_id: str) -> str:
    rid = _short_id({"text": text, "when": when_iso})
    state.setdefault("reminders", []).append({
        "id": rid, "text": text, "when": when_iso, "chat_id": chat_id,
        "created": datetime.now(timezone.utc).isoformat(),
    })
    return rid


def pop_due_reminders(state: dict, now_utc: datetime) -> list[dict]:
    """Remove and return reminders whose time has arrived."""
    due, remaining = [], []
    for r in state.get("reminders", []):
        try:
            when = datetime.fromisoformat(r["when"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            (due if when <= now_utc else remaining).append(r)
        except ValueError:
            continue  # drop malformed reminders
    state["reminders"] = remaining
    return due


def get_reminders(state: dict) -> list[dict]:
    return state.get("reminders", [])


# --- Family memory (teachable facts, used by AMA and briefs) ---

def add_memory(state: dict, text: str) -> str:
    mid = _short_id({"text": text})
    state.setdefault("family_memory", []).append({
        "id": mid, "text": text,
        "added": datetime.now(timezone.utc).isoformat(),
    })
    return mid


def forget_memory(state: dict, memory_id: str) -> bool:
    memories = state.get("family_memory", [])
    kept = [m for m in memories if m.get("id") != memory_id]
    state["family_memory"] = kept
    return len(kept) < len(memories)


def get_memories(state: dict) -> list[dict]:
    return state.get("family_memory", [])
