"""
Gmail source: fetch new school emails matching the configured filters.
Used by the real-time alerts pipeline.
"""

import base64
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import config

log = logging.getLogger("family-cos")


def _build_query() -> str:
    """Build a Gmail search query from config filters."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=config.MAX_EMAIL_AGE_HOURS)
    after_epoch = int(cutoff.timestamp())

    parts = []
    for kw in config.SCHOOL_KEYWORDS:
        parts.append(f'"{kw}"')  # matches anywhere: sender, subject, body
    for domain in config.SCHOOL_DOMAINS:
        parts.append(f"from:@{domain}")
    for sender in config.SCHOOL_SENDERS:
        parts.append(f"from:{sender}")

    sender_filter = " OR ".join(parts)
    return f"after:{after_epoch} ({sender_filter})"


def _decode_part(part: dict) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """Reduce an HTML body to readable plain text."""
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _collect_bodies(payload: dict, found: dict):
    """Walk the MIME tree, capturing the first plain and first HTML body."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain" and "plain" not in found:
        decoded = _decode_part(payload)
        if decoded:
            found["plain"] = decoded
    elif mime == "text/html" and "html" not in found:
        decoded = _decode_part(payload)
        if decoded:
            found["html"] = decoded

    for part in payload.get("parts", []):
        _collect_bodies(part, found)


def extract_body(payload: dict) -> str:
    """Extract body text, preferring text/plain and falling back to stripped HTML."""
    found = {}
    _collect_bodies(payload, found)
    if found.get("plain"):
        return found["plain"]
    if found.get("html"):
        return _strip_html(found["html"])
    return ""


def retry(fn, retries=3):
    """Retry a Google API call on transient 500 errors."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if "500" in str(e) or "backend" in str(e).lower():
                log.warning(f"Gmail API transient error (attempt {attempt + 1}): {e}")
                if attempt < retries - 1:
                    time.sleep(3)
                    continue
            raise


def fetch_new_emails(service) -> list[dict]:
    """Fetch emails matching school filters within the configured time window."""
    query = _build_query()

    results = retry(lambda: service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute())

    messages = results.get("messages", [])
    emails = []

    for msg_stub in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_stub["id"], format="full"
        ).execute()

        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}

        body = extract_body(msg["payload"])
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
