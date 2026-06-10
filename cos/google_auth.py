"""
Google OAuth helper — Gmail + Calendar in a single token.
Supports env-based auth (GitHub Actions) and local token.json.

The token may have been minted with gmail.readonly only. Gmail keeps
working in that case; Calendar calls fail with 403 and the briefing
pipeline degrades gracefully (per-source error handling).
"""

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

log = logging.getLogger("family-cos")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",  # write: add events from alerts/AMA
]
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"


class GoogleAuthError(Exception):
    """Raised when credentials can't be refreshed without a browser."""


# Back-compat alias (alerts pipeline previously raised GmailAuthError)
GmailAuthError = GoogleAuthError


def _get_creds_from_env():
    """Build credentials from environment variables (CI/cloud)."""
    from cos import clean_env
    refresh_token = clean_env("GMAIL_REFRESH_TOKEN")
    client_id = clean_env("GMAIL_CLIENT_ID")
    client_secret = clean_env("GMAIL_CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        return None

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        return creds
    except Exception as e:
        log.error(f"Env-based Google auth failed: {e}")
        return None


def _load_creds():
    """Load credentials from env (CI) or token.json (local)."""
    creds = _get_creds_from_env()
    if creds:
        return creds

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise GoogleAuthError(
                "Google token expired or missing. Run: python main.py --reauth"
            )
    return creds


def interactive_auth():
    """Run interactive OAuth flow (opens browser). For initial setup / reauth."""
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())
    return creds


def get_gmail_service():
    return build("gmail", "v1", credentials=_load_creds())


def get_calendar_service():
    return build("calendar", "v3", credentials=_load_creds())
