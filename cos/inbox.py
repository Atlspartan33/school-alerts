"""
Telegram inbox: button presses, slash commands, and natural-language messages.

Safety model (mirrors the family ops spec):
  - Read/answer: immediate (questions, slash commands, briefing lookups)
  - Writes (task / calendar event / reminder): PROPOSED first — the bot sends
    a preview with Approve/Cancel buttons and only executes on approval
  - mark done / remember / forget: immediate (internal state, reversible)
  - Forbidden: sending email, deleting anything external, anything financial

Polled every ~5 minutes by inbox.py, so replies are near-real-time, not instant.
"""

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import config
from cos import delivery
from cos.intelligence import answer_question, parse_event_from_alert
from cos.state import (
    add_memory, add_proposal, add_reminder, forget_memory, get_alert,
    get_memories, get_proposal, get_recent_alerts, get_reminders,
    get_telegram_offset, mark_alert_done, pop_due_reminders, prune_proposals,
    resolve_proposal, set_telegram_offset,
)

log = logging.getLogger("family-cos")

HELP_TEXT = """Family Chief of Staff — what I can do:

/today — today's calendar + due tasks
/tomorrow — tomorrow's schedule + what to prep tonight
/week — the week ahead
/due — everything due or overdue
/unassigned — open tasks with no owner (coverage gaps)
/help — this message

Or just talk to me:
• "what does Thursday look like?"
• "add dentist for Tori Friday 2pm" (I'll preview before adding)
• "remind us tomorrow night to put the trash out"
• "done with the field trip form"
• "remember that Tori's teacher prefers email before 3pm"

I check messages every ~5 minutes, so replies aren't instant.
Anything that writes to the calendar or task board shows Approve/Cancel first."""

SLASH_QUESTIONS = {
    "/today": "What's on the calendar today, and what tasks are due today?",
    "/tomorrow": "What's on the calendar tomorrow, and what should be prepped or confirmed tonight?",
    "/week": "Give me the week ahead: calendar and tasks due, ordered by day.",
    "/due": "What tasks and school action items are due or overdue right now? Most urgent first.",
    "/unassigned": "Which open tasks have no owner? List them as coverage gaps that need someone to claim them.",
}


def alert_buttons(alert_id: str, has_calendar_suggestion: bool) -> list[list[dict]]:
    """Inline keyboard attached to every school alert."""
    row = []
    if has_calendar_suggestion:
        row.append({"text": "📅 Add to calendar", "callback_data": f"cal:{alert_id}"})
    row.append({"text": "➕ Make task", "callback_data": f"task:{alert_id}"})
    row.append({"text": "✅ Done", "callback_data": f"done:{alert_id}"})
    return [row]


def _approve_buttons(pid: str) -> list[list[dict]]:
    return [[
        {"text": "✅ Approve", "callback_data": f"ok:{pid}"},
        {"text": "❌ Cancel", "callback_data": f"no:{pid}"},
    ]]


def _describe(ptype: str, payload: dict) -> str:
    """Human-readable preview of a proposed action."""
    if ptype == "create_event":
        when = payload.get("start", "")
        if payload.get("all_day"):
            when = when[:10]
        loc = f" @ {payload['location']}" if payload.get("location") else ""
        return f'📅 Calendar event: "{payload.get("title")}" — {when}{loc}'
    if ptype == "create_task":
        due = f", due {payload['due']}" if payload.get("due") else ""
        return f'➕ Task: "{payload.get("name")}" ({payload.get("group") or config.DEFAULT_TASK_GROUP}{due})'
    if ptype == "create_reminder":
        return f'⏰ Reminder: "{payload.get("text")}" — {payload.get("when")}'
    return f"{ptype}: {payload}"


def _propose(state: dict, chat_id: str, ptype: str, payload: dict):
    """Store a proposal and send the preview with Approve/Cancel buttons."""
    pid = add_proposal(state, ptype, payload, chat_id)
    delivery.send_to_chat(
        chat_id,
        f"Proposed:\n{_describe(ptype, payload)}",
        buttons=_approve_buttons(pid),
    )


def _execute_proposal(proposal: dict, state: dict, services: dict) -> str:
    """Run an approved proposal. Returns a confirmation string."""
    ptype, payload = proposal["type"], proposal["payload"]

    if ptype == "create_event":
        from cos.actions import create_calendar_event
        return create_calendar_event(services["calendar"], payload)

    if ptype == "create_task":
        from cos.actions import create_monday_task
        return create_monday_task(payload["name"], payload.get("group"),
                                  payload.get("due"))

    if ptype == "create_reminder":
        when_local = datetime.fromisoformat(payload["when"])
        if when_local.tzinfo is None:
            when_local = when_local.replace(tzinfo=ZoneInfo(config.TIMEZONE))
        add_reminder(state, payload["text"], when_local.isoformat(),
                     proposal["chat_id"])
        return f'Reminder set: "{payload["text"]}" — {when_local.strftime("%a %b %d, %I:%M %p")}'

    raise RuntimeError(f"Unknown proposal type {ptype}")


def _handle_callback(cb: dict, state: dict, services: dict) -> None:
    """Handle one inline-button press."""
    data = cb.get("data", "")
    chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    cb_id = cb.get("id", "")

    if chat_id not in delivery.known_chat_ids():
        log.warning(f"Ignoring callback from unknown chat {chat_id}")
        delivery.answer_callback(cb_id)
        return

    action, _, ref = data.partition(":")

    try:
        # --- Proposal approval flow ---
        if action in ("ok", "no"):
            proposal = get_proposal(state, ref)
            if proposal is None or proposal.get("status") != "pending":
                delivery.answer_callback(cb_id, "That proposal expired or was already handled.")
                return
            if action == "no":
                resolve_proposal(state, ref, "cancelled")
                delivery.answer_callback(cb_id, "Cancelled")
                delivery.send_to_chat(chat_id, f"❌ Cancelled: {_describe(proposal['type'], proposal['payload'])}")
                return
            if delivery.DRY_RUN:
                delivery.send_to_chat(chat_id, f"[dry-run] would execute: {_describe(proposal['type'], proposal['payload'])}")
                return
            confirmation = _execute_proposal(proposal, state, services)
            resolve_proposal(state, ref, "executed")
            delivery.answer_callback(cb_id, "Done ✅")
            delivery.send_to_chat(chat_id, f"✅ {confirmation}")
            return

        # --- Alert buttons ---
        alert = get_alert(state, ref)
        if alert is None:
            delivery.answer_callback(cb_id, "That alert has expired (older than 7 days).")
            return

        if action == "done":
            mark_alert_done(state, ref)
            delivery.answer_callback(cb_id, "Cleared ✅")
            delivery.send_to_chat(chat_id, f'✅ Cleared: "{alert["headline"]}"')

        elif action == "task":
            payload = _task_payload_from_alert(alert)
            delivery.answer_callback(cb_id)
            _propose(state, chat_id, "create_task", payload)

        elif action == "cal":
            event = parse_event_from_alert(alert)
            delivery.answer_callback(cb_id)
            if event is None:
                delivery.send_to_chat(
                    chat_id,
                    f'Couldn\'t find a concrete date in "{alert["headline"]}" — '
                    "reply with the date and I'll propose it.",
                )
                return
            _propose(state, chat_id, "create_event", event)

        else:
            delivery.answer_callback(cb_id)

    except Exception as e:
        log.error(f"Callback {data} failed: {e}")
        delivery.answer_callback(cb_id)
        delivery.send_to_chat(chat_id, f"⚠️ That didn't work: {e}")


def _task_payload_from_alert(alert: dict) -> dict:
    name = alert.get("headline") or "School follow-up"
    child = alert.get("child", "")
    if child and child.lower() != "general" and child.lower() not in name.lower():
        name = f"{child}: {name}"
    due = None
    dates = alert.get("dates")
    if dates and len(str(dates)) >= 10:
        candidate = str(dates)[:10]
        if candidate[4:5] == "-" and candidate[7:8] == "-":
            due = candidate
    return {"name": name, "group": config.DEFAULT_TASK_GROUP, "due": due}


def _handle_message(msg: dict, state: dict, services: dict) -> None:
    """Handle one text message: slash command, done-reply, question, or request."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()

    if chat_id not in delivery.known_chat_ids():
        log.warning(f"Ignoring message from unknown chat {chat_id}")
        return
    if not text:
        return

    # --- Slash commands ---
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        if command in ("/help", "/start"):
            delivery.send_to_chat(chat_id, HELP_TEXT)
            return
        question = SLASH_QUESTIONS.get(command)
        if question is None:
            delivery.send_to_chat(chat_id, f"Unknown command {command}. Try /help.")
            return
        text = question  # fall through to AMA with the canned question

    # --- Gather fresh context ---
    calendar_events, monday_items = [], []
    try:
        from cos.sources.gcal import fetch_upcoming_events
        calendar_events = fetch_upcoming_events(services["calendar"])
    except Exception as e:
        log.warning(f"AMA: calendar unavailable: {e}")
    try:
        from cos.sources.monday import fetch_monday_items
        monday_items = fetch_monday_items()
    except Exception as e:
        log.warning(f"AMA: monday unavailable: {e}")

    open_alerts = [a for a in get_recent_alerts(state) if a.get("status") == "open"]

    result = answer_question(text, calendar_events, monday_items, open_alerts,
                             family_notes=get_memories(state),
                             pending_reminders=get_reminders(state))

    confirmations = []
    proposals = []
    for action in result.get("actions", []):
        try:
            kind = action.get("type")
            if kind in ("create_task", "create_event", "create_reminder"):
                payload = {k: v for k, v in action.items() if k != "type"}
                proposals.append((kind, payload))
            elif kind == "mark_done":
                if mark_alert_done(state, action.get("alert_id", "")):
                    confirmations.append("Marked done ✅")
            elif kind == "remember":
                add_memory(state, action.get("text", ""))
                confirmations.append(f'Noted: "{action.get("text", "")}"')
            elif kind == "forget":
                if forget_memory(state, action.get("memory_id", "")):
                    confirmations.append("Forgotten.")
        except Exception as e:
            log.error(f"AMA action {action} failed: {e}")
            confirmations.append(f"⚠️ Couldn't complete that: {e}")

    reply = result.get("reply", "")
    if confirmations:
        reply = (reply + "\n\n" + "\n".join(confirmations)).strip()
    if reply:
        delivery.send_to_chat(chat_id, reply, parse_mode="HTML")

    for kind, payload in proposals:
        _propose(state, chat_id, kind, payload)


def fire_due_reminders(state: dict) -> int:
    """Send any reminders whose time has arrived. Returns how many fired."""
    due = pop_due_reminders(state, datetime.now(timezone.utc))
    for r in due:
        delivery.send_to_chat(r.get("chat_id", ""), f"⏰ Reminder: {r['text']}")
    return len(due)


def process_updates(state: dict, services: dict) -> int:
    """Fetch and handle pending Telegram updates. Returns how many were handled."""
    prune_proposals(state)
    offset = get_telegram_offset(state)
    updates = delivery.get_updates(offset)

    handled = 0
    for update in updates:
        try:
            if "callback_query" in update:
                _handle_callback(update["callback_query"], state, services)
                handled += 1
            elif "message" in update:
                _handle_message(update["message"], state, services)
                handled += 1
        except Exception as e:
            log.error(f"Failed to handle update {update.get('update_id')}: {e}")
        set_telegram_offset(state, update["update_id"] + 1)

    return handled
