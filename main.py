"""
Family Chief of Staff — school email alerts (real-time pipeline).

Runs once per invocation:
  1. Load state (processed email IDs)
  2. Fetch new school emails from Gmail
  3. Classify + summarize each with Claude
  4. Send Telegram alerts for important ones (and remember them for the brief)
  5. Save state

Runs on GitHub Actions (every 15 min) or locally via Task Scheduler.
Flags: --reauth (interactive Google login), --dry-run (print, don't send, don't save state).
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
    os.makedirs(os.path.dirname(config.ALERTS_LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            RotatingFileHandler(config.ALERTS_LOG_FILE, maxBytes=1_000_000, backupCount=7),
            logging.StreamHandler(),
        ],
    )
log = logging.getLogger("family-cos")

from cos import delivery
from cos.google_auth import GoogleAuthError, get_gmail_service, interactive_auth
from cos.inbox import alert_buttons
from cos.intelligence import classify_and_summarize, format_telegram_message
from cos.runlog import record_run
from cos.sources.gmail_school import fetch_new_emails
from cos.state import load_state, save_state, remember_alert


# --- Heartbeat ---

def maybe_send_heartbeat(state: dict):
    """Send a daily heartbeat message so you know the system is alive."""
    now = datetime.now(timezone.utc)
    last = state.get("last_heartbeat")

    if last:
        hours_since = (now - datetime.fromisoformat(last)).total_seconds() / 3600
        if hours_since < 24:
            return

    today_stats = state.get("today_stats", {"scanned": 0, "alerted": 0})
    msg = (
        f"✅ School Alerts active\n"
        f"Today: {today_stats['scanned']} emails scanned, {today_stats['alerted']} alert(s) sent\n"
        f"Last check: {now.strftime('%I:%M %p ET')}"
    )
    delivery.send_telegram_plain(msg)
    state["last_heartbeat"] = now.isoformat()
    state["today_stats"] = {"scanned": 0, "alerted": 0}


# --- Main ---

def run(dry_run: bool = False):
    log.info("Starting school email check...")

    if not config.SCHOOL_KEYWORDS and not config.SCHOOL_DOMAINS and not config.SCHOOL_SENDERS:
        log.error("No school keywords, domains, or senders configured. Edit config.py first.")
        sys.exit(1)

    delivery.DRY_RUN = dry_run
    state = load_state()
    processed = set(state.get("processed_ids", []))

    # Authenticate Gmail — alert via Telegram if token is broken
    try:
        service = get_gmail_service()
    except GoogleAuthError as e:
        log.error(f"Google auth failed: {e}")
        last_auth_alert = state.get("last_auth_alert")
        now = datetime.now(timezone.utc)
        should_alert = True
        if last_auth_alert:
            hours_since = (now - datetime.fromisoformat(last_auth_alert)).total_seconds() / 3600
            should_alert = hours_since >= 24

        if should_alert:
            try:
                delivery.send_telegram_plain(
                    "⚠️ School Alerts: Google token expired!\n\n"
                    "Run this on your PC to fix it:\n"
                    "  cd school-alerts && python main.py --reauth"
                )
            except Exception as te:
                log.error(f"Failed to send Telegram auth alert: {te}")
            state["last_auth_alert"] = now.isoformat()
            if not dry_run:
                save_state(state)
        record_run("alerts", {"gmail": f"auth failed: {e}"})
        sys.exit(1)

    emails = fetch_new_emails(service)
    new_emails = [e for e in emails if e["id"] not in processed]

    if not new_emails:
        log.info("No new school emails.")
        state["today_stats"] = state.get("today_stats", {"scanned": 0, "alerted": 0})
        maybe_send_heartbeat(state)
        if not dry_run:
            save_state(state)
        record_run("alerts", {"gmail": "ok"}, scanned=0, alerted=0)
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
            if clean_subj and any(clean_subj in prev or prev in clean_subj for prev in alerted_subjects):
                log.info("  -> Skipped (duplicate of already-alerted email)")
                processed.add(email["id"])
                continue

            result = classify_and_summarize(email)

            if result is None:
                log.info("  -> Skipped (not important)")
                processed.add(email["id"])
            else:
                message = format_telegram_message(result, email.get("date", ""), email["id"])
                log.info("  -> IMPORTANT — sending Telegram alert")
                alert_id = remember_alert(state, result, email["id"])
                has_cal = bool(result.get("summary", {}).get("calendar"))
                extra_recipients = [
                    cid for name, cid in state.get("people_chats", {}).items()
                    if state.get("people_settings", {}).get(name, {}).get("alerts", True)
                ]
                sent = delivery.send_telegram(message, buttons=alert_buttons(alert_id, has_cal),
                                              extra_chat_ids=extra_recipients)
                if sent:
                    alerts_sent += 1
                    if clean_subj:
                        alerted_subjects.append(clean_subj)
                    processed.add(email["id"])
                    log.info("  -> Alert sent successfully")
                else:
                    log.error("  -> Telegram send FAILED — will retry next run")
                    state["recent_alerts"] = [
                        a for a in state.get("recent_alerts", []) if a.get("id") != alert_id
                    ]

        except Exception as e:
            log.error(f"  -> Error processing '{email['subject']}': {e} — will retry next run")

    state["processed_ids"] = list(processed)

    today_stats = state.get("today_stats", {"scanned": 0, "alerted": 0})
    today_stats["scanned"] = today_stats.get("scanned", 0) + len(new_emails)
    today_stats["alerted"] = today_stats.get("alerted", 0) + alerts_sent
    state["today_stats"] = today_stats

    maybe_send_heartbeat(state)
    if not dry_run:
        save_state(state)
    record_run("alerts", {"gmail": "ok"}, scanned=len(new_emails), alerted=alerts_sent)
    log.info(f"Done. {alerts_sent} alert(s) sent out of {len(new_emails)} email(s).")


if __name__ == "__main__":
    if "--reauth" in sys.argv:
        print("Opening browser for Google re-authentication (Gmail + Calendar)...")
        interactive_auth()
        print("Authenticated successfully!")
        sys.exit(0)

    try:
        run(dry_run="--dry-run" in sys.argv)
    except Exception as e:
        log.error(f"Fatal error: {e}")
        sys.exit(1)
