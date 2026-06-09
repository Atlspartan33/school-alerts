"""
Gmail source: non-school emails with deadlines, payments, or action items.
Used by the briefing pipeline. School emails are excluded — they're already
covered by the real-time alerts pipeline.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import config
from cos.sources.gmail_school import extract_body, retry


def _build_query() -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.EMAIL_LOOKBACK_DAYS)
    after_epoch = int(cutoff.timestamp())

    exclusions = []
    for domain in config.SCHOOL_DOMAINS:
        exclusions.append(f"-from:@{domain}")
    for kw in config.SCHOOL_KEYWORDS:
        exclusions.append(f'-"{kw}"')

    keywords = " OR ".join(f'subject:"{kw}"' for kw in config.ACTION_KEYWORDS)
    return f"after:{after_epoch} {' '.join(exclusions)} ({keywords})"


def fetch_action_emails(gmail_service) -> list[dict]:
    """Fetch non-school emails from the lookback window that may need action."""
    results = retry(lambda: gmail_service.users().messages().list(
        userId="me", q=_build_query(), maxResults=config.MAX_EMAILS_TO_SCAN
    ).execute())

    messages = results.get("messages", [])
    emails = []

    for msg_stub in messages:
        msg = gmail_service.users().messages().get(
            userId="me", id=msg_stub["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

        body = extract_body(msg["payload"])
        if len(body) > 2000:
            body = body[:2000] + "\n...[truncated]"

        date_str = headers.get("date", "")
        try:
            email_date = parsedate_to_datetime(date_str)
        except Exception:
            email_date = datetime.now(timezone.utc)

        emails.append({
            "subject": headers.get("subject", "(no subject)"),
            "sender": headers.get("from", ""),
            "date": email_date.strftime("%b %d, %Y"),
            "body": body,
        })

    return emails
