"""
Gmail API client: authenticate via OAuth and fetch new school emails.
"""

import base64
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_PATH = "token.json"
CREDENTIALS_PATH = "credentials.json"


class GmailAuthError(Exception):
    """Raised when Gmail credentials cannot be refreshed without user interaction."""


def _get_creds_from_env():
    """Build credentials from environment variables (for CI/cloud)."""
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")

    if not all([refresh_token, client_id, client_secret]):
        return None

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


def get_gmail_service():
    """Authenticate and return a Gmail API service object.

    Uses environment variables when available (CI/cloud), otherwise
    falls back to token.json (local). Raises GmailAuthError if neither works.
    """
    # Try env-based auth first (GitHub Actions / cloud)
    creds = _get_creds_from_env()
    if creds:
        return build("gmail", "v1", credentials=creds)

    # Fall back to token.json (local machine)
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise GmailAuthError(
                "Gmail token expired or missing. Run main.py manually to re-authenticate."
            )
    return build("gmail", "v1", credentials=creds)


def get_gmail_service_interactive():
    """Authenticate with interactive browser login. Use for manual re-auth only."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _build_query() -> str:
    """Build a Gmail search query from config filters."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.MAX_EMAIL_AGE_HOURS)
    after_epoch = int(cutoff.timestamp())

    # Collect all OR conditions
    parts = []
    for kw in config.SCHOOL_KEYWORDS:
        parts.append(f'"{kw}"')  # matches anywhere: sender, subject, body
    for domain in config.SCHOOL_DOMAINS:
        parts.append(f"from:@{domain}")
    for sender in config.SCHOOL_SENDERS:
        parts.append(f"from:{sender}")

    sender_filter = " OR ".join(parts)
    return f"after:{after_epoch} ({sender_filter})"


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        result = _extract_body(part)
        if result:
            return result
    return ""


def fetch_new_emails(service) -> list[dict]:
    """Fetch unread emails matching school filters within the configured time window."""
    query = _build_query()

    results = service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg_stub in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_stub["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

        body = _extract_body(msg["payload"])
        if len(body) > 4000:
            body = body[:4000] + "\n...[truncated]"

        date_str = headers.get("date", "")
        try:
            email_date = parsedate_to_datetime(date_str)
        except Exception:
            email_date = datetime.now(timezone.utc)

        emails.append({
            "id": msg["id"],
            "subject": headers.get("subject", "(no subject)"),
            "sender": headers.get("from", ""),
            "date": email_date.isoformat(),
            "body": body,
        })

    return emails
