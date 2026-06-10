"""
Family Chief of Staff — Telegram inbox runner.

Processes pending button presses and messages (done-replies, questions,
add-task/add-event requests). Runs every ~5 minutes via GitHub Actions,
so replies are near-real-time rather than instant.

Flags: --dry-run (print replies instead of sending; doesn't save state).
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import config

# --- Setup ---

os.chdir(Path(__file__).parent)
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("family-cos")

from cos import delivery
from cos.google_auth import GoogleAuthError, get_calendar_service
from cos.inbox import fire_due_reminders, process_updates
from cos.runlog import record_run
from cos.state import load_state, save_state


def run(dry_run: bool = False):
    delivery.DRY_RUN = dry_run
    state = load_state()

    services = {"calendar": None}
    try:
        services["calendar"] = get_calendar_service()
    except GoogleAuthError as e:
        # Inbox still works for done/task actions; calendar actions will report the error
        log.warning(f"Calendar service unavailable: {e}")

    handled = process_updates(state, services)
    fired = fire_due_reminders(state)

    if handled or fired:
        log.info(f"Handled {handled} update(s), fired {fired} reminder(s)")
        record_run("inbox", {"telegram": "ok"}, handled=handled, reminders_fired=fired)
    if not dry_run:
        save_state(state)


if __name__ == "__main__":
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
