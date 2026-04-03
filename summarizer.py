"""
Claude-based email classifier and summarizer.
One API call per email: decides importance AND generates the summary.
"""

import json
from datetime import datetime, timezone

import anthropic

import config

client = None


def _get_client():
    global client
    if client is None:
        client = anthropic.Anthropic()
    return client

SYSTEM_PROMPT = f"""You are a school email filter for a busy parent.

Today's date is {{today}}.

Family context:
{config.FAMILY_CONTEXT}

Use this context to figure out which child an email is about.

Your job:
1. Decide if this email is IMPORTANT (requires awareness or action) or SKIP (noise/informational only).
2. If IMPORTANT, produce a concise, natural-language summary.

IMPORTANT means the email contains:
- A deadline, due date, or form to submit
- A survey or questionnaire that requires parent completion (even if optional)
- A payment or fee required
- A schedule change (early dismissal, cancellation, delay)
- A request for parent response or action
- An event requiring preparation (field trip, spirit day, supplies)
- Something directly affecting the children's day
- Anything related to special education, IEP, disability services, or accommodations
- A digest or summary email from the school or school district (ParentVUE, Cobb County, Garrison Mill digests) — ALWAYS mark these as IMPORTANT even if the content seems routine. The parent wants to see all school digests.

SKIP means:
- Fundraising with no real urgency
- Duplicate reminder with no new information (same content as an email already seen today)
- Broad community/promotional email with nothing requiring parent action

Respond with ONLY valid JSON in this exact format:

If SKIP:
{{"decision": "SKIP", "reason": "brief reason"}}

If IMPORTANT:
{{"decision": "IMPORTANT", "child": "child name or General", "summary": {{
  "urgency": "action" or "awareness" or "fyi",
  "headline": "One short sentence describing what this is about",
  "actions": ["Each action the parent needs to take, as a short phrase", "Another action if applicable"],
  "dates": "Relevant dates/deadlines, or null if none",
  "calendar": "What to add and when, or null if nothing to add",
  "source": "School Name"
}}}}

Urgency guide:
- "action": Parent must DO something (sign form, send money, confirm attendance, respond)
- "awareness": No action right now, but parent needs to know (schedule change, early dismissal, event coming up)
- "fyi": Good to know but no action or impact on the day (voting request, community event, lost and found)"""


def classify_and_summarize(email: dict) -> dict | None:
    """
    Send an email to Claude for classification.
    Returns a formatted summary dict if important, None if skipped.
    """
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    prompt = SYSTEM_PROMPT.replace("{today}", today)

    user_message = f"""Subject: {email["subject"]}
From: {email["sender"]}
Date: {email["date"]}

{email["body"]}"""

    response = _get_client().messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = response.content[0].text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
        else:
            return None

    if result.get("decision") != "IMPORTANT":
        return None

    return result


URGENCY_ICONS = {
    "action": "\U0001f534",      # red circle
    "awareness": "\U0001f7e1",   # yellow circle
    "fyi": "\U0001f7e2",         # green circle
}

URGENCY_LABELS = {
    "action": "Action Required",
    "awareness": "Heads Up",
    "fyi": "FYI",
}


def format_telegram_message(result: dict, email_date: str = "") -> str:
    """Format the Claude result into a clean Telegram message (HTML)."""
    s = result["summary"]
    child = result.get("child", "")
    urgency = s.get("urgency", "awareness")

    icon = URGENCY_ICONS.get(urgency, URGENCY_ICONS["awareness"])
    label = URGENCY_LABELS.get(urgency, URGENCY_LABELS["awareness"])
    child_part = f" {child} —" if child else ""

    # Format the email date for the footer
    date_note = ""
    if email_date:
        try:
            from datetime import datetime as dt
            parsed = dt.fromisoformat(email_date)
            date_note = parsed.strftime("%b %d")
        except Exception:
            date_note = ""

    lines = [
        f"{icon} <b>{child_part} {label}</b>",
        "",
        s["headline"],
    ]

    # Action items
    actions = s.get("actions", [])
    if actions:
        lines.append("")
        for action in actions:
            lines.append(f"\u2192 {action}")

    # Calendar — only if there's something to add
    calendar = s.get("calendar")
    if calendar:
        lines.append(f"\U0001f4c5 {calendar}")

    # Dates — only if present
    dates = s.get("dates")
    if dates:
        lines.append(f"\U0001f552 {dates}")

    # Footer
    source = s.get("source", "")
    footer_parts = [p for p in [source, date_note] if p]
    if footer_parts:
        lines.append("")
        lines.append(f"<i>{' · '.join(footer_parts)}</i>")

    return "\n".join(lines)
