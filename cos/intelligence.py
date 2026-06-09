"""
Claude intelligence layer:
  - classify_and_summarize: per-email school-alert classification
  - format_telegram_message: render an alert for Telegram
  - generate_brief: synthesize the daily chief-of-staff brief
"""

import json
import logging
import time
from datetime import datetime, timezone

import httpx

import config
from cos import clean_env

log = logging.getLogger("family-cos")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def _call_claude(model: str, max_tokens: int, system: str, user_message: str) -> str:
    """Call the Messages API via raw httpx with retries.

    Raw httpx (not the SDK) is deliberate: the SDK intermittently fails with
    opaque connection errors on GitHub Actions runners for larger payloads;
    this direct pattern ran the old Family Pulse reliably for months.
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }
    headers = {
        "x-api-key": clean_env("ANTHROPIC_API_KEY"),
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    last_error = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120.0) as http:
                resp = http.post(ANTHROPIC_API_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return next(
                    (b["text"] for b in data["content"] if b["type"] == "text"), ""
                ).strip()
        except Exception as e:
            last_error = e
            log.warning(f"Claude API attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            if attempt < 2:
                time.sleep(5)
    raise last_error


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

    text = _call_claude(config.CLASSIFIER_MODEL, 500, prompt, user_message)

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
        "Sunday": ("SUNDAY RETRO + WEEK PREVIEW", (
            "Sunday: look BACKWARD first, then preview the week.\n"
            "RETRO section (use weekly_stats and recent_school_alerts):\n"
            "- What got handled this week vs. what slipped (open alerts, overdue tasks)\n"
            "- Call out recurring patterns bluntly (e.g. 'swim paperwork late twice "
            "this month — set a standing reminder')\n"
            "WEEK PREVIEW section:\n"
            "- Calendar for Mon-Fri, tasks by priority, anything due early in the week\n"
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

{person_instructions}You will receive data from up to five sources:
1. calendar_events — Google Calendar (family schedule). Events with \
"calendar": "School calendar" come from the school's published feed.
2. monday_items — the family to-do board (group = life area: Finance, House, Kids, etc.)
3. emails — non-school emails that may contain deadlines or payments
4. recent_school_alerts — school alerts the family was already sent this week. \
Each has a status: "open" means nobody marked it handled; "done" means it was \
cleared. Each open alert has a "nudges" count = how many briefs already \
mentioned it.
5. weekly_stats — counters for the Sunday retro (may be empty)

BE A CHIEF OF STAFF, NOT A LIST-READER. Connect the dots across sources:
- If a school alert mentioned an event or deadline that does NOT appear on the \
calendar, flag it: "Not on the calendar yet — reply 'add <thing> to calendar'."
- FOLLOW-THROUGH: only nudge OPEN school alerts. If an open alert has \
nudges >= {nudge_threshold}, escalate — lead the WATCH-OUTS with it and be \
blunt: "Third reminder: ...". Never re-mention alerts marked done.
- MONEY: if emails show a renewal, subscription price increase, or declined \
payment, give it one line under WATCH-OUTS. Same for overdue Finance-group tasks.
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
    weekly_stats: dict | None = None,
    person: str | None = None,
) -> str:
    """Generate the daily chief-of-staff brief, optionally personalized."""
    now = datetime.now(timezone.utc)
    mode_name, mode_instructions = _brief_mode()

    person_instructions = ""
    if person:
        person_instructions = (
            f"THIS BRIEF IS FOR {person.upper()} PERSONALLY. Address them as 'you'.\n"
            f"- Lead with {person}'s own day: events and tasks where the owner is "
            f"{person} (or unassigned-but-clearly-theirs).\n"
            f"- Summarize the other parent's load in ONE line so they can cover "
            f"for each other — don't repeat their whole list.\n\n"
        )

    prompt = BRIEF_PROMPT_TEMPLATE.format(
        family_context=config.FAMILY_CONTEXT,
        today=now.strftime("%A, %B %d, %Y"),
        today_short=now.strftime("%b %d"),
        mode_name=mode_name,
        mode_instructions=mode_instructions,
        person_instructions=person_instructions,
        nudge_threshold=config.NUDGE_ESCALATION_THRESHOLD,
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
        "weekly_stats": weekly_stats or {},
    }, indent=2, default=str)

    return _call_claude(config.BRIEF_MODEL, 2000, prompt, user_message)


# =========================================================================
# Action parsing & ask-me-anything (inbox pipeline)
# =========================================================================

def _json_from_text(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                return None
    return None


def parse_event_from_alert(alert: dict) -> dict | None:
    """Turn a stored school alert into a structured calendar event."""
    now_local = datetime.now(timezone.utc)
    prompt = (
        f"Today is {now_local.strftime('%A, %B %d, %Y')}. Timezone: {config.TIMEZONE}.\n"
        "Convert this school alert into ONE calendar event. Respond with ONLY JSON:\n"
        '{"title": "short event title (include child name)", '
        '"start": "YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD", '
        '"end": "same format or null", "all_day": true/false, '
        '"location": "string or null", "notes": "string or null"}\n'
        "If the alert mentions a date without a time, make it all-day. "
        "If no concrete date can be determined at all, respond with "
        '{"error": "no date"}.'
    )
    text = _call_claude(config.CLASSIFIER_MODEL, 300, prompt,
                        json.dumps(alert, default=str))
    result = _json_from_text(text)
    if not result or result.get("error") or not result.get("start"):
        return None
    return result


AMA_PROMPT = """You are the Massey family's chief of staff, replying to a \
Telegram message from a parent. Today is {today}. Timezone: {tz}.

Family context:
{family_context}

You receive the parent's message plus JSON data: calendar_events (next 7 days),
monday_items (open tasks), open_alerts (school alerts not yet handled).
Replies arrive with up to ~5 minutes of delay, so never promise real-time action.

You can BOTH answer and act. Respond with ONLY JSON:
{{"reply": "plain-text Telegram reply (concise, direct, no markdown)",
  "actions": [
    {{"type": "create_task", "name": "...", "group": "Finance|House|Kids|Terrell To-Do", "due": "YYYY-MM-DD or null"}},
    {{"type": "create_event", "title": "...", "start": "YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD", "end": "... or null", "all_day": true/false, "location": null, "notes": null}},
    {{"type": "mark_done", "alert_id": "..."}}
  ]}}

Rules:
- "actions" is usually empty — only act when the message clearly asks for it
  ("add...", "remind me...", "put ... on the calendar", "done with ...", "we
  handled ...").
- "done"-style messages: find the matching open alert by meaning, return
  mark_done with its id, and confirm what you cleared. If nothing matches, say so.
- Questions ("what's Thursday look like?", "what's still open?"): answer from
  the data, short and scannable. Lead with what matters.
- If asked something the data can't answer, say what you don't have access to.
- Never invent events, tasks, or alert ids."""


def answer_question(message_text: str, calendar_events: list[dict],
                    monday_items: list[dict], open_alerts: list[dict]) -> dict:
    """Answer a free-text Telegram message. Returns {"reply": str, "actions": [...]}."""
    now = datetime.now(timezone.utc)
    prompt = AMA_PROMPT.format(
        today=now.strftime("%A, %B %d, %Y"),
        tz=config.TIMEZONE,
        family_context=config.FAMILY_CONTEXT,
    )

    user_message = json.dumps({
        "message": message_text,
        "calendar_events": calendar_events,
        "monday_items": monday_items[:30],
        "open_alerts": open_alerts,
    }, indent=2, default=str)

    text = _call_claude(config.BRIEF_MODEL, 1000, prompt, user_message)
    result = _json_from_text(text)
    if not result or "reply" not in result:
        return {"reply": "Sorry — I couldn't process that one. Try rephrasing.",
                "actions": []}
    result.setdefault("actions", [])
    return result
