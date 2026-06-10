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
from zoneinfo import ZoneInfo

import httpx

import config
from cos import clean_env

log = logging.getLogger("family-cos")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def now_local() -> datetime:
    """Family-local time. All date reasoning is done in ET, not UTC."""
    return datetime.now(ZoneInfo(config.TIMEZONE))


# Neutral-on-people, blunt-on-tasks. The bot must never read like it's
# keeping score between the parents.
TONE_RULES = """TONE — non-negotiable:
- Be blunt about TASKS and DEADLINES ("sign it tonight", "this is the third
  reminder", "this won't happen Sunday — move it").
- Be neutral about PEOPLE. Never attribute failure, lateness, or neglect to a
  named person. Banned framings: "X forgot", "X hasn't done", "X failed",
  "X dropped the ball", "X is late on", "again".
- Reframe person-attached problems operationally: "owner unclear",
  "coverage gap", "still open", "needs confirmation", "decision needed",
  "no one is on pickup yet".
- Owner names may appear as plain facts of assignment ("Owner: Kim"), never
  inside criticism."""


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
    today = now_local().strftime("%A, %B %d, %Y")
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


def format_telegram_message(result: dict, email_date: str = "",
                            email_id: str = "") -> str:
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
    if email_id:
        footer_parts.append(
            f'<a href="https://mail.google.com/mail/u/0/#all/{email_id}">Open email</a>'
        )
    if footer_parts:
        lines.append("")
        lines.append(f"<i>{' · '.join(footer_parts)}</i>")

    return "\n".join(lines)


# =========================================================================
# Daily brief (chief-of-staff pipeline)
# =========================================================================

def _brief_mode(mode: str = "morning") -> tuple[str, str]:
    """Return (mode_name, mode_instructions) for the run."""
    if mode == "evening":
        return "TOMORROW PREP", (
            "Evening prep brief — sent the night before. Look at TOMORROW only.\n"
            "- Tomorrow's calendar, in order, with times\n"
            "- PREP TONIGHT: what to pack, bring, sign, confirm, or lay out "
            "(infer from events: swim → suit and towel; field trip → slip and lunch; "
            "early dismissal → pickup plan)\n"
            "- COVERAGE: any event tomorrow where it's unclear which adult is on it "
            "— call it a coverage gap, don't assign blame\n"
            "- Tasks due tomorrow and any reminders pending\n"
            "- Keep it SHORT — this is a checklist, not an essay\n"
            "- End with: 'Tonight before bed: ...' (one line)"
        )

    day = now_local().strftime("%A")

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


BRIEF_PROMPT_TEMPLATE = """You are the family's chief of staff — a sharp, \
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
6. family_notes — facts the family taught the bot (treat as ground truth)
7. pending_reminders — one-shot reminders already scheduled (don't re-suggest them)
8. backlog_suggestion — at most one improvement to the assistant itself
   (Sunday only). If present, add ONE line at the very end of the message:
   💡 <b>Worth building next:</b> [title] (#[number]) — [one short clause on
   why it helps]. Never more than one; skip the line entirely if absent.

{tone_rules}

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

FORMAT — Telegram HTML (parse_mode=HTML). Allowed tags ONLY: <b>, <i>,
<a href="...">, and ONE <blockquote expandable> at the end. Nothing else —
no other tags, no markdown, no ━ divider lines.

<b>{mode_name} · {today_short}</b>

[Sections per the mode instructions. Each section header is an emoji +
<b>Title</b> on its own line, e.g.:
🗓 <b>Today</b> / 📅 <b>Tomorrow</b> / ✅ <b>Due</b> / 📧 <b>Emails</b>]

⚠️ <b>Watch-outs</b>
[Cross-source insights: missed calendar entries, open school action items,
money issues, conflicts. Bold the subject of each, e.g.
<b>Life Time DocuSign unsigned</b> — Tori swims Thu 3:45.
Skip this section entirely if there are none — never pad it.]

→ <b>Bottom line:</b> [2-3 direct, opinionated sentences. Tell them what to
prioritize. Don't hedge. Don't say "consider" — say "do this."]

<blockquote expandable>[THE LONG TAIL goes here so it collapses: the full
task list, remaining calendar events, lower-priority emails. One compact
line each. Skip this block entirely if everything already fit above.]</blockquote>

HTML RULES (critical — broken markup means the message degrades to raw text):
- Escape literal characters in content: & as &amp;  < as &lt;  > as &gt;
- Every <b>, <i>, <a>, <blockquote expandable> must be properly closed
- calendar_events with a "link" and monday_items with a "url": make the
  title/name a link, e.g. <a href="URL">Sprayberry Animal Hospital</a>.
  Never print bare URLs.
- Keep the part BEFORE the blockquote under ~1200 characters — lead with
  what matters; the blockquote holds the rest. Whole message under 3500.

CONTENT RULES:
- Use short day names (Mon, Tue) and 12-hour times like 7:00 AM
- For tasks: show priority, owner, and subtask progress when present. Skip vague items.
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
    mode: str = "morning",
    family_notes: list[dict] | None = None,
    pending_reminders: list[dict] | None = None,
    backlog_suggestion: dict | None = None,
) -> str:
    """Generate the chief-of-staff brief (morning or evening prep), optionally personalized."""
    now = now_local()
    mode_name, mode_instructions = _brief_mode(mode)

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
        tone_rules=TONE_RULES,
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
        "family_notes": [m["text"] for m in (family_notes or [])],
        "pending_reminders": [
            {"text": r["text"], "when": r["when"]} for r in (pending_reminders or [])
        ],
        "backlog_suggestion": backlog_suggestion,
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
    prompt = (
        f"Today is {now_local().strftime('%A, %B %d, %Y')}. Timezone: {config.TIMEZONE}.\n"
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


def revise_proposal(ptype: str, payload: dict, instruction: str) -> dict | None:
    """Apply a free-text edit instruction to a pending proposal payload."""
    shapes = {
        "create_event": '{"title": "...", "start": "YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD", '
                        '"end": "... or null", "all_day": true/false, "location": null, "notes": null}',
        "create_task": '{"name": "...", "group": "Finance|House|Kids|Terrell To-Do", "due": "YYYY-MM-DD or null"}',
        "create_reminder": '{"text": "...", "when": "YYYY-MM-DDTHH:MM:SS (local time)"}',
    }
    prompt = (
        f"Today is {now_local().strftime('%A, %B %d, %Y')}. Timezone: {config.TIMEZONE}.\n"
        f"A parent is editing a pending {ptype} action. Apply their instruction to the "
        f"payload and respond with ONLY the complete revised JSON in this shape:\n"
        f"{shapes.get(ptype, '{}')}\n"
        "Keep every field they didn't ask to change. If the instruction makes no sense "
        'for this action, respond with {"error": "brief reason"}.'
    )
    user_message = json.dumps({"current": payload, "instruction": instruction}, default=str)
    text = _call_claude(config.CLASSIFIER_MODEL, 300, prompt, user_message)
    result = _json_from_text(text)
    if not result or result.get("error"):
        return None
    return result


AMA_PROMPT = """You are the family's chief of staff, replying to a \
Telegram message from a parent. Right now it is {today} ({tz}).

Family context:
{family_context}

{tone_rules}

You receive the parent's message plus JSON data: calendar_events (next 7 days),
monday_items (open tasks), open_alerts (school alerts not yet handled),
family_notes (facts the family taught you — ground truth), pending_reminders.
Replies arrive with up to ~5 minutes of delay, so never promise real-time action.

You can BOTH answer and act. Respond with ONLY JSON:
{{"reply": "Telegram reply (concise, direct). Light HTML allowed: <b> for emphasis, <a href=\\"...\\"> for calendar/task links when the data provides one. NO other tags — especially no <br>; use real newline characters for line breaks. No markdown, never bare URLs. Escape literal & < > as &amp; &lt; &gt;",
  "actions": [
    {{"type": "create_task", "name": "...", "group": "Finance|House|Kids|Terrell To-Do", "due": "YYYY-MM-DD or null"}},
    {{"type": "create_event", "title": "...", "start": "YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD", "end": "... or null", "all_day": true/false, "location": null, "notes": null}},
    {{"type": "create_reminder", "text": "what to remind", "when": "YYYY-MM-DDTHH:MM:SS (local {tz} time)"}},
    {{"type": "mark_done", "alert_id": "..."}},
    {{"type": "remember", "text": "the fact to store, rewritten as a clear standalone sentence"}},
    {{"type": "forget", "memory_id": "..."}}
  ]}}

Rules:
- "actions" is usually empty — only act when the message clearly asks for it
  ("add...", "remind me/us...", "put ... on the calendar", "done with ...",
  "remember that...", "forget the one about...").
- IMPORTANT: create_task / create_event / create_reminder are NOT executed
  immediately — the system shows the parent a preview with Approve/Cancel
  buttons. So phrase your reply accordingly: "Proposed — approve below" not
  "Done" or "Added".
- mark_done / remember / forget DO execute immediately — confirm those plainly.
- "done"-style messages: find the matching open alert by meaning, return
  mark_done with its id, and confirm what you cleared. If nothing matches, say so.
- Reminder times: "tomorrow night" → 20:00 tomorrow; "in the morning" → 07:30;
  vague day with no time → 09:00.
- Questions ("what's Thursday look like?", "what's still open?"): answer from
  the data, short and scannable. Lead with what matters.
- If asked something the data can't answer, say what you don't have access to.
- Never invent events, tasks, alert ids, or memory ids."""


def answer_question(message_text: str, calendar_events: list[dict],
                    monday_items: list[dict], open_alerts: list[dict],
                    family_notes: list[dict] | None = None,
                    pending_reminders: list[dict] | None = None) -> dict:
    """Answer a free-text Telegram message. Returns {"reply": str, "actions": [...]}."""
    prompt = AMA_PROMPT.format(
        today=now_local().strftime("%A, %B %d, %Y, %I:%M %p"),
        tz=config.TIMEZONE,
        family_context=config.FAMILY_CONTEXT,
        tone_rules=TONE_RULES,
    )

    user_message = json.dumps({
        "message": message_text,
        "calendar_events": calendar_events,
        "monday_items": monday_items[:30],
        "open_alerts": open_alerts,
        "family_notes": family_notes or [],
        "pending_reminders": [
            {"text": r["text"], "when": r["when"]} for r in (pending_reminders or [])
        ],
    }, indent=2, default=str)

    text = _call_claude(config.BRIEF_MODEL, 1000, prompt, user_message)
    result = _json_from_text(text)
    if not result or "reply" not in result:
        return {"reply": "Sorry — I couldn't process that one. Try rephrasing.",
                "actions": []}
    result.setdefault("actions", [])
    return result
