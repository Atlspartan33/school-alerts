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

from datetime import datetime, timezone

from cos import delivery
from cos.google_auth import GoogleAuthError, get_gmail_service, get_calendar_service
from cos.intelligence import generate_brief
from cos.runlog import record_run
from cos.sources.gcal import fetch_upcoming_events
from cos.sources.gmail_actions import fetch_action_emails
from cos.sources.ics import fetch_school_events
from cos.sources.monday import fetch_monday_items
from cos.state import (load_state, save_state, get_recent_alerts, record_nudges,
                       pop_weekly_stats, get_memories, get_reminders)


SOURCE_LABELS = {
    "calendar": "Calendar",
    "school_calendar": "School calendar",
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


def _detect_mode() -> str:
    """Morning brief before 4 PM ET, tomorrow-prep after (each cron lands in its window)."""
    from cos.intelligence import now_local
    return "evening" if now_local().hour >= 16 else "morning"


def run(dry_run: bool = False, mode: str | None = None):
    mode = mode or _detect_mode()
    log.info(f"Starting {mode} brief...")
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

    if config.SCHOOL_ICS_URLS:
        try:
            school_events = fetch_school_events()
            calendar_events = sorted(
                calendar_events + school_events, key=lambda e: e["start"])
            statuses["school_calendar"] = "ok"
            log.info(f"School calendar (ICS): {len(school_events)} events")
        except Exception as e:
            statuses["school_calendar"] = f"failed: {e}"
            log.error(f"School ICS failed: {e}")

    state = None
    recent_alerts = []
    try:
        state = load_state()
        recent_alerts = get_recent_alerts(state)
        statuses["alerts_memory"] = "ok"
        log.info(f"School alert memory: {len(recent_alerts)} recent alert(s)")
    except Exception as e:
        statuses["alerts_memory"] = f"failed: {e}"
        log.error(f"State load failed: {e}")

    # Sunday retro reads (and resets) the weekly counters — morning brief only
    weekly_stats = {}
    if state is not None and mode == "morning":
        from cos.intelligence import now_local
        if now_local().strftime("%A") == "Sunday":
            weekly_stats = pop_weekly_stats(state)

    family_notes = get_memories(state) if state is not None else []
    pending_reminders = get_reminders(state) if state is not None else []

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

    # --- Generate + send (per-person if configured, otherwise broadcast) ---
    people = delivery.person_chat_ids()
    footer = _health_footer(statuses)
    sent = True

    # One-tap Done buttons for the open watch-out items (top 3 by recency)
    open_alerts = [a for a in recent_alerts if a.get("status") == "open" and a.get("id")]
    buttons = [
        [{"text": f"✅ Done: {a['headline'][:32]}", "callback_data": f"done:{a['id']}"}]
        for a in open_alerts[-3:]
    ] or None

    try:
        if people:
            for person, chat_id in people.items():
                brief = generate_brief(calendar_events, monday_items, action_emails,
                                       recent_alerts, weekly_stats, person=person,
                                       mode=mode, family_notes=family_notes,
                                       pending_reminders=pending_reminders)
                log.info(f"Brief for {person} generated ({len(brief)} chars)")
                if not delivery.send_to_chat(chat_id, brief + footer,
                                             parse_mode="HTML", buttons=buttons):
                    sent = False
        else:
            brief = generate_brief(calendar_events, monday_items, action_emails,
                                   recent_alerts, weekly_stats,
                                   mode=mode, family_notes=family_notes,
                                   pending_reminders=pending_reminders)
            log.info(f"Brief generated ({len(brief)} chars)")
            sent = delivery.send_telegram(brief + footer, buttons=buttons)
    except Exception as e:
        log.error(f"Claude API failed: {e}")
        delivery.send_telegram_plain(f"⚠️ Family Brief: generation failed\n{e}")
        record_run("brief", statuses, sent=False, error=str(e))
        sys.exit(1)

    # Follow-through: count this brief as a nudge for every open alert it saw
    if state is not None and not dry_run:
        record_nudges(state, [a["id"] for a in recent_alerts
                              if a.get("status") == "open" and a.get("id")])
        save_state(state)

    record_run("brief", statuses, sent=sent,
               counts={"calendar": len(calendar_events), "monday": len(monday_items),
                       "emails": len(action_emails), "school_alerts": len(recent_alerts)},
               per_person=bool(people))

    if sent:
        log.info("Family Brief sent")
    else:
        log.error("Failed to send brief")
        sys.exit(1)


if __name__ == "__main__":
    cli_mode = None
    if "--evening" in sys.argv:
        cli_mode = "evening"
    elif "--morning" in sys.argv:
        cli_mode = "morning"
    try:
        run(dry_run="--dry-run" in sys.argv, mode=cli_mode)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
