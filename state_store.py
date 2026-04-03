"""
Gist-backed state store for GitHub Actions.
Falls back to local state.json when not running in CI.
"""

import json
import logging
import os

import httpx

log = logging.getLogger("school-alerts")

STATE_FILE = "state.json"


def _use_gist() -> bool:
    """Check if we should use Gist storage (GitHub Actions) or local file."""
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
            if "processed_ids" not in data:
                data["processed_ids"] = []
            return data
        except Exception as e:
            log.error(f"Failed to load state from Gist: {e}")
            return {"processed_ids": []}
    else:
        if not os.path.exists(STATE_FILE):
            return {"processed_ids": []}
        with open(STATE_FILE) as f:
            return json.load(f)


def save_state(state: dict):
    """Save state to Gist (CI) or local file."""
    from config import MAX_STATE_IDS

    ids = state.get("processed_ids", [])
    if len(ids) > MAX_STATE_IDS:
        state["processed_ids"] = ids[-MAX_STATE_IDS:]

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
