"""
Family Chief of Staff — configuration.

Anything that identifies the family (names, schools, teachers, filters)
lives OUTSIDE this public file: in the FAMILY_CONFIG env var (a JSON repo
secret in CI) or a gitignored local family_config.json. This file holds
only generic knobs and the loader.
"""

import json
import os
from pathlib import Path

# =========================================================================
# Family config loader (private details live in env/secret, not in git)
# =========================================================================

def _load_family_config() -> dict:
    raw = (os.environ.get("FAMILY_CONFIG") or "").strip().strip("﻿")
    if not raw:
        local = Path(__file__).parent / "family_config.json"
        if local.exists():
            raw = local.read_text(encoding="utf-8-sig")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"WARNING: FAMILY_CONFIG is not valid JSON ({e}); running without family details")
        return {}


_fam = _load_family_config()

# =========================================================================
# Children & schools (from family config)
# =========================================================================

CHILD_NAMES = _fam.get("child_names", [])
FAMILY_CONTEXT = _fam.get("family_context", "")

# School email filters: keywords match anywhere in sender, subject, or body.
SCHOOL_KEYWORDS = _fam.get("school_keywords", [])
SCHOOL_DOMAINS = _fam.get("school_domains", [])
SCHOOL_SENDERS = _fam.get("school_senders", [])

# =========================================================================
# Action-email scan (briefing pipeline) — non-school emails with deadlines
# =========================================================================

ACTION_KEYWORDS = [
    "deadline",
    "due",
    "action required",
    "RSVP",
    "confirm",
    "payment",
    "invoice",
    "appointment",
    "reminder",
    "expiring",
    "renewal",
    "registration",
    "subscription",
    "auto-renew",
    "price increase",
    "declined",
]

# =========================================================================
# School calendar feeds (ICS) — events that never arrive by email
# =========================================================================

SCHOOL_ICS_URLS = _fam.get("school_ics_urls", [])

# =========================================================================
# Telegram recipients
# =========================================================================
# TELEGRAM_CHAT_IDS (env) — broadcast list, used for alerts and system messages.
# Per-person briefs: family config maps person name -> env var holding their
# chat id, e.g. {"people": {"Alex": "TELEGRAM_CHAT_ID_ALEX"}}.

PEOPLE = _fam.get("people", {})

# =========================================================================
# Actions
# =========================================================================

TIMEZONE = "America/New_York"
DEFAULT_TASK_GROUP = "Kids"        # Monday group for tasks created from school alerts
NUDGE_ESCALATION_THRESHOLD = 2     # briefs an item can appear in before escalation

# =========================================================================
# Models
# =========================================================================

# Per-email classifier: high volume, runs every 15 minutes.
CLASSIFIER_MODEL = "claude-sonnet-4-6"

# Daily brief synthesis: once a day, quality matters most.
BRIEF_MODEL = "claude-opus-4-8"

# =========================================================================
# Processing
# =========================================================================

MAX_EMAIL_AGE_HOURS = 24      # alerts: only look at emails this fresh
EMAIL_LOOKBACK_DAYS = 7       # brief: action-email scan window
MAX_EMAILS_TO_SCAN = 30       # brief: cap on action-email candidates
CALENDAR_DAYS_AHEAD = 7       # brief: how far ahead to look
MAX_STATE_IDS = 500           # cap processed email IDs in state
RECENT_ALERT_DAYS = 7         # how long alert summaries stay in brief memory

# =========================================================================
# Logging
# =========================================================================

ALERTS_LOG_FILE = "logs/alerts.log"
BRIEF_LOG_FILE = "logs/brief.log"
RUN_LOG_FILE = "logs/runs.jsonl"

# Back-compat alias (old code imported LOG_FILE)
LOG_FILE = ALERTS_LOG_FILE
