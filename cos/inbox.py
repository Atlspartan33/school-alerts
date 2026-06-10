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
from cos.intelligence import answer_question, parse_event_from_alert, revise_proposal
from cos.state import (
    add_memory, add_person_chat, add_proposal, add_reminder, expect_guest,
    forget_memory, get_alert, get_memories, get_proposal, get_recent_alerts,
    get_reminders, get_telegram_offset, mark_alert_done, pop_due_reminders,
    pop_expected_guest, pop_pending_edit, prune_proposals, resolve_proposal,
    set_pending_edit, set_telegram_offset,
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
• "add a dentist appointment Friday 2pm" (I'll preview before adding)
• "remind us tomorrow night to put the trash out"
• "done with the field trip form"
• "remember that swim class needs a towel every Thursday"

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
        {"text": "✏️ Edit", "callback_data": f"ed:{pid}"},
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

    if chat_id not in delivery.known_chat_ids(state):
        log.warning(f"Ignoring callback from unknown chat {chat_id}")
        delivery.answer_callback(cb_id)
        return

    action, _, ref = data.partition(":")

    try:
        # --- Proposal approval flow ---
        if action in ("ok", "no", "ed"):
            proposal = get_proposal(state, ref)
            if proposal is None or proposal.get("status") != "pending":
                delivery.answer_callback(cb_id, "That proposal expired or was already handled.")
                return
            if action == "ed":
                set_pending_edit(state, chat_id, ref)
                delivery.answer_callback(cb_id)
                delivery.send_to_chat(
                    chat_id,
                    "What should I change? (e.g. \"make it 3pm\", \"Saturday instead\", "
                    "\"assign it to the House group\")",
                )
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


def _apply_edit(state: dict, chat_id: str, pid: str, instruction: str):
    """Revise a pending proposal with the user's instruction and re-preview it."""
    proposal = get_proposal(state, pid)
    if proposal is None or proposal.get("status") != "pending":
        delivery.send_to_chat(chat_id, "That proposal expired — start over with a fresh request.")
        return
    if instruction.strip().lower() in ("cancel", "nevermind", "never mind", "forget it"):
        resolve_proposal(state, pid, "cancelled")
        delivery.send_to_chat(chat_id, f"❌ Cancelled: {_describe(proposal['type'], proposal['payload'])}")
        return

    revised = revise_proposal(proposal["type"], proposal["payload"], instruction)
    if revised is None:
        set_pending_edit(state, chat_id, pid)  # keep waiting for a usable instruction
        delivery.send_to_chat(
            chat_id,
            "Couldn't apply that to this action — try rephrasing (or say \"cancel\").",
        )
        return

    proposal["payload"] = revised
    delivery.send_to_chat(
        chat_id,
        f"Revised:\n{_describe(proposal['type'], revised)}",
        buttons=_approve_buttons(pid),
    )


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


def _welcome_guest(name: str, chat_id: str, state: dict, services: dict):
    """First-contact moment: greet by name and immediately show tomorrow."""
    delivery.send_to_chat(
        chat_id,
        f"Hi {name} 👋 I'm the family's chief of staff. I watch the school "
        "emails, the family calendar, and the to-do board so things don't "
        "slip through the cracks.\n\nHere's what I see coming up:",
    )
    # Live mini-brief — the wow is real data, zero effort
    _handle_message(
        {"chat": {"id": chat_id},
         "text": "Give a warm, scannable preview of tomorrow and the next few days: "
                 "calendar, anything due, anything the kids need. Keep it short."},
        state, services,
    )
    delivery.send_to_chat(
        chat_id,
        "You can just talk to me, any time:\n"
        "• \"what does Thursday look like?\"\n"
        "• \"add date night Friday 7pm to the calendar\"\n"
        "• \"remind us tomorrow night to pack the swim bag\"\n"
        "• \"put 'buy birthday gift' on the family list\"\n\n"
        "Anything I add, you approve with one tap first — I never change "
        "anything on my own. Try /help for the full list. Replies take a few "
        "minutes (I check messages every 5).",
    )
    # Tell the rest of the family
    delivery.send_telegram_plain(f"✅ {name} is onboarded — their briefs start tomorrow morning.")


def _handle_message(msg: dict, state: dict, services: dict) -> None:
    """Handle one text message: slash command, done-reply, question, or request."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()

    if chat_id not in delivery.known_chat_ids(state):
        guest_name = pop_expected_guest(state)
        if guest_name:
            add_person_chat(state, guest_name, chat_id)
            log.info(f"Onboarded {guest_name} at chat {chat_id}")
            _welcome_guest(guest_name, chat_id, state, services)
        else:
            log.warning(f"Ignoring message from unknown chat {chat_id}")
        return
    if not text:
        return

    # --- Pending edit: this message is the edit instruction ---
    if not text.startswith("/"):
        pending_pid = pop_pending_edit(state, chat_id)
        if pending_pid:
            _apply_edit(state, chat_id, pending_pid, text)
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
            elif kind == "expect_guest":
                expect_guest(state, action.get("name", "").strip() or "Guest")
        except Exception as e:
            log.error(f"AMA action {action} failed: {e}")
            confirmations.append(f"⚠️ Couldn't complete that: {e}")

    reply = result.get("reply", "")
    if confirmations:
        reply = (reply + "\n\n" + "\n".join(confirmations)).strip()
    if reply:
        # Telegram HTML has no <br>; the model occasionally emits one anyway
        reply = reply.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
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
