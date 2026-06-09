"""
Claude intelligence layer:
  - classify_and_summarize: per-email school-alert classification
  - format_telegram_message: render an alert for Telegram
  - generate_brief: synthesize the daily chief-of-staff brief
"""

import json
import logging
from datetime import datetime, timezone

import anthropic

import config

log = logging.getLogger("family-cos")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=120.0, max_retries=3)
    return _client


# =========================================================================
# School email classification (alerts pipeline)
# =========================================================================

CLASSIFIER_PROMPT = f"""You are a school email filter for a busy parent.

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
    """Classify one email with Claude. Returns the result dict if important, None if skipped."""
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
    prompt = CLASSIFIER_PROMPT.replace("{today}", today)

    user_message = f"""Subject: {email["subject"]}
From: {email["sender"]}
Date: {email["date"]}

{email["body"]}"""

    response = _get_client().messages.create(
        model=config.CLASSIFIER_MODEL,
        max_tokens=500,
        system=prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "").strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
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

    date_note = ""
    if email_date:
        try:
            date_note = datetime.fromisoformat(email_date).strftime("%b %d")
        except Exception:
            date_note = ""

    lines = [
        f"{icon} <b>{child_part} {label}</b>",
        "",
        s["headline"],
    ]

    actions = s.get("actions", [])
    if actions:
        lines.append("")
        for action in actions:
            lines.append(f"→ {action}")

    calendar = s.get("calendar")
    if calendar:
        lines.append(f"\U0001f4c5 {calendar}")

    dates = s.get("dates")
    if dates:
        lines.append(f"\U0001f552 {dates}")

    source = s.get("source", "")
    footer_parts = [p for p in [source, date_note] if p]
    if footer_parts:
        lines.append("")
        lines.append(f"<i>{' · '.join(footer_parts)}</i>")

    return "\n".join(lines)


# =========================================================================
# Daily brief (chief-of-staff pipeline)
# =========================================================================

def _brief_mode() -> tuple[str, str]:
    """Return (mode_name, mode_instructions) based on current day of week."""
    day = datetime.now(timezone.utc).strftime("%A")

    modes = {
        "Monday": ("WEEK AHEAD", (
            "Monday overview. Set the tone for the week.\n"
            "- Show the full calendar for the week\n"
            "- List tasks grouped by priority\n"
            "- Highlight anything due in the next 3 days\n"
            "- End with: 'Your #1 priority this week is...'"
        )),
        "Tuesday": ("TUESDAY CHECK-IN", (
            "Quick check-in. Keep it short.\n"
            "- Only today's and tomorrow's calendar events\n"
            "- High priority tasks that haven't been started\n"
            "- Any emails needing a response today\n"
            "- End with one clear action for today"
        )),
        "Wednesday": ("MID-WEEK NUDGE", (
            "Wednesday nudge. Be direct and action-oriented.\n"
            "- Only mention events for Wed-Fri\n"
            "- Focus on tasks that are HIGH priority or overdue\n"
            "- Call out anything due THIS WEEK that hasn't been started\n"
            "- Use nudge language: 'Have you started X yet?' / 'X is due in 2 days'\n"
            "- End with the single most urgent thing to do TODAY"
        )),
        "Thursday": ("THURSDAY CHECK-IN", (
            "Quick check-in. Keep it short.\n"
            "- Only today's and Friday's calendar events\n"
            "- Tasks due by end of week\n"
            "- Any emails needing a response\n"
            "- End with: 'Before the weekend, make sure you...'"
        )),
        "Friday": ("WEEKEND PREP", (
            "Friday wrap-up. Help them close out the week.\n"
            "- Only show weekend calendar events (Sat/Sun)\n"
            "- List tasks that are overdue or due next week\n"
            "- Call out anything that should be finished before the weekend\n"
            "- Suggest what can wait vs. what should be knocked out tonight\n"
            "- End with: 'If you do one thing tonight, make it...'"
        )),
        "Saturday": ("WEEKEND UPDATE", (
            "Light weekend check-in. Keep it brief.\n"
            "- Only show Sunday and Monday calendar events\n"
            "- Only mention HIGH priority tasks\n"
            "- Skip emails unless something is due Monday\n"
            "- Keep the tone relaxed"
        )),
        "Sunday": ("WEEK PREVIEW", (
            "Sunday evening preview of the week ahead.\n"
            "- Show the full calendar for Mon-Fri\n"
            "- List all tasks by priority\n"
            "- Flag anything due early in the week\n"
            "- End with: 'Your biggest priority tomorrow is...'"
        )),
    }
    return modes[day]


BRIEF_PROMPT_TEMPLATE = """You are the Massey family's chief of staff — a sharp, \
no-nonsense operator who keeps the household running.

Family context:
{family_context}

Today is {today}.
This is the {mode_name} briefing.

{mode_instructions}

You will receive data from up to four sources:
1. calendar_events — Google Calendar (family schedule)
2. monday_items — the family to-do board (group = life area: Finance, House, Kids, etc.)
3. emails — non-school emails that may contain deadlines or payments
4. recent_school_alerts — school alerts the family was already sent this week \
(each may carry action items, dates, and suggested calendar entries)

BE A CHIEF OF STAFF, NOT A LIST-READER. Connect the dots across sources:
- If a school alert mentioned an event or deadline that does NOT appear on the \
calendar, flag it: "Not on the calendar yet — add it."
- If a school alert had action items days ago and nothing suggests they were \
handled, nudge: "Still open from Tuesday's alert: ..."
- If a task's due date collides with a busy calendar day, say so.
- If two things compete for the same evening, call the conflict.
- Surface at most the 2-3 sharpest insights — quality over quantity.

FORMAT (plain text only, no HTML/markdown):

━━━━━━━━━━━━━━━━━━━━━━━━━━
{mode_name} — {today_short}
━━━━━━━━━━━━━━━━━━━━━━━━━━

[Sections based on the mode instructions above]

WATCH-OUTS
[Cross-source insights: missed calendar entries, open school action items,
conflicts. Skip this section entirely if there are none — never pad it.]

BOTTOM LINE
[2-3 direct, opinionated sentences. Tell them what to prioritize.
Don't hedge. Don't say "consider" — say "do this."]
━━━━━━━━━━━━━━━━━━━━━━━━━━

RULES:
- Keep under 3500 characters total
- Plain text ONLY — no HTML, no markdown, no bold/italic
- Use short day names (Mon, Tue) and 12-hour time
- For tasks: show priority, owner, and subtask progress when present. Skip vague items or bare URLs.
- For emails: only include ones with genuine deadlines or payments. Skip marketing.
- Be opinionated. "Do X first" not "You might want to consider X"
- If a data source is empty or failed, skip it — don't mention it"""


def generate_brief(
    calendar_events: list[dict],
    monday_items: list[dict],
    action_emails: list[dict],
    recent_alerts: list[dict],
) -> str:
    """Generate the daily chief-of-staff brief."""
    now = datetime.now(timezone.utc)
    mode_name, mode_instructions = _brief_mode()

    prompt = BRIEF_PROMPT_TEMPLATE.format(
        family_context=config.FAMILY_CONTEXT,
        today=now.strftime("%A, %B %d, %Y"),
        today_short=now.strftime("%b %d"),
        mode_name=mode_name,
        mode_instructions=mode_instructions,
    )

    trimmed_emails = [
        {"subject": e["subject"], "sender": e["sender"], "date": e["date"]}
        for e in action_emails[:15]
    ]

    user_message = json.dumps({
        "calendar_events": calendar_events,
        "monday_items": monday_items[:30],
        "emails": trimmed_emails,
        "recent_school_alerts": recent_alerts,
    }, indent=2, default=str)

    response = _get_client().messages.create(
        model=config.BRIEF_MODEL,
        max_tokens=2000,
        system=prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    return next((b.text for b in response.content if b.type == "text"), "").strip()
