"""
Family Chief of Staff — daily brief pipeline.

Gathers Google Calendar, Monday.com, action emails, and this week's school
alerts, then has Claude synthesize one opinionated brief and sends it to
Telegram. Each source fails independently — a Monday outage never kills
the calendar half of the brief, and failures are reported in the footer.

Runs daily via GitHub Actions. Flags: --dry-run (print, don't send).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

import config

# --- Setup ---

os.chdir(Path(__file__).parent)
load_dotenv()

if os.environ.get("GITHUB_ACTIONS"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )
else:
    os.makedirs(os.path.dirname(config.BRIEF_LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(config.BRIEF_LOG_FILE, maxBytes=1_000_000, backupCount=4),
            logging.StreamHandler(),
        ],
    )
log = logging.getLogger("family-cos")

from cos import delivery
from cos.google_auth import GoogleAuthError, get_gmail_service, get_calendar_service
from cos.intelligence import generate_brief
from cos.runlog import record_run
from cos.sources.gcal import fetch_upcoming_events
from cos.sources.gmail_actions import fetch_action_emails
from cos.sources.monday import fetch_monday_items
from cos.state import load_state, get_recent_alerts


SOURCE_LABELS = {
    "calendar": "Calendar",
    "monday": "Tasks",
    "email": "Email",
    "alerts_memory": "School memory",
}


def _health_footer(statuses: dict) -> str:
    """One line listing any degraded sources, or empty if all healthy."""
    failed = [SOURCE_LABELS.get(k, k) for k, v in statuses.items() if v != "ok"]
    if not failed:
        return ""
    return f"\n⚠️ Sources unavailable this run: {', '.join(failed)}"


def run(dry_run: bool = False):
    log.info("Starting daily brief...")
    delivery.DRY_RUN = dry_run
    statuses = {}

    # --- Authenticate Google ---
    gmail_service = calendar_service = None
    try:
        gmail_service = get_gmail_service()
        calendar_service = get_calendar_service()
    except GoogleAuthError as e:
        log.error(f"Google auth failed: {e}")
        try:
            delivery.send_telegram_plain(
                "⚠️ Family Brief: Google token expired!\n\n"
                "Run on your PC: cd school-alerts && python main.py --reauth"
            )
        except Exception:
            pass

    # --- Gather data (each source fails independently) ---
    calendar_events = []
    try:
        if calendar_service is None:
            raise RuntimeError("no Google credentials")
        calendar_events = fetch_upcoming_events(calendar_service)
        statuses["calendar"] = "ok"
        log.info(f"Calendar: {len(calendar_events)} events")
    except Exception as e:
        statuses["calendar"] = f"failed: {e}"
        log.error(f"Calendar failed: {e}")

    monday_items = []
    try:
        monday_items = fetch_monday_items()
        statuses["monday"] = "ok"
        log.info(f"Monday.com: {len(monday_items)} open items")
    except Exception as e:
        statuses["monday"] = f"failed: {e}"
        log.error(f"Monday.com failed: {e}")

    action_emails = []
    try:
        if gmail_service is None:
            raise RuntimeError("no Google credentials")
        action_emails = fetch_action_emails(gmail_service)
        statuses["email"] = "ok"
        log.info(f"Gmail: {len(action_emails)} action-email candidates")
    except Exception as e:
        statuses["email"] = f"failed: {e}"
        log.error(f"Gmail scan failed: {e}")

    recent_alerts = []
    try:
        recent_alerts = get_recent_alerts(load_state())
        statuses["alerts_memory"] = "ok"
        log.info(f"School alert memory: {len(recent_alerts)} recent alert(s)")
    except Exception as e:
        statuses["alerts_memory"] = f"failed: {e}"
        log.error(f"State load failed: {e}")

    if not any(v == "ok" for v in statuses.values()):
        log.error("Every source failed — not generating a brief.")
        delivery.send_telegram_plain(
            "⚠️ Family Brief: all data sources failed this run. "
            "Check the GitHub Actions logs."
        )
        record_run("brief", statuses, sent=False)
        sys.exit(1)

    if not calendar_events and not monday_items and not action_emails and not recent_alerts:
        log.info("No data from any source — quiet day.")
        delivery.send_telegram_plain(
            "📋 Family Brief\n\nNothing on the calendar, no open tasks, "
            "no action emails. Quiet day." + _health_footer(statuses)
        )
        record_run("brief", statuses, sent=True, empty=True)
        return

    # --- Generate brief ---
    try:
        brief = generate_brief(calendar_events, monday_items, action_emails, recent_alerts)
        log.info(f"Brief generated ({len(brief)} chars)")
    except Exception as e:
        log.error(f"Claude API failed: {e}")
        delivery.send_telegram_plain(f"⚠️ Family Brief: generation failed\n{e}")
        record_run("brief", statuses, sent=False, error=str(e))
        sys.exit(1)

    # --- Send ---
    sent = delivery.send_telegram_plain(brief + _health_footer(statuses))
    record_run("brief", statuses, sent=sent,
               counts={"calendar": len(calendar_events), "monday": len(monday_items),
                       "emails": len(action_emails), "school_alerts": len(recent_alerts)})

    if sent:
        log.info("Family Brief sent")
    else:
        log.error("Failed to send brief")
        sys.exit(1)


if __name__ == "__main__":
    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
