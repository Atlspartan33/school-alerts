"""
School Email Alert System — main orchestration.

Runs once per invocation:
  1. Load state (processed email IDs)
  2. Fetch new school emails from Gmail
  3. Classify + summarize each with Claude
  4. Send Telegram alerts for important ones
  5. Save state

Runs on GitHub Actions (every 15 min) or locally via Task Scheduler.
"""

import logging
import os
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

import config
from gmail_reader import GmailAuthError, get_gmail_service, get_gmail_service_interactive, fetch_new_emails
from summarizer import classify_and_summarize, format_telegram_message
from notifier import send_telegram, send_telegram_plain
from state_store import load_state, save_state

# --- Setup ---

os.chdir(Path(__file__).parent)
load_dotenv()

# In CI, log to stdout only. Locally, log to file + stdout.
if os.environ.get("GITHUB_ACTIONS"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )
else:
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(config.LOG_FILE, maxBytes=1_000_000, backupCount=7),
            logging.StreamHandler(),
        ],
    )
log = logging.getLogger("school-alerts")


# --- Heartbeat ---

def maybe_send_heartbeat(state: dict, emails_scanned: int, alerts_sent: int):
    """Send a daily heartbeat message so you know the system is alive."""
    now = datetime.now(timezone.utc)
    last = state.get("last_heartbeat")

    if last:
        last_dt = datetime.fromisoformat(last)
        hours_since = (now - last_dt).total_seconds() / 3600
        if hours_since < 24:
            return

    today_stats = state.get("today_stats", {"scanned": 0, "alerted": 0})
    scanned = today_stats["scanned"] + emails_scanned
    alerted = today_stats["alerted"] + alerts_sent

    msg = (
        f"\u2705 School Alerts active\n"
        f"Today: {scanned} emails scanned, {alerted} alert(s) sent\n"
        f"Last check: {now.strftime('%I:%M %p ET')}"
    )
    send_telegram_plain(msg)
    state["last_heartbeat"] = now.isoformat()
    state["today_stats"] = {"scanned": 0, "alerted": 0}


# --- Main ---

def run():
    log.info("Starting school email check...")

    if not config.SCHOOL_KEYWORDS and not config.SCHOOL_DOMAINS and not config.SCHOOL_SENDERS:
        log.error("No school keywords, domains, or senders configured. Edit config.py first.")
        sys.exit(1)

    state = load_state()
    processed = set(state.get("processed_ids", []))

    # Authenticate Gmail — alert via Telegram if token is broken
    try:
        service = get_gmail_service()
    except GmailAuthError as e:
        log.error(f"Gmail auth failed: {e}")
        last_auth_alert = state.get("last_auth_alert")
        now = datetime.now(timezone.utc)
        should_alert = True
        if last_auth_alert:
            hours_since = (now - datetime.fromisoformat(last_auth_alert)).total_seconds() / 3600
            should_alert = hours_since >= 24

        if should_alert:
            send_telegram_plain(
                "\u26a0\ufe0f School Alerts: Gmail token expired!\n\n"
                "Run this on your PC to fix it:\n"
                "  cd school-alerts && python main.py --reauth"
            )
            state["last_auth_alert"] = now.isoformat()
            save_state(state)
        return

    emails = fetch_new_emails(service)
    new_emails = [e for e in emails if e["id"] not in processed]

    if not new_emails:
        log.info("No new school emails.")
        today_stats = state.get("today_stats", {"scanned": 0, "alerted": 0})
        state["today_stats"] = today_stats
        maybe_send_heartbeat(state, 0, 0)
        save_state(state)
        return

    log.info(f"Found {len(new_emails)} new school email(s) to process.")
    alerts_sent = 0
    alerted_subjects = []

    for email in new_emails:
        try:
            log.info(f"Processing: {email['subject']}")

            # Skip near-duplicate subjects (forwards, replies, "UPDATED LINK" variants)
            clean_subj = re.sub(
                r'\s*(re|fw|fwd|fwd?)\s*:\s*|\*+|updated\s+link\s*[-:]?\s*',
                '', email['subject'], flags=re.IGNORECASE,
            ).strip().lower()
            clean_subj = re.sub(r'^[\s\-:!]+|[\s\-:!]+$', '', clean_subj)
            if any(clean_subj in prev or prev in clean_subj for prev in alerted_subjects):
                log.info(f"  -> Skipped (duplicate of already-alerted email)")
                processed.add(email["id"])
                continue

            result = classify_and_summarize(email)

            if result is None:
                log.info(f"  -> Skipped (not important)")
                processed.add(email["id"])
            else:
                message = format_telegram_message(result, email.get("date", ""))
                log.info(f"  -> IMPORTANT — sending Telegram alert")
                sent = send_telegram(message)
                if sent:
                    alerts_sent += 1
                    alerted_subjects.append(clean_subj)
                    processed.add(email["id"])
                    log.info(f"  -> Alert sent successfully")
                else:
                    log.error(f"  -> Telegram send FAILED — will retry next run")

        except Exception as e:
            log.error(f"  -> Error processing '{email['subject']}': {e}")
            processed.add(email["id"])

    state["processed_ids"] = list(processed)

    today_stats = state.get("today_stats", {"scanned": 0, "alerted": 0})
    today_stats["scanned"] = today_stats.get("scanned", 0) + len(new_emails)
    today_stats["alerted"] = today_stats.get("alerted", 0) + alerts_sent
    state["today_stats"] = today_stats

    maybe_send_heartbeat(state, len(new_emails), alerts_sent)
    save_state(state)
    log.info(f"Done. {alerts_sent} alert(s) sent out of {len(new_emails)} email(s).")


if __name__ == "__main__":
    if "--reauth" in sys.argv:
        print("Opening browser for Gmail re-authentication...")
        get_gmail_service_interactive()
        print("Gmail authenticated successfully!")
        sys.exit(0)

    try:
        run()
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
