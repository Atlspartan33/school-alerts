"""
Configuration for the school email alert system.
Edit these values to match your family's setup.
"""

# --- School Email Filters ---
# Keywords: matches anywhere in sender name, subject, or body.
# This is the easiest way to catch all emails from a school
# even when they come from different sender addresses.
SCHOOL_KEYWORDS = [
    "Garrison Mill",
    "CTLS",
    "CTLSParent",
    "parentvue",
    "eastcobbprep",
    "Kathleen O'Brien",
]

# Optional: exact sender domains
SCHOOL_DOMAINS = [
    "cobbk12.org",
    "cadenceeducation.ccsend.com",
]

# Optional: exact sender addresses
SCHOOL_SENDERS = []

# --- Children ---
# Used by the summarizer to identify which child an email is about.
CHILD_NAMES = [
    "Tori",
    "TJ",
]

# Extra context so the summarizer can connect emails to the right child.
FAMILY_CONTEXT = """
- Tori (daughter): 1st grade at Garrison Mill. Teacher is Sharon Hanna.
- TJ / Terrell (son): goes to Garrison Mill 2 days/week and East Cobb Prep (run by Cadence) the rest of the week. TJ has special education needs and an IEP.
- Garrison Mill emails could be about either child. Use clues like grade, teacher, or class to figure out which.
- Any email related to special education, IEP, disability services, or special needs should be tagged as TJ.
"""

# --- Processing ---
# Only process emails received within this many hours.
MAX_EMAIL_AGE_HOURS = 24

# Max processed IDs to keep in state.json (prevents unbounded growth).
MAX_STATE_IDS = 500

# --- Logging ---
LOG_FILE = "logs/alerts.log"
