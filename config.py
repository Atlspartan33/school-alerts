"""
Family Chief of Staff — configuration.
One place for everything about the Massey family the system needs to know.
"""

# =========================================================================
# Children & schools
# =========================================================================

CHILD_NAMES = ["Tori", "TJ"]

FAMILY_CONTEXT = """
- Terrell (dad): manages family logistics. Primary recipient of alerts and briefs.
- Kimberly / Kim (mom): shares parenting duties. Will also receive briefs.
- Tori (daughter): 1st grade at Garrison Mill Elementary. Teacher: Sharon Hanna.
- TJ / Terrell (son): Garrison Mill ~2 days/week, East Cobb Prep (run by Cadence
  Education) the rest of the week. TJ has special education needs and an IEP.
- Garrison Mill emails could be about either child. Use clues like grade,
  teacher, or class to figure out which. If unclear, label it General.
- Anything about special education, IEP, disability services, accommodations,
  or therapy is about TJ.
"""

# =========================================================================
# School email filters (real-time alerts pipeline)
# =========================================================================

# Keywords: match anywhere in sender name, subject, or body.
SCHOOL_KEYWORDS = [
    "Garrison Mill",
    "CTLS",
    "CTLSParent",
    "parentvue",
    "eastcobbprep",
    "Kathleen O'Brien",
]

# Sender domains that are almost always school-related.
SCHOOL_DOMAINS = [
    "cobbk12.org",
    "cadenceeducation.ccsend.com",
]

# Exact sender addresses (optional).
SCHOOL_SENDERS = []

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
# Add iCal/ICS URLs here (e.g. the Cobb County district calendar feed or a
# Google Calendar "secret address in iCal format"). Empty list = source off.

SCHOOL_ICS_URLS = []

# =========================================================================
# Telegram recipients
# =========================================================================
# TELEGRAM_CHAT_IDS (env) — broadcast list, used for alerts and system messages.
# Optional per-person briefs: set TELEGRAM_CHAT_ID_TERRELL and/or
# TELEGRAM_CHAT_ID_KIM (env) and each person gets a personalized daily brief.

PEOPLE = {
    "Terrell": "TELEGRAM_CHAT_ID_TERRELL",
    "Kim": "TELEGRAM_CHAT_ID_KIM",
}

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
